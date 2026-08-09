"""Frozen contracts and errors for real vision review evidence.

A ``VisionEvidence`` record is the proof that a multimodal model was actually
shown an image and asked whether it illustrates a specific caption. It is the
only artifact that can approve a NEW production image; a decision that merely
asserts a pass without this record cannot advance the pipeline.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# The three verdicts a vision model may return, from best to worst alignment.
PARITY_VERDICTS: tuple[str, ...] = ("match", "rough", "mismatch")
PASSING_VERDICTS: frozenset[str] = frozenset({"match", "rough"})
REVIEW_STATUSES: tuple[str, ...] = ("pass", "fail")
IDENTITY_REVIEW_STATUSES: tuple[str, ...] = (*REVIEW_STATUSES, "not_applicable")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# A real parity judgement names what it saw; a bare "ok" is not evidence.
MIN_REASONING_CHARS = 12


class VisionReviewError(RuntimeError):
    """A vision review record or decision is malformed, missing or not a pass."""


class MissingVisionEvidenceError(VisionReviewError):
    """A review decision advanced without the required vision evidence."""


class StaleVisionEvidenceError(VisionReviewError):
    """Stored vision evidence no longer matches the image, caption or prompt."""


class VisionProviderError(VisionReviewError):
    """The vision provider could not, or refused to, produce a real verdict."""


class UntrustedProviderError(VisionReviewError):
    """The named provider is not allowed to act as a vision reviewer."""


# --- Cryptographic binding between a vision review and the adapter that minted it.
#
# A ``VisionEvidence`` is no longer a JSON anyone can hand-author. It can only be
# produced by a ``BaseVisionProvider`` adapter that (a) actually received the
# image bytes and (b) holds a signing secret for its ``vision_provider`` name.
# The signature binds the provider identity to the exact image/caption/prompt
# hashes, verdict and reasoning, so a record cannot be forged, replayed under a
# different provider, or silently edited after the fact. ``verify`` fails closed
# when no key is registered for the named provider -- which is the honest state
# whenever a real, keyed vision adapter has not been wired in.
PROVIDER_VERIFICATION_KEYS: dict[str, str] = {}


def register_provider_key(provider: str, key: str) -> None:
    """Register the public verification key for a trusted vision reviewer.

    In production the key is the public half of the adapter's signing secret,
    loaded from the secrets backend. A test/CI adapter registers its own key so
    the deterministic path can be exercised without a live model.
    """

    PROVIDER_VERIFICATION_KEYS[str(provider)] = str(key)


def _hmac_sign(key: str, payload: str) -> str:
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _evidence_signature(
    *,
    provider: str,
    call_id: str,
    image_sha256: str,
    caption_sha256: str,
    prompt_sha256: str,
    parity_verdict: str,
    parity_reasoning: str,
    reviewed_pixels: bool,
    key: str,
) -> str:
    """Deterministic signature over the fields that define one review."""

    payload = "\n".join(
        [
            str(provider),
            str(call_id),
            str(image_sha256),
            str(caption_sha256),
            str(prompt_sha256),
            str(parity_verdict),
            str(parity_reasoning),
            "1" if reviewed_pixels else "0",
        ]
    )
    return _hmac_sign(key, payload)


def _current_evidence_signature(
    *,
    provider: str,
    vision_call_id: str,
    generation_provider: str,
    image_sha256: str,
    caption_group_sha256: str,
    prompt_sha256: str,
    proposition_sha256: str,
    identity_anchor_sha256: tuple[str, ...],
    visible_persistent_character_ids: tuple[str, ...],
    semantic_review_status: str,
    semantic_review_reasoning: str,
    reality_review_status: str,
    reality_review_reasoning: str,
    identity_review_status: str,
    identity_review_reasoning: str,
    reviewed_pixels: bool,
    legacy_pass: bool,
    key: str,
) -> str:
    payload = "\n".join(
        [
            str(provider),
            str(vision_call_id),
            str(generation_provider),
            str(image_sha256),
            str(caption_group_sha256),
            str(prompt_sha256),
            str(proposition_sha256),
            *[f"anchor:{value}" for value in identity_anchor_sha256],
            *[f"character:{value}" for value in visible_persistent_character_ids],
            str(semantic_review_status),
            str(semantic_review_reasoning),
            str(reality_review_status),
            str(reality_review_reasoning),
            str(identity_review_status),
            str(identity_review_reasoning),
            "1" if reviewed_pixels else "0",
            "1" if legacy_pass else "0",
        ]
    )
    return _hmac_sign(key, payload)


@dataclass(frozen=True)
class ParityResult:
    """What a provider returns for one image/caption pair."""

    verdict: str
    reasoning: str
    call_id: str


@dataclass(frozen=True)
class CurrentReviewResult:
    """Three independent findings returned after reviewing current pixels."""

    semantic_review_status: str
    semantic_review_reasoning: str
    reality_review_status: str
    reality_review_reasoning: str
    identity_review_status: str
    identity_review_reasoning: str
    vision_call_id: str


@dataclass(frozen=True)
class CurrentVisionEvidence:
    """Signed v2 evidence for one Caption Group image and all identity anchors."""

    shot_id: str
    vision_provider: str
    vision_call_id: str
    generation_provider: str
    image_sha256: str
    caption_group_sha256: str
    prompt_sha256: str
    proposition_sha256: str
    identity_anchor_sha256: tuple[str, ...]
    visible_persistent_character_ids: tuple[str, ...]
    semantic_review_status: str
    semantic_review_reasoning: str
    reality_review_status: str
    reality_review_reasoning: str
    identity_review_status: str
    identity_review_reasoning: str
    reviewed_pixels: bool = False
    legacy_pass: bool = False
    provider_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "vision_provider": self.vision_provider,
            "vision_call_id": self.vision_call_id,
            "generation_provider": self.generation_provider,
            "image_sha256": self.image_sha256,
            "caption_group_sha256": self.caption_group_sha256,
            "prompt_sha256": self.prompt_sha256,
            "proposition_sha256": self.proposition_sha256,
            "identity_anchor_sha256": list(self.identity_anchor_sha256),
            "visible_persistent_character_ids": list(self.visible_persistent_character_ids),
            "semantic_review_status": self.semantic_review_status,
            "semantic_review_reasoning": self.semantic_review_reasoning,
            "reality_review_status": self.reality_review_status,
            "reality_review_reasoning": self.reality_review_reasoning,
            "identity_review_status": self.identity_review_status,
            "identity_review_reasoning": self.identity_review_reasoning,
            "reviewed_pixels": self.reviewed_pixels,
            "legacy_pass": self.legacy_pass,
            "provider_signature": self.provider_signature,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "CurrentVisionEvidence":
        anchors = mapping.get("identity_anchor_sha256", ())
        visible = mapping.get("visible_persistent_character_ids", ())
        return cls(
            shot_id=str(mapping.get("shot_id", "")),
            vision_provider=str(mapping.get("vision_provider", "")),
            vision_call_id=str(mapping.get("vision_call_id", "")),
            generation_provider=str(mapping.get("generation_provider", "")),
            image_sha256=str(mapping.get("image_sha256", "")),
            caption_group_sha256=str(mapping.get("caption_group_sha256", "")),
            prompt_sha256=str(mapping.get("prompt_sha256", "")),
            proposition_sha256=str(mapping.get("proposition_sha256", "")),
            identity_anchor_sha256=tuple(str(value) for value in anchors) if isinstance(anchors, (list, tuple)) else (),
            visible_persistent_character_ids=tuple(str(value) for value in visible) if isinstance(visible, (list, tuple)) else (),
            semantic_review_status=str(mapping.get("semantic_review_status", "")),
            semantic_review_reasoning=str(mapping.get("semantic_review_reasoning", "")),
            reality_review_status=str(mapping.get("reality_review_status", "")),
            reality_review_reasoning=str(mapping.get("reality_review_reasoning", "")),
            identity_review_status=str(mapping.get("identity_review_status", "")),
            identity_review_reasoning=str(mapping.get("identity_review_reasoning", "")),
            reviewed_pixels=bool(mapping.get("reviewed_pixels", False)),
            legacy_pass=bool(mapping.get("legacy_pass", False)),
            provider_signature=str(mapping.get("provider_signature", "")),
        )

    def is_pass(self) -> bool:
        return (
            self.semantic_review_status == "pass"
            and self.reality_review_status == "pass"
            and self.identity_review_status in {"pass", "not_applicable"}
        )

    def validate(self) -> None:
        if not self.shot_id.strip():
            raise VisionReviewError("current vision evidence has no shot id")
        if not self.reviewed_pixels:
            raise VisionReviewError(f"shot {self.shot_id}: current review did not read image pixels")
        if self.legacy_pass:
            raise VisionReviewError(f"shot {self.shot_id}: legacy_pass is forbidden for current evidence")
        from .provider import validate_vision_provider

        validate_vision_provider(self.vision_provider)
        if not self.vision_call_id.strip():
            raise VisionReviewError(f"shot {self.shot_id}: current evidence has no vision_call_id")
        if not self.generation_provider.strip():
            raise VisionReviewError(f"shot {self.shot_id}: current evidence has no generation provider")
        if self.generation_provider == self.vision_provider:
            raise VisionReviewError(f"shot {self.shot_id}: generation provider may not review its own image")
        for label, value in (
            ("image_sha256", self.image_sha256),
            ("caption_group_sha256", self.caption_group_sha256),
            ("prompt_sha256", self.prompt_sha256),
            ("proposition_sha256", self.proposition_sha256),
        ):
            if not _HEX64.match(value or ""):
                raise VisionReviewError(f"shot {self.shot_id}: {label} is not a sha-256 digest")
        if len(self.identity_anchor_sha256) != len(self.visible_persistent_character_ids):
            raise VisionReviewError(
                f"shot {self.shot_id}: every visible persistent character requires exactly one ordered identity anchor"
            )
        if len(set(self.visible_persistent_character_ids)) != len(self.visible_persistent_character_ids):
            raise VisionReviewError(f"shot {self.shot_id}: visible persistent character IDs are duplicated")
        if any(not value.strip() for value in self.visible_persistent_character_ids):
            raise VisionReviewError(f"shot {self.shot_id}: visible persistent character IDs are invalid")
        if any(not _HEX64.match(value or "") for value in self.identity_anchor_sha256):
            raise VisionReviewError(f"shot {self.shot_id}: identity anchor SHA list is invalid")
        if self.semantic_review_status not in REVIEW_STATUSES:
            raise VisionReviewError(f"shot {self.shot_id}: semantic review status is invalid")
        if self.reality_review_status not in REVIEW_STATUSES:
            raise VisionReviewError(f"shot {self.shot_id}: reality review status is invalid")
        if self.identity_review_status not in IDENTITY_REVIEW_STATUSES:
            raise VisionReviewError(f"shot {self.shot_id}: identity review status is invalid")
        has_characters = bool(self.visible_persistent_character_ids)
        if has_characters == (self.identity_review_status == "not_applicable"):
            raise VisionReviewError(
                f"shot {self.shot_id}: identity not_applicable is allowed only with no visible persistent characters"
            )
        for label, value in (
            ("semantic", self.semantic_review_reasoning),
            ("reality", self.reality_review_reasoning),
            ("identity", self.identity_review_reasoning),
        ):
            if len(value.strip()) < MIN_REASONING_CHARS:
                raise VisionReviewError(f"shot {self.shot_id}: {label} review reasoning is too thin")

    def verify(self) -> None:
        self.validate()
        key = PROVIDER_VERIFICATION_KEYS.get(self.vision_provider)
        if key is None:
            raise VisionReviewError(
                f"shot {self.shot_id}: no verification key is registered for provider {self.vision_provider!r}"
            )
        expected = _current_evidence_signature(
            provider=self.vision_provider,
            vision_call_id=self.vision_call_id,
            generation_provider=self.generation_provider,
            image_sha256=self.image_sha256,
            caption_group_sha256=self.caption_group_sha256,
            prompt_sha256=self.prompt_sha256,
            proposition_sha256=self.proposition_sha256,
            identity_anchor_sha256=self.identity_anchor_sha256,
            visible_persistent_character_ids=self.visible_persistent_character_ids,
            semantic_review_status=self.semantic_review_status,
            semantic_review_reasoning=self.semantic_review_reasoning,
            reality_review_status=self.reality_review_status,
            reality_review_reasoning=self.reality_review_reasoning,
            identity_review_status=self.identity_review_status,
            identity_review_reasoning=self.identity_review_reasoning,
            reviewed_pixels=self.reviewed_pixels,
            legacy_pass=self.legacy_pass,
            key=key,
        )
        if not hmac.compare_digest(self.provider_signature, expected):
            raise VisionReviewError(f"shot {self.shot_id}: current vision evidence signature is invalid")


@dataclass(frozen=True)
class VisionEvidence:
    """Proof that a vision model reviewed one image against one caption."""

    shot_id: str
    vision_provider: str
    call_id: str
    image_sha256: str
    caption_sha256: str
    prompt_sha256: str
    parity_verdict: str
    parity_reasoning: str
    reviewed_pixels: bool = False
    legacy_pass: bool = False
    provider_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "vision_provider": self.vision_provider,
            "call_id": self.call_id,
            "image_sha256": self.image_sha256,
            "caption_sha256": self.caption_sha256,
            "prompt_sha256": self.prompt_sha256,
            "parity_verdict": self.parity_verdict,
            "parity_reasoning": self.parity_reasoning,
            "reviewed_pixels": self.reviewed_pixels,
            "legacy_pass": self.legacy_pass,
            "provider_signature": self.provider_signature,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "VisionEvidence":
        return cls(
            shot_id=str(mapping.get("shot_id", "")),
            vision_provider=str(mapping.get("vision_provider", "")),
            call_id=str(mapping.get("call_id", "")),
            image_sha256=str(mapping.get("image_sha256", "")),
            caption_sha256=str(mapping.get("caption_sha256", "")),
            prompt_sha256=str(mapping.get("prompt_sha256", "")),
            parity_verdict=str(mapping.get("parity_verdict", "")),
            parity_reasoning=str(mapping.get("parity_reasoning", "")),
            reviewed_pixels=bool(mapping.get("reviewed_pixels", False)),
            legacy_pass=bool(mapping.get("legacy_pass", False)),
            provider_signature=str(mapping.get("provider_signature", "")),
        )

    def is_pass(self) -> bool:
        return self.parity_verdict in PASSING_VERDICTS

    def verify(self) -> None:
        """Fail closed unless the record is cryptographically bound to a keyed adapter.

        Structural ``validate`` only checks shape. ``verify`` additionally proves
        the record was minted by a ``BaseVisionProvider`` that held the signing
        secret for ``vision_provider`` and processed exactly these pixels,
        caption and prompt. A hand-authored dict (even one with valid-looking
        fields) has no valid signature and is rejected -- this is what closes the
        "any JSON can claim Claude saw it" gap.
        """

        self.validate()
        key = PROVIDER_VERIFICATION_KEYS.get(self.vision_provider)
        if key is None:
            raise VisionReviewError(
                f"shot {self.shot_id}: no verification key is registered for provider "
                f"{self.vision_provider!r}; the vision evidence was not minted by a "
                f"trusted, keyed vision adapter"
            )
        expected = _evidence_signature(
            provider=self.vision_provider,
            call_id=self.call_id,
            image_sha256=self.image_sha256,
            caption_sha256=self.caption_sha256,
            prompt_sha256=self.prompt_sha256,
            parity_verdict=self.parity_verdict,
            parity_reasoning=self.parity_reasoning,
            reviewed_pixels=self.reviewed_pixels,
            key=key,
        )
        if not hmac.compare_digest(self.provider_signature, expected):
            raise VisionReviewError(
                f"shot {self.shot_id}: vision evidence signature is invalid; it was "
                f"not produced by {self.vision_provider!r} reviewing these exact "
                f"pixels/caption/prompt (forged or tampered evidence)"
            )

    def validate(self) -> None:
        """Fail closed on any structural defect.

        A review that did not read pixels, that carries a malformed hash, that
        reports an unknown verdict, or whose reasoning is too thin to be a real
        judgement is not evidence and must not approve anything.
        """

        if not self.reviewed_pixels:
            raise VisionReviewError(
                f"shot {self.shot_id}: a review that did not read the image pixels is not evidence"
            )
        # The provider that reviewed the pixels must be a trusted multimodal
        # vision model. This used to be checked only by unit tests; it is now
        # enforced here so a text-only or image-generating provider cannot
        # masquerade as a vision reviewer in production.
        from .provider import validate_vision_provider
        validate_vision_provider(self.vision_provider)
        if not self.vision_provider.strip():
            raise VisionReviewError(f"shot {self.shot_id}: vision evidence has no provider")
        if not self.call_id.strip():
            raise VisionReviewError(f"shot {self.shot_id}: vision evidence has no provider call id")
        for label, value in (
            ("image_sha256", self.image_sha256),
            ("caption_sha256", self.caption_sha256),
            ("prompt_sha256", self.prompt_sha256),
        ):
            if not _HEX64.match(value or ""):
                raise VisionReviewError(f"shot {self.shot_id}: {label} is not a sha-256 digest")
        if self.parity_verdict not in PARITY_VERDICTS:
            raise VisionReviewError(
                f"shot {self.shot_id}: unknown parity verdict {self.parity_verdict!r}"
            )
        if len(self.parity_reasoning.strip()) < MIN_REASONING_CHARS:
            raise VisionReviewError(
                f"shot {self.shot_id}: parity reasoning is too thin to be a real judgement"
            )
