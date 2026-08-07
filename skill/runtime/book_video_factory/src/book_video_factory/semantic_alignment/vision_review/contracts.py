"""Frozen contracts and errors for real vision review evidence.

A ``VisionEvidence`` record is the proof that a multimodal model was actually
shown an image and asked whether it illustrates a specific caption. It is the
only artifact that can approve a NEW production image; a decision that merely
asserts a pass without this record cannot advance the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# The three verdicts a vision model may return, from best to worst alignment.
PARITY_VERDICTS: tuple[str, ...] = ("match", "rough", "mismatch")
PASSING_VERDICTS: frozenset[str] = frozenset({"match", "rough"})

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


@dataclass(frozen=True)
class ParityResult:
    """What a provider returns for one image/caption pair."""

    verdict: str
    reasoning: str
    call_id: str


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
    reviewed_pixels: bool = True
    legacy_pass: bool = False

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
            reviewed_pixels=bool(mapping.get("reviewed_pixels", True)),
            legacy_pass=bool(mapping.get("legacy_pass", False)),
        )

    def is_pass(self) -> bool:
        return self.parity_verdict in PASSING_VERDICTS

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
