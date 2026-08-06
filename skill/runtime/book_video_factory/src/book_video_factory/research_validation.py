"""Fail-closed validation for formal book research.

This module validates agent-authored research. It never invents chapters, events,
characters, facts, or citations. A failing report blocks formal editorial work.
"""
from __future__ import annotations

import re
from typing import Any

from .book_research import BookResearch
from .fact_ledger import validate_fact_ledger
from .source_ingestion import SourceLevel


class ResearchValidationError(ValueError):
    """Formal research is incomplete, fabricated, or not traceable."""


_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bderived(?:\s+candidate)?\b", re.IGNORECASE),
    re.compile(r"\b(?:todo|tbd|placeholder)\b", re.IGNORECASE),
    re.compile(r"待定|占位|点击问题\s*\d+|深层命题\s*\d+|主概念\s*\d+|结尾图像\s*\d+"),
)


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _PLACEHOLDER_PATTERNS)
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_placeholder(item) for item in value)
    return False


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchValidationError(f"{label} must be a nonempty string")
    if _contains_placeholder(value):
        raise ResearchValidationError(f"{label} contains placeholder content")
    return value.strip()


def validate_formal_research(research: BookResearch) -> dict[str, Any]:
    """Validate one research object for formal single-narrator writing."""
    if research.source_level is not SourceLevel.A:
        raise ResearchValidationError("formal research requires source level A")
    if research.research_status not in {"ready", "complete"}:
        raise ResearchValidationError(
            f"formal research status must be ready/complete, got {research.research_status!r}"
        )
    if research.coverage and research.coverage != "full_text_available":
        raise ResearchValidationError("formal research requires full_text_available coverage")
    _require_nonempty_string(research.book_title, "book_title")
    _require_nonempty_string(research.author, "author")

    if not isinstance(research.chapter_notes, list) or not research.chapter_notes:
        raise ResearchValidationError("chapter_notes must be nonempty")
    chapter_ids: set[str] = set()
    for index, chapter in enumerate(research.chapter_notes):
        if not isinstance(chapter, dict):
            raise ResearchValidationError(f"chapter_notes[{index}] must be an object")
        chapter_id = _require_nonempty_string(chapter.get("chapter_id"), f"chapter_notes[{index}].chapter_id")
        if chapter_id in chapter_ids:
            raise ResearchValidationError(f"duplicate chapter_id: {chapter_id}")
        chapter_ids.add(chapter_id)
        _require_nonempty_string(chapter.get("summary"), f"chapter_notes[{index}].summary")
        if _contains_placeholder(chapter):
            raise ResearchValidationError(f"chapter_notes[{index}] contains placeholder content")

    validate_fact_ledger(research.fact_ledger)
    fact_ids = {str(entry["id"]) for entry in research.fact_ledger.entries}

    events = research.event_candidates
    if not isinstance(events, list) or not 20 <= len(events) <= 40:
        raise ResearchValidationError(
            f"formal research requires 20-40 real event candidates, got {len(events) if isinstance(events, list) else 'invalid'}"
        )

    event_ids: set[str] = set()
    reference_graph: dict[str, list[str]] = {}
    known_refs = chapter_ids | fact_ids
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ResearchValidationError(f"event_candidates[{index}] must be an object")
        if _contains_placeholder(event):
            raise ResearchValidationError(f"event_candidates[{index}] contains placeholder content")
        event_id = _require_nonempty_string(event.get("event_id"), f"event_candidates[{index}].event_id")
        if event_id in event_ids:
            raise ResearchValidationError(f"duplicate event_id: {event_id}")
        event_ids.add(event_id)
        chapter_ref = _require_nonempty_string(
            event.get("chapter_ref"), f"event_candidates[{index}].chapter_ref"
        )
        if chapter_ref not in chapter_ids:
            raise ResearchValidationError(
                f"event {event_id} references unknown chapter {chapter_ref}"
            )
        _require_nonempty_string(event.get("summary"), f"event_candidates[{index}].summary")
        source_ids = event.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids or not all(
            isinstance(item, str) and item.strip() for item in source_ids
        ):
            raise ResearchValidationError(f"event {event_id}.source_ids must be nonempty")
        unknown = sorted(set(source_ids) - known_refs)
        if unknown:
            raise ResearchValidationError(
                f"event {event_id} references unknown source IDs: {unknown}"
            )
        reference_graph[event_id] = list(source_ids)

    if not isinstance(research.character_map, list) or not research.character_map:
        raise ResearchValidationError("character_map must be nonempty for formal research")
    if _contains_placeholder(research.character_map):
        raise ResearchValidationError("character_map contains placeholder content")

    return {
        "schema_version": "formal-research-validation.v1",
        "status": "pass",
        "book_title": research.book_title,
        "source_level": research.source_level.value,
        "chapter_count": len(chapter_ids),
        "event_count": len(event_ids),
        "fact_count": len(fact_ids),
        "character_count": len(research.character_map),
        "reference_graph": reference_graph,
    }
