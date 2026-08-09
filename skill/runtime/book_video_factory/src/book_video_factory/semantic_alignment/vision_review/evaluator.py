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
import json
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.semantic_alignment.models import VisualProposition

from .contracts import (
    CurrentVisionEvidence,
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


def _current_review_semantic_inputs(
    *,
    caption_group: Mapping[str, Any],
    caption_texts: Mapping[str, str],
    proposition: VisualProposition,
    prompt_text: str,
) -> tuple[str, str, str, str, str, str]:
    """Normalize the exact semantic content shown to the current reviewer."""

    if not isinstance(caption_group, Mapping):
        raise VisionReviewError("current vision review requires a complete Caption Group artifact")
    group_payload = dict(caption_group)
    bound_group_sha = group_payload.pop("caption_group_sha256", None)
    derived_group_sha = _sha_text(
        json.dumps(group_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if bound_group_sha != derived_group_sha:
        raise VisionReviewError("current vision review Caption Group artifact SHA is stale")
    caption_ids = group_payload.get("caption_ids")
    if (
        not isinstance(caption_ids, list)
        or not caption_ids
        or any(not isinstance(value, str) or not value.strip() for value in caption_ids)
        or len(set(caption_ids)) != len(caption_ids)
    ):
        raise VisionReviewError("current vision review Caption Group IDs are invalid")
    if not isinstance(caption_texts, Mapping) or set(caption_texts) != set(caption_ids):
        raise VisionReviewError("current vision review Caption text set must exactly match the Group")
    normalized_captions: list[str] = []
    for caption_id in caption_ids:
        text = caption_texts.get(caption_id)
        if not isinstance(text, str) or not text.strip() or text != text.strip():
            raise VisionReviewError(f"current vision review Caption text is invalid: {caption_id}")
        normalized_captions.append(f"{caption_id}: {text}")
    if not isinstance(proposition, VisualProposition):
        raise VisionReviewError("current vision review requires a VisualProposition")
    proposition_text = json.dumps(
        proposition.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise VisionReviewError("current vision review requires the bound Prompt")
    return (
        "\n".join(normalized_captions),
        derived_group_sha,
        proposition_text,
        proposition.content_sha256(),
        prompt_text,
        _sha_text(prompt_text),
    )


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


def review_current_shot(
    *,
    shot_id: str,
    image_path: str | Path,
    caption_group: Mapping[str, Any],
    caption_texts: Mapping[str, str],
    proposition: VisualProposition,
    prompt_text: str,
    visible_persistent_character_ids: tuple[str, ...],
    identity_reference_paths: tuple[str | Path, ...],
    generation_provider: str,
    provider: BaseVisionProvider,
) -> CurrentVisionEvidence:
    """Review current scene and identity pixels and mint signed v2 evidence."""

    if not str(shot_id).strip():
        raise VisionReviewError("current vision review requires a shot id")
    if isinstance(provider, NullVisionProvider) or str(provider.name or "").strip() in {"", "none"}:
        raise VisionProviderError(
            f"shot {shot_id}: blocked_by_missing_vision_provider "
            "(no multimodal vision model is configured)"
        )
    validate_vision_provider(provider.name)
    if str(generation_provider).strip() == provider.name:
        raise VisionReviewError(
            f"shot {shot_id}: generation provider may not review its own image"
        )
    (
        caption_group_text,
        caption_group_sha256,
        proposition_text,
        proposition_sha256,
        prompt_text,
        prompt_sha256,
    ) = _current_review_semantic_inputs(
        caption_group=caption_group,
        caption_texts=caption_texts,
        proposition=proposition,
        prompt_text=prompt_text,
    )
    visible_ids = tuple(str(value) for value in visible_persistent_character_ids)
    reference_paths = tuple(Path(value) for value in identity_reference_paths)
    if len(visible_ids) != len(reference_paths):
        raise VisionReviewError(
            f"shot {shot_id}: every visible persistent character requires exactly one ordered identity reference"
        )

    image = Path(image_path)
    if image.is_symlink() or not image.is_file():
        raise VisionReviewError(f"shot {shot_id}: image to review does not exist or is symlinked: {image}")
    image_bytes = image.read_bytes()
    if not image_bytes:
        raise VisionProviderError(f"shot {shot_id}: image file is empty, nothing to review")
    identity_bytes: list[bytes] = []
    identity_hashes: list[str] = []
    for index, path in enumerate(reference_paths):
        if path.is_symlink() or not path.is_file():
            raise VisionReviewError(
                f"shot {shot_id}: identity reference {index} does not exist or is symlinked: {path}"
            )
        payload = path.read_bytes()
        if not payload:
            raise VisionProviderError(f"shot {shot_id}: identity reference {index} is empty")
        identity_bytes.append(payload)
        identity_hashes.append(_sha_bytes(payload))

    result = provider.review_current(
        image_bytes=image_bytes,
        caption_group_text=caption_group_text,
        proposition_text=proposition_text,
        prompt_text=prompt_text,
        identity_reference_bytes=tuple(identity_bytes),
    )
    if not result.vision_call_id.strip():
        raise VisionReviewError(
            f"shot {shot_id}: vision provider returned no call id; refusing unverifiable evidence"
        )
    signature_fields = {
        "provider": provider.name,
        "vision_call_id": result.vision_call_id,
        "generation_provider": str(generation_provider),
        "image_sha256": _sha_bytes(image_bytes),
        "caption_group_sha256": str(caption_group_sha256),
        "prompt_sha256": str(prompt_sha256),
        "proposition_sha256": str(proposition_sha256),
        "identity_anchor_sha256": tuple(identity_hashes),
        "visible_persistent_character_ids": visible_ids,
        "semantic_review_status": result.semantic_review_status,
        "semantic_review_reasoning": result.semantic_review_reasoning,
        "reality_review_status": result.reality_review_status,
        "reality_review_reasoning": result.reality_review_reasoning,
        "identity_review_status": result.identity_review_status,
        "identity_review_reasoning": result.identity_review_reasoning,
        "reviewed_pixels": True,
        "legacy_pass": False,
    }
    evidence = CurrentVisionEvidence(
        shot_id=str(shot_id),
        vision_provider=provider.name,
        provider_signature=provider._sign_current_evidence(**signature_fields),
        **{key: value for key, value in signature_fields.items() if key != "provider"},
    )
    evidence.verify()
    return evidence
