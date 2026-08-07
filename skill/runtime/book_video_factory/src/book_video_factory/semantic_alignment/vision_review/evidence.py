"""Persistence, staleness and decision-gate helpers for vision evidence.

* ``build_vision_evidence_document`` / ``load_vision_evidence_document`` move a
  set of ``VisionEvidence`` records in and out of the ``vision-evidence.v1``
  artifact.
* ``verify_evidence_current`` is the staleness guard: it recomputes the image,
  caption and prompt hashes and fails closed if any of them drifted from the
  stored evidence, so an edited caption or a swapped image can never be served
  by a stale approval.
* ``validate_review_decision`` is the gate used by the per-shot and encoded
  reviews: evidence present -> authoritative; missing but ``legacy_pass`` ->
  accepted as legacy; otherwise it refuses to advance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    MissingVisionEvidenceError,
    StaleVisionEvidenceError,
    VisionEvidence,
    VisionReviewError,
)

SCHEMA_VERSION = "vision-evidence.v1"


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
        evidence.validate()
        loaded[str(shot_id)] = evidence
    return loaded


def verify_evidence_current(
    evidence: VisionEvidence,
    *,
    image_path: str | Path,
    caption_text: str,
    prompt_text: str,
) -> None:
    """Fail closed when the reviewed image, caption or prompt drifted."""

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

    ``require_pass`` additionally rejects a ``mismatch`` verdict, so the render
    preflight can demand not just *that* a review exists but that it *passed*.
    """

    shot_id = decision.get("shot_id", "<unknown>")
    raw = decision.get("vision_evidence")
    if raw is None:
        if decision.get("legacy_pass") is True:
            return
        raise MissingVisionEvidenceError(
            f"shot {shot_id}: decision has no vision evidence and is not marked legacy_pass"
        )
    if not isinstance(raw, Mapping):
        raise VisionReviewError(f"shot {shot_id}: vision_evidence is not a mapping")
    evidence = VisionEvidence.from_mapping({**raw, "shot_id": raw.get("shot_id", shot_id)})
    evidence.validate()
    if require_pass and not evidence.is_pass():
        raise VisionReviewError(
            f"shot {shot_id}: vision verdict {evidence.parity_verdict!r} is not a pass"
        )
