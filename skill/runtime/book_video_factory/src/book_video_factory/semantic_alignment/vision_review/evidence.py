"""Persistence, staleness and decision-gate helpers for vision evidence.

* ``build_vision_evidence_document`` / ``load_vision_evidence_document`` move a
  set of ``VisionEvidence`` records in and out of the ``vision-evidence.v1``
  artifact.
* ``verify_evidence_current`` is the staleness guard: it recomputes the image,
  caption and prompt hashes and fails closed if any of them drifted from the
  stored evidence, so an edited caption or a swapped image can never be served
  by a stale approval.
* ``validate_review_decision`` is the gate used by the per-shot and encoded
  reviews: evidence present -> authoritative; missing (including a
  ``legacy_pass`` marker) -> refused to advance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    CurrentVisionEvidence,
    MissingVisionEvidenceError,
    StaleVisionEvidenceError,
    VisionEvidence,
    VisionReviewError,
)

SCHEMA_VERSION = "vision-evidence.v1"
CURRENT_SCHEMA_VERSION = "vision-evidence.v2"


def _sha_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_vision_evidence_document(
    release_id: str, evidences: Iterable[VisionEvidence]
) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for evidence in evidences:
        evidence.validate()
        reviews[evidence.shot_id] = evidence.to_dict()
    return {
        "schema_version": SCHEMA_VERSION,
        "release_id": str(release_id),
        "reviews": reviews,
    }


def load_vision_evidence_document(document: Mapping[str, Any]) -> dict[str, VisionEvidence]:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise VisionReviewError(
            f"vision evidence document has unexpected schema {document.get('schema_version')!r}"
        )
    reviews = document.get("reviews")
    if not isinstance(reviews, Mapping):
        raise VisionReviewError("vision evidence document has no reviews map")
    loaded: dict[str, VisionEvidence] = {}
    for shot_id, payload in reviews.items():
        if not isinstance(payload, Mapping):
            raise VisionReviewError(f"vision evidence for {shot_id!r} is not a mapping")
        evidence = VisionEvidence.from_mapping({**payload, "shot_id": shot_id})
        # Structural validate is not enough: a stored document must also prove its
        # records were cryptographically minted by a keyed vision adapter, so a
        # forged evidence document fails closed at load time (not just at the gate).
        evidence.verify()
        loaded[str(shot_id)] = evidence
    return loaded


def build_current_vision_evidence_document(
    release_id: str, evidences: Iterable[CurrentVisionEvidence]
) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for evidence in evidences:
        evidence.verify()
        if evidence.shot_id in reviews:
            raise VisionReviewError(f"duplicate current vision evidence for shot {evidence.shot_id}")
        reviews[evidence.shot_id] = evidence.to_dict()
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "release_id": str(release_id),
        "reviews": reviews,
    }


def load_current_vision_evidence_document(
    document: Mapping[str, Any],
    *,
    current_bindings: Mapping[str, Mapping[str, Any]],
) -> dict[str, CurrentVisionEvidence]:
    if document.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise VisionReviewError(
            f"current vision evidence document has unexpected schema {document.get('schema_version')!r}"
        )
    reviews = document.get("reviews")
    if not isinstance(reviews, Mapping):
        raise VisionReviewError("current vision evidence document has no reviews map")
    if not isinstance(current_bindings, Mapping) or set(map(str, current_bindings)) != set(map(str, reviews)):
        raise VisionReviewError(
            "current vision evidence load requires exactly one current artifact binding per review"
        )
    loaded: dict[str, CurrentVisionEvidence] = {}
    for shot_id, payload in reviews.items():
        if not isinstance(payload, Mapping):
            raise VisionReviewError(f"current vision evidence for {shot_id!r} is not a mapping")
        evidence = CurrentVisionEvidence.from_mapping({**payload, "shot_id": shot_id})
        evidence.verify()
        binding = current_bindings.get(str(shot_id))
        if not isinstance(binding, Mapping):
            raise VisionReviewError(f"current artifact binding for {shot_id!r} is not a mapping")
        try:
            verify_current_evidence(evidence, **dict(binding))
        except TypeError as error:
            raise VisionReviewError(
                f"current artifact binding for {shot_id!r} is incomplete or has unknown fields"
            ) from error
        loaded[str(shot_id)] = evidence
    return loaded


def verify_current_evidence(
    evidence: CurrentVisionEvidence,
    *,
    image_path: str | Path,
    caption_group_sha256: str,
    prompt_sha256: str,
    proposition_sha256: str,
    visible_persistent_character_ids: tuple[str, ...],
    identity_reference_paths: tuple[str | Path, ...],
    generation_provider: str,
    require_pass: bool = False,
) -> None:
    """Pure fail-closed validator for Render integration and later manifest loads."""

    evidence.verify()
    image = Path(image_path)
    if image.is_symlink() or not image.is_file():
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: reviewed image is gone or symlinked: {image}"
        )
    if _sha_file(image) != evidence.image_sha256:
        raise StaleVisionEvidenceError(f"shot {evidence.shot_id}: image changed since current review")
    for label, actual, expected in (
        ("Caption Group", str(caption_group_sha256), evidence.caption_group_sha256),
        ("Prompt", str(prompt_sha256), evidence.prompt_sha256),
        ("Proposition", str(proposition_sha256), evidence.proposition_sha256),
        ("generation provider", str(generation_provider), evidence.generation_provider),
    ):
        if actual != expected:
            raise StaleVisionEvidenceError(f"shot {evidence.shot_id}: {label} changed since current review")
    visible = tuple(str(value) for value in visible_persistent_character_ids)
    if visible != evidence.visible_persistent_character_ids:
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: visible persistent character order changed since current review"
        )
    paths = tuple(Path(value) for value in identity_reference_paths)
    if len(paths) != len(evidence.identity_anchor_sha256):
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: identity reference count changed since current review"
        )
    current_anchors: list[str] = []
    for index, path in enumerate(paths):
        if path.is_symlink() or not path.is_file():
            raise StaleVisionEvidenceError(
                f"shot {evidence.shot_id}: identity reference {index} is gone or symlinked"
            )
        current_anchors.append(_sha_file(path))
    if tuple(current_anchors) != evidence.identity_anchor_sha256:
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: ordered identity anchor pixels changed since current review"
        )
    if require_pass and not evidence.is_pass():
        raise VisionReviewError(
            f"shot {evidence.shot_id}: semantic/reality/identity current review is not fully passing"
        )


def verify_evidence_current(
    evidence: VisionEvidence,
    *,
    image_path: str | Path,
    caption_text: str,
    prompt_text: str,
) -> None:
    """Fail closed when the reviewed image, caption or prompt drifted.

    All three bindings are mandatory. The image is re-hashed against the on-disk
    file because the reviewed pixels are the authoritative artifact (BLOCKER-2);
    the caption and prompt prose are re-hashed and compared to the stored
    ``caption_sha256`` / ``prompt_sha256``. A swapped image, an edited caption,
    or a changed prompt each invalidate the previously-approved evidence, so a
    stale approval can never ride a changed artifact (Scene/Preflight re-verify,
    BLOCKER-4). Making the two prose bindings required closes the old hole where
    the caption/prompt hashes were optional and a stale approval kept working
    after the caption or prompt was edited.
    """

    evidence.validate()
    path = Path(image_path)
    if not path.exists():
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: reviewed image is gone: {path}"
        )
    current_image = _sha_file(path)
    if current_image != evidence.image_sha256:
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: image changed since review "
            f"({evidence.image_sha256[:12]} -> {current_image[:12]})"
        )
    current_caption = _sha_text(caption_text)
    if current_caption != evidence.caption_sha256:
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: caption changed since review"
        )
    current_prompt = _sha_text(prompt_text)
    if current_prompt != evidence.prompt_sha256:
        raise StaleVisionEvidenceError(
            f"shot {evidence.shot_id}: prompt changed since review"
        )


def validate_review_decision(
    decision: Mapping[str, Any], *, require_pass: bool = False
) -> None:
    """Gate a single review decision.

    A decision may only advance when it carries authoritative vision evidence
    bound to the shot (image/caption/prompt hashes plus a trusted multimodal
    provider that read the pixels). ``legacy_pass`` is no longer accepted as a
    substitute for evidence. ``require_pass`` additionally rejects a
    ``mismatch`` verdict, so the render preflight can demand not just *that* a
    review exists but that it *passed*.
    """

    shot_id = decision.get("shot_id", "<unknown>")
    raw = decision.get("vision_evidence")
    if raw is None:
        raise MissingVisionEvidenceError(
            f"shot {shot_id}: decision has no vision evidence"
        )
    if not isinstance(raw, Mapping):
        raise VisionReviewError(f"shot {shot_id}: vision_evidence is not a mapping")
    evidence = VisionEvidence.from_mapping({**raw, "shot_id": raw.get("shot_id", shot_id)})
    # Structural ``validate`` is not enough: a hand-authored dict would pass it.
    # ``verify`` proves the record was cryptographically minted by a keyed vision
    # adapter that actually reviewed these exact pixels/caption/prompt.
    evidence.verify()
    if require_pass and not evidence.is_pass():
        raise VisionReviewError(
            f"shot {shot_id}: vision verdict {evidence.parity_verdict!r} is not a pass"
        )
