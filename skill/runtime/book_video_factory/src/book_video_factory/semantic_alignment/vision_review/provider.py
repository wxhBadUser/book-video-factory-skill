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
import json
from pathlib import Path

from .contracts import (
    PARITY_VERDICTS,
    ParityResult,
    UntrustedProviderError,
    VisionProviderError,
    VisionReviewError,
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
    if candidate not in VISION_PROVIDER_ALLOWLIST:
        raise UntrustedProviderError(
            f"provider {candidate!r} is not on the vision reviewer allowlist "
            f"{VISION_PROVIDER_ALLOWLIST}"
        )
    return candidate


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
