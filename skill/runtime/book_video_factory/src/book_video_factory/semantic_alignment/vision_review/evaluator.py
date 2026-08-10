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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.semantic_alignment.models import VisualProposition

from .contracts import (
    CurrentVisionEvidence,
    ParityResult,
    VisionEvidence,
    VisionProviderError,
    VisionReviewError,
    caption_text_set_sha256 as compute_caption_text_set_sha256,
)
from .provider import BaseVisionProvider, NullVisionProvider, validate_vision_provider


def _sha_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _CurrentReviewInputs:
    """The exact, normalized semantic payload one current review was built on."""

    group_id: str
    caption_ids: tuple[str, ...]
    caption_group_text: str
    caption_group_sha256: str
    caption_text_set_sha256: str
    proposition_text: str
    proposition_sha256: str
    prompt_text: str
    prompt_sha256: str


def _current_review_semantic_inputs(
    *,
    caption_group: Mapping[str, Any],
    caption_texts: Mapping[str, str],
    proposition: VisualProposition,
    prompt_text: str,
) -> _CurrentReviewInputs:
    """Normalize the exact semantic content shown to the current reviewer.

    Two different caption serializations come out of here and they are not
    interchangeable:

    * ``caption_group_text`` is the human-readable ``"<id>: <text>"`` block that
      is literally handed to the vision model.
    * ``caption_text_set_sha256`` is the SHA-256 over the ordered caption prose
      joined exactly the way the Director joined it when it produced
      ``IMAGE_TASK.prompt_binding.caption_text_sha256``.

    Both are derived from the *same* ordered ``caption_texts``, so the second
    one proves what the first one contained. The reviewer never accepts a
    caller-supplied caption hash.
    """

    if not isinstance(caption_group, Mapping):
        raise VisionReviewError("current vision review requires a complete Caption Group artifact")
    group_payload = dict(caption_group)
    bound_group_sha = group_payload.pop("caption_group_sha256", None)
    derived_group_sha = _sha_text(
        json.dumps(group_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if bound_group_sha != derived_group_sha:
        raise VisionReviewError("current vision review Caption Group artifact SHA is stale")
    group_id = group_payload.get("group_id")
    if not isinstance(group_id, str) or not group_id.strip():
        raise VisionReviewError("current vision review Caption Group has no group id")
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
    return _CurrentReviewInputs(
        group_id=str(group_id),
        caption_ids=tuple(str(value) for value in caption_ids),
        caption_group_text="\n".join(normalized_captions),
        caption_group_sha256=derived_group_sha,
        # Recomputed here from the very texts that were just serialized into
        # ``caption_group_text``; never read from a caller-supplied field.
        caption_text_set_sha256=compute_caption_text_set_sha256(caption_ids, caption_texts),
        proposition_text=proposition_text,
        proposition_sha256=proposition.content_sha256(),
        prompt_text=prompt_text,
        prompt_sha256=_sha_text(prompt_text),
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


def _verify_reviewed_captions_against_prompt_binding(
    *,
    shot_id: str,
    task_id: str,
    inputs: _CurrentReviewInputs,
    prompt_binding: Mapping[str, Any],
) -> None:
    """Prove the captions actually under review are the ones the Prompt bound.

    This is the hinge of the caption<->image contract. ``inputs`` was rebuilt
    from the live Caption Group and the live caption prose that is about to be
    handed to the vision model; ``prompt_binding`` is what the Director recorded
    on the IMAGE_TASK when it wrote the prompt that produced the image. If the
    reviewer is holding different prose, a different order, or a different
    group, the two hashes diverge and no evidence is minted at all.

    Note what is deliberately *not* possible here: the caption hash is never
    read out of ``prompt_binding`` and copied into the evidence. It is always
    recomputed from real text, and ``prompt_binding`` is only ever the
    expectation it is compared against.
    """

    if not isinstance(prompt_binding, Mapping):
        raise VisionReviewError(
            f"shot {shot_id}: current vision review requires the IMAGE_TASK prompt binding"
        )
    if not str(task_id).strip():
        raise VisionReviewError("current vision review requires the IMAGE_TASK id")

    bound_caption_ids = prompt_binding.get("caption_ids")
    if not isinstance(bound_caption_ids, (list, tuple)) or tuple(
        str(value) for value in bound_caption_ids
    ) != inputs.caption_ids:
        raise VisionReviewError(
            f"shot {shot_id}: reviewed caption order {list(inputs.caption_ids)} does not match "
            f"the prompt binding caption order {list(bound_caption_ids or [])}"
        )

    bound_group_id = str(prompt_binding.get("group_id", ""))
    if bound_group_id != inputs.group_id:
        raise VisionReviewError(
            f"shot {shot_id}: reviewed Caption Group {inputs.group_id!r} does not match the "
            f"prompt binding group {bound_group_id!r}"
        )

    bound_group_sha = str(prompt_binding.get("caption_group_sha256", ""))
    if bound_group_sha != inputs.caption_group_sha256:
        raise VisionReviewError(
            f"shot {shot_id}: reviewed Caption Group SHA does not match the prompt binding "
            f"(group artifact drifted since the image was prompted)"
        )

    bound_caption_text_sha = str(prompt_binding.get("caption_text_sha256", ""))
    if bound_caption_text_sha != inputs.caption_text_set_sha256:
        raise VisionReviewError(
            f"shot {shot_id}: the caption text actually under review hashes to "
            f"{inputs.caption_text_set_sha256} but the prompt that produced this image was "
            f"bound to caption text {bound_caption_text_sha}; the reviewer is not looking at "
            f"the captions this image was drawn for"
        )

    bound_prompt_sha = str(prompt_binding.get("prompt_sha256", ""))
    if bound_prompt_sha != inputs.prompt_sha256:
        raise VisionReviewError(
            f"shot {shot_id}: reviewed Prompt does not match the prompt binding"
        )

    bound_proposition_sha = str(prompt_binding.get("proposition_sha256", ""))
    if bound_proposition_sha != inputs.proposition_sha256:
        raise VisionReviewError(
            f"shot {shot_id}: reviewed Visual Proposition does not match the prompt binding"
        )


def review_current_shot(
    *,
    shot_id: str,
    task_id: str,
    image_path: str | Path,
    caption_group: Mapping[str, Any],
    caption_texts: Mapping[str, str],
    proposition: VisualProposition,
    prompt_text: str,
    prompt_binding: Mapping[str, Any],
    visible_persistent_character_ids: tuple[str, ...],
    identity_reference_paths: tuple[str | Path, ...],
    generation_provider: str,
    provider: BaseVisionProvider,
) -> CurrentVisionEvidence:
    """Review current scene and identity pixels and mint signed v2 evidence.

    ``prompt_binding`` is the IMAGE_TASK's own record of what the image was
    drawn for. It is an expectation, not an input: every hash in the returned
    evidence is recomputed here from live artifacts and live pixels, then
    checked against the binding before the model is ever called.
    """

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
    inputs = _current_review_semantic_inputs(
        caption_group=caption_group,
        caption_texts=caption_texts,
        proposition=proposition,
        prompt_text=prompt_text,
    )
    # Fail closed before spending a model call: if the captions in hand are not
    # the captions this image was prompted for, there is nothing worth reviewing.
    _verify_reviewed_captions_against_prompt_binding(
        shot_id=str(shot_id),
        task_id=str(task_id),
        inputs=inputs,
        prompt_binding=prompt_binding,
    )
    caption_group_text = inputs.caption_group_text
    caption_group_sha256 = inputs.caption_group_sha256
    proposition_text = inputs.proposition_text
    proposition_sha256 = inputs.proposition_sha256
    prompt_text = inputs.prompt_text
    prompt_sha256 = inputs.prompt_sha256
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
        "task_id": str(task_id),
        "group_id": inputs.group_id,
        "caption_ids": inputs.caption_ids,
        "caption_text_set_sha256": inputs.caption_text_set_sha256,
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
