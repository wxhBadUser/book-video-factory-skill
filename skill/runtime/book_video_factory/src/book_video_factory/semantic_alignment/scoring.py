"""Deterministic alignment scoring used by the per-shot review reports.

The score is *not* a gate. Gates are binary and evidence-backed (see
``validation`` and the ``vision_review`` package). This score exists so a human
reading the review report can sort 173 shots by how suspicious they look.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, Mapping, Sequence

from .models import VisualProposition
from .validation import is_boilerplate_rationale, normalize_rationale

WEIGHTS: Mapping[str, float] = {
    "entity_coverage": 0.40,
    "lexical_overlap": 0.20,
    "rationale_substance": 0.15,
    "mode_fit": 0.25,
}
SUBSTANCE_TARGET_CHARS = 32


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _bigrams(text: str) -> set[str]:
    compact = "".join(
        char for char in _normalize(text) if not unicodedata.category(char).startswith(("P", "Z", "C"))
    )
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _proposition_blob(proposition: VisualProposition) -> str:
    parts = [
        proposition.subject,
        proposition.action,
        proposition.environment,
        proposition.mood,
        *(item.entity_id for item in proposition.entity_visibility),
        *(item.natural_language for item in proposition.entity_visibility),
        *proposition.surrogate_objects,
        *proposition.source_terms,
    ]
    return "".join(_normalize(part) for part in parts)


def score_alignment(
    *,
    proposition: VisualProposition | Mapping[str, object],
    caption_texts: Sequence[str],
    source_entities: Iterable[str],
) -> dict[str, object]:
    """Return a reproducible 0..1 alignment score with its components."""
    resolved = (
        proposition
        if isinstance(proposition, VisualProposition)
        else VisualProposition.from_mapping(proposition)  # type: ignore[arg-type]
    )
    entities = [str(item).strip() for item in source_entities if str(item).strip()]
    caption_blob = "".join(_normalize(text) for text in caption_texts)
    blob = _proposition_blob(resolved)

    if entities:
        covered = [entity for entity in entities if entity in blob]
        entity_coverage = len(covered) / len(entities)
    else:
        covered = []
        entity_coverage = 0.0

    lexical_overlap = _jaccard(_bigrams(caption_blob), _bigrams(blob))

    if is_boilerplate_rationale(resolved.rationale_text):
        rationale_substance = 0.0
    else:
        informative = len(normalize_rationale(resolved.rationale_text))
        rationale_substance = min(1.0, informative / SUBSTANCE_TARGET_CHARS)

    if resolved.mode == "Literal":
        visible = [item for item in resolved.entity_visibility if item.must_be_visible]
        mode_fit = 1.0 if (visible and entity_coverage > 0.0) else 0.0
    elif resolved.mode == "Symbolic":
        named_surrogate = any(
            item and item in _normalize(resolved.rationale_text)
            for item in resolved.surrogate_objects
        )
        named_source = any(entity in _normalize(resolved.rationale_text) for entity in entities)
        mode_fit = 1.0 if (named_surrogate and named_source) else 0.4 if named_surrogate else 0.0
    elif resolved.mode == "Abstract":
        mode_fit = 0.0 if any(item.must_be_visible for item in resolved.entity_visibility) else 1.0
    else:
        mode_fit = 0.0

    components = {
        "entity_coverage": round(entity_coverage, 4),
        "lexical_overlap": round(lexical_overlap, 4),
        "rationale_substance": round(rationale_substance, 4),
        "mode_fit": round(mode_fit, 4),
    }
    total = sum(WEIGHTS[key] * value for key, value in components.items())
    return {
        "total": round(min(1.0, max(0.0, total)), 4),
        "components": components,
        "covered_entities": covered,
        "missing_entities": [entity for entity in entities if entity not in covered],
        "mode": resolved.mode,
    }


__all__ = ["SUBSTANCE_TARGET_CHARS", "WEIGHTS", "score_alignment"]
