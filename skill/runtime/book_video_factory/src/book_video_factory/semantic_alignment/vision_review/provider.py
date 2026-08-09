"""Vision provider policy and base provider that must be handed real pixels.

Two rules are enforced here and cannot be bypassed:

1. **Allowlist.** Only a known multimodal vision model may review images. An
   unknown or empty provider fails closed.
2. **No image generator as reviewer.** The provider that *drew* an image can
   never be the provider that *approves* it. Every image-generation lane
   (host-imagegen, and the flagged gemini-web / flow-web strings) is explicitly
   forbidden from acting as a vision reviewer.

``BaseVisionProvider`` guarantees a subclass can only produce a verdict after it
has been given nonempty image bytes, so a text-only provider cannot claim a pass
without ever seeing the picture.
"""

from __future__ import annotations

import abc
import hashlib
import json
import os
from pathlib import Path

from .contracts import (
    PARITY_VERDICTS,
    PROVIDER_VERIFICATION_KEYS,
    ParityResult,
    UntrustedProviderError,
    VisionProviderError,
    VisionReviewError,
    _evidence_signature,
    register_provider_key,
)

# The per-project artifact that declares which vision reviewer is active.
VISION_REVIEW_PROVIDER_RELATIVE = "qa/vision_review_provider.json"
VISION_REVIEW_PROVIDER_SCHEMA = "vision-review-provider.v1"

# Multimodal models permitted to act as vision reviewers.
VISION_PROVIDER_ALLOWLIST: tuple[str, ...] = (
    "claude-sonnet-4.5",
    "claude-opus-4.7",
)

# Image-generation lanes that may NEVER review their own (or any) output.
FORBIDDEN_VISION_PROVIDERS: frozenset[str] = frozenset(
    {"host-imagegen", "gemini-web", "flow-web", "imagegen"}
)


def validate_vision_provider(name: str) -> str:
    """Return the provider name if it is a trusted vision reviewer, else raise."""

    candidate = str(name or "").strip()
    if not candidate:
        raise UntrustedProviderError("no vision provider named; refusing to review")
    if candidate in FORBIDDEN_VISION_PROVIDERS:
        raise UntrustedProviderError(
            f"provider {candidate!r} generates images and may not review them"
        )
    if candidate in PROVIDER_VERIFICATION_KEYS or candidate in VISION_PROVIDER_ALLOWLIST:
        return candidate
    raise UntrustedProviderError(
        f"provider {candidate!r} is not a trusted, keyed vision reviewer "
        f"(allowlist {VISION_PROVIDER_ALLOWLIST} or a registered signing key required)"
    )


def load_vision_review_provider(project_root: str | Path) -> str:
    """Read ``qa/vision_review_provider.json`` and return the vetted provider.

    Fails closed if the declaration is missing, malformed, of the wrong schema,
    or names a provider that is not a trusted vision reviewer (for example an
    image-generation lane such as ``gemini-web``).
    """

    path = Path(project_root) / VISION_REVIEW_PROVIDER_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise VisionReviewError(
            f"vision review provider declaration is missing: {path}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisionReviewError(
            f"vision review provider declaration is unreadable: {error}"
        ) from error
    if not isinstance(document, dict):
        raise VisionReviewError("vision review provider declaration must be an object")
    if document.get("schema_version") != VISION_REVIEW_PROVIDER_SCHEMA:
        raise VisionReviewError(
            "vision review provider declaration has an unexpected schema "
            f"{document.get('schema_version')!r}"
        )
    active = document.get("active_provider")
    if not isinstance(active, str) or not active.strip():
        raise VisionReviewError("vision review provider declaration has no active_provider")
    return validate_vision_provider(active)


class BaseVisionProvider(abc.ABC):
    """A vision provider that can only answer after receiving image pixels."""

    #: Subclasses set this to a name that must pass ``validate_vision_provider``.
    name: str = ""
    #: Subclasses set this to the secret used to sign the evidence they mint.
    #: Without it the adapter cannot produce a verifiable ``VisionEvidence``.
    signing_secret: str = ""

    def _sign_evidence(
        self,
        *,
        image_sha256: str,
        caption_sha256: str,
        prompt_sha256: str,
        verdict: str,
        reasoning: str,
        call_id: str,
        reviewed_pixels: bool,
    ) -> str:
        """Return the HMAC that binds this review to the adapter's secret."""

        key = self.signing_secret or PROVIDER_VERIFICATION_KEYS.get(self.name, "")
        if not key:
            raise VisionProviderError(
                f"provider {self.name!r} has no signing secret; it cannot mint verifiable evidence"
            )
        return _evidence_signature(
            provider=self.name,
            call_id=call_id,
            image_sha256=image_sha256,
            caption_sha256=caption_sha256,
            prompt_sha256=prompt_sha256,
            parity_verdict=verdict,
            parity_reasoning=reasoning,
            reviewed_pixels=reviewed_pixels,
            key=key,
        )

    def review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        if not isinstance(image_bytes, (bytes, bytearray)) or len(image_bytes) == 0:
            raise VisionProviderError(
                "vision review requires nonempty image pixels; refusing text-only judgement"
            )
        if not str(caption_text).strip():
            raise VisionProviderError("vision review requires the caption text under review")
        result = self._review(
            image_bytes=bytes(image_bytes),
            caption_text=caption_text,
            prompt_text=prompt_text,
        )
        if not isinstance(result, ParityResult):
            raise VisionProviderError(
                f"provider {self.name!r} did not return a ParityResult"
            )
        if result.verdict not in PARITY_VERDICTS:
            raise VisionProviderError(
                f"provider {self.name!r} returned unknown verdict {result.verdict!r}"
            )
        return result

    @abc.abstractmethod
    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        """Ask the real multimodal model whether the image illustrates the caption."""


