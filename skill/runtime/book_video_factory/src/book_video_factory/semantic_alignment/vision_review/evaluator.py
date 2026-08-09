"""Orchestrates one vision review and returns bound, validated evidence.

``review_shot`` is the only sanctioned way to create a fresh ``VisionEvidence``.
It fails closed before it ever asks the model when the provider is untrusted,
the image is missing or empty, and after the model answers when the provider
returned no call id. The image hash is computed from the exact bytes that were
handed to the provider, so the evidence can never be bound to a different file
than the one that was reviewed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import (
    ParityResult,
    VisionEvidence,
    VisionProviderError,
    VisionReviewError,
)
from .provider import BaseVisionProvider, NullVisionProvider, validate_vision_provider


def _sha_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def review_shot(
    *,
    shot_id: str,
    image_path: str | Path,
    caption_text: str,
    prompt_text: str,
    provider: BaseVisionProvider,
) -> VisionEvidence:
    """Run a real vision review for one shot and return validated evidence."""

    if not str(shot_id).strip():
        raise VisionReviewError("vision review requires a shot id")
    # A missing provider is an honest blocked state, not an "untrusted" one: it
    # surfaces blocked_by_missing_vision_provider so the pipeline records the gap
    # rather than inventing an approval.
    if isinstance(provider, NullVisionProvider) or str(provider.name or "").strip() in {"", "none"}:
        raise VisionProviderError(
            f"shot {shot_id}: blocked_by_missing_vision_provider "
            "(no multimodal vision model is configured)"
        )
    # Fail closed before touching the model: an untrusted or image-generation
    # provider is rejected here, so a forbidden reviewer never runs.
    validate_vision_provider(provider.name)

    path = Path(image_path)
    if not path.exists():
        raise VisionReviewError(f"shot {shot_id}: image to review does not exist: {path}")
    image_bytes = path.read_bytes()
    if not image_bytes:
        raise VisionProviderError(f"shot {shot_id}: image file is empty, nothing to review")

    result = provider.review(
        image_bytes=image_bytes,
        caption_text=caption_text,
        prompt_text=prompt_text,
    )
    if not isinstance(result, ParityResult):  # defensive; base already checks
        raise VisionProviderError(f"shot {shot_id}: provider returned no ParityResult")
    if not str(result.call_id).strip():
        raise VisionReviewError(
            f"shot {shot_id}: vision provider returned no call id; refusing unverifiable evidence"
        )

    image_sha256 = _sha_bytes(image_bytes)
    caption_sha256 = _sha_text(caption_text)
    prompt_sha256 = _sha_text(prompt_text)
    provider_signature = provider._sign_evidence(
        image_sha256=image_sha256,
        caption_sha256=caption_sha256,
        prompt_sha256=prompt_sha256,
        verdict=result.verdict,
        reasoning=result.reasoning,
        call_id=str(result.call_id),
        reviewed_pixels=True,
    )
    evidence = VisionEvidence(
        shot_id=str(shot_id),
        vision_provider=provider.name,
        call_id=str(result.call_id),
        image_sha256=image_sha256,
        caption_sha256=caption_sha256,
        prompt_sha256=prompt_sha256,
        parity_verdict=result.verdict,
        parity_reasoning=result.reasoning,
        reviewed_pixels=True,
        legacy_pass=False,
        provider_signature=provider_signature,
    )
    evidence.verify()
    return evidence