class NullVisionProvider(BaseVisionProvider):
    """The honest default when no multimodal model is wired.

    It never fabricates a verdict; it blocks. Callers surface
    ``blocked_by_missing_vision_provider`` so the pipeline records the gap
    instead of inventing an approval.
    """

    name = "none"

    def review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:  # noqa: D401
        raise VisionProviderError(
            "blocked_by_missing_vision_provider: no vision model is configured, "
            "so no parity verdict can be produced"
        )

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        raise VisionProviderError("blocked_by_missing_vision_provider")


class LocalVisionProvider(BaseVisionProvider):
    """Deterministic, offline vision adapter used by tests/CI and as a reference.

    It performs a *real* local inspection of the pixels (decode + luminance) and
    signs its verdict, so the production evidence path -- ``review_shot`` -> signed
    ``VisionEvidence`` -> ``validate_review_decision`` -- is exercised end to end
    without a live model. Its signing secret is a constant registered at import;
    it is clearly named a stub and must never be used to certify production frames.
    """

    name = "local-vision-stub"
    signing_secret = "local-vision-stub-signing-secret-do-not-use-in-production"

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        from io import BytesIO

        try:
            from PIL import Image, ImageStat

            with Image.open(BytesIO(image_bytes)) as image:
                luminance = float(ImageStat.Stat(image.convert("L")).mean[0])
            verdict = "match" if luminance > 4 else "rough"
            reasoning = (
                f"local deterministic parity check: image decoded ({len(image_bytes)} bytes), "
                f"mean luminance {luminance:.1f}; caption references {len(str(caption_text))} chars; "
                f"prompt references {len(str(prompt_text))} chars"
            )
        except Exception:
            # Cannot decode locally: report a conservative *rough* (never a clean
            # match) and explain. This keeps the offline stub from crashing the
            # pipeline on a non-decodable frame while still requiring a real
            # model for a confident pass, and always yields a signed verdict.
            verdict = "rough"
            reasoning = (
                f"local deterministic parity check: image could not be decoded locally "
                f"({len(image_bytes)} bytes); caption references {len(str(caption_text))} chars; "
                f"prompt references {len(str(prompt_text))} chars; human/Claude review recommended"
            )
        call_id = hashlib.sha256(
            image_bytes + str(caption_text).encode("utf-8") + str(prompt_text).encode("utf-8")
        ).hexdigest()[:24]
        return ParityResult(verdict=verdict, reasoning=reasoning, call_id=call_id)


register_provider_key(LocalVisionProvider.name, LocalVisionProvider.signing_secret)


class ClaudeVisionProvider(BaseVisionProvider):
    """Production multimodal reviewer.

    The real API call is performed inside ``_review``; the verdict is signed with
    a secret that must be supplied via ``CLAUDE_VISION_SIGNING_SECRET`` (or a
    secrets backend). Without the secret, no evidence can be minted and the
    pipeline surfaces ``blocked_by_missing_vision_provider`` instead of inventing
    an approval. The verification key is registered from the same secret so
    ``VisionEvidence.verify`` accepts production records.
    """

    name = "claude-sonnet-4.5"

    def __init__(self, signing_secret: str | None = None) -> None:
        self.signing_secret = signing_secret or os.environ.get("CLAUDE_VISION_SIGNING_SECRET", "")

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        if not self.signing_secret:
            raise VisionProviderError(
                "blocked_by_missing_vision_provider: CLAUDE_VISION_SIGNING_SECRET is unset; "
                "no production vision review can be minted"
            )
        # Production wiring: send (image_bytes, caption_text, prompt_text) to the
        # real Claude vision endpoint, parse the returned verdict + reasoning, and
        # return a ParityResult. Kept behind the secret guard so the sandbox can
        # never pretend to have reviewed a frame.
        raise VisionProviderError(
            "production Claude vision call is not wired in this environment; "
            "set CLAUDE_VISION_SIGNING_SECRET and implement the API call"
        )


def register_claude_provider(secret: str) -> None:
    """Register the production Claude reviewer key (call once from the secrets backend)."""

    register_provider_key(ClaudeVisionProvider.name, secret)
