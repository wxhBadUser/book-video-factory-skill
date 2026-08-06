"""Cross-artifact validation for the single-narrator editorial evidence graph."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .book_research import BookResearch
from .narrative_routing import MIN_EVIDENCE_FOR_SELECTION, SCORE_KEYS
from .research_validation import validate_formal_research


class EditorialValidationError(ValueError):
    """Routes, anchors, cards, or their evidence links are invalid."""


_PLACEHOLDER = re.compile(
    r"\b(?:todo|tbd|placeholder|derived)\b|待定|占位|点击问题\s*\d+|深层命题\s*\d+|主概念\s*\d+|结尾图像\s*\d+",
    re.IGNORECASE,
)
_VALID_TREATMENTS = {"expand", "compress", "merge", "delete", "analysis_only"}
_REQUIRED_ANCHOR_TEXT = (
    "event_summary", "character_desire", "choice", "result", "cost"
)
_REQUIRED_CARD_TEXT = (
    "scene_location", "key_dialogue", "narrator_reaction", "hammer_line",
    "next_question", "emotion", "visual_function",
)


def _placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_PLACEHOLDER.search(value))
    if isinstance(value, Mapping):
        return any(_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_placeholder(item) for item in value)
    return False


def _text(obj: Mapping[str, Any], key: str, label: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EditorialValidationError(f"{label}.{key} must be nonempty")
    if _placeholder(value):
        raise EditorialValidationError(f"{label}.{key} contains placeholder content")
    return value.strip()


def _string_list(obj: Mapping[str, Any], key: str, label: str) -> list[str]:
    value = obj.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise EditorialValidationError(f"{label}.{key} must be a nonempty string list")
    if _placeholder(value):
        raise EditorialValidationError(f"{label}.{key} contains placeholder content")
    return [item.strip() for item in value]


def validate_editorial_graph(
    research: BookResearch,
    creative_decision: Mapping[str, Any],
    fate_anchors: Mapping[str, Any],
    event_cards: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate all editorial artifacts and their cross references."""
    research_report = validate_formal_research(research)
    event_ids = {event["event_id"] for event in research.event_candidates}
    known_source_ids = {
        chapter["chapter_id"] for chapter in research.chapter_notes
    } | {entry["id"] for entry in research.fact_ledger.entries}

    routes = creative_decision.get("candidate_routes")
    if not isinstance(routes, list) or len(routes) != 3:
        raise EditorialValidationError("candidate_routes must contain exactly three routes")
    route_ids: set[str] = set()
    questions: set[str] = set()
    theses: set[str] = set()
    engines: set[str] = set()
    route_scores: dict[str, int] = {}
    eligible: set[str] = set()
    for index, route in enumerate(routes):
        if not isinstance(route, Mapping):
            raise EditorialValidationError(f"route[{index}] must be an object")
        label = f"route[{index}]"
        route_id = _text(route, "route_id", label)
        if route_id in route_ids:
            raise EditorialValidationError(f"duplicate route_id: {route_id}")
        route_ids.add(route_id)
        question = _text(route, "click_question", label)
        thesis = _text(route, "deep_thesis", label)
        engine = _text(route, "narrative_engine", label)
        if question == thesis:
            raise EditorialValidationError(f"{route_id} click question equals deep thesis")
        questions.add(question)
        theses.add(thesis)
        engines.add(engine)
        for key in (
            "midpoint_question", "narrator_persona", "main_concept", "ending_image"
        ):
            _text(route, key, label)
        evidence = _string_list(route, "fate_evidence_ids", label)
        reinterpretation = route.get("reinterpretation_evidence_ids")
        if reinterpretation is None:
            reinterpretation = route.get("reinterpretation_evidence")
        if not isinstance(reinterpretation, list) or not reinterpretation or not all(
            isinstance(item, str) and item.strip() for item in reinterpretation
        ):
            raise EditorialValidationError(
                f"{route_id}.reinterpretation_evidence_ids must be nonempty"
            )
        unknown = sorted((set(evidence) | set(reinterpretation)) - event_ids)
        if unknown:
            raise EditorialValidationError(
                f"{route_id} references unknown event evidence: {unknown}"
            )
        scores = route.get("scores")
        if not isinstance(scores, Mapping) or set(scores) != set(SCORE_KEYS):
            raise EditorialValidationError(f"{route_id}.scores must contain exactly ten score keys")
        if not all(isinstance(value, int) and 1 <= value <= 5 for value in scores.values()):
            raise EditorialValidationError(f"{route_id}.scores values must be integers 1..5")
        route_scores[route_id] = sum(scores.values())
        if len(set(evidence)) >= MIN_EVIDENCE_FOR_SELECTION:
            eligible.add(route_id)
    if len(questions) != 3 or len(theses) != 3:
        raise EditorialValidationError("three routes require distinct questions and theses")
    if len(engines) != 3:
        raise EditorialValidationError("three routes require distinct narrative engines")
    selected = creative_decision.get("selected_route_id")
    if selected not in route_ids or selected not in eligible:
        raise EditorialValidationError("selected route must exist and be evidence-eligible")
    highest = max(route_scores[route_id] for route_id in eligible)
    if route_scores[str(selected)] < highest:
        raise EditorialValidationError("selected route must be a highest-scoring eligible route")
    rejected = creative_decision.get("rejected_route_reasons")
    if not isinstance(rejected, list) or len(rejected) != 2 or _placeholder(rejected):
        raise EditorialValidationError("rejected_route_reasons must explain both rejected routes")

    anchors = fate_anchors.get("anchors")
    if not isinstance(anchors, list) or not 8 <= len(anchors) <= 12:
        raise EditorialValidationError("anchors must contain 8-12 authored entries")
    anchor_ids: set[str] = set()
    selected_events: set[str] = set()
    anchor_sources: dict[str, set[str]] = {}
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            raise EditorialValidationError(f"anchor[{index}] must be an object")
        label = f"anchor[{index}]"
        anchor_id = _text(anchor, "anchor_id", label)
        if anchor_id in anchor_ids:
            raise EditorialValidationError(f"duplicate anchor_id: {anchor_id}")
        anchor_ids.add(anchor_id)
        source_event = _text(anchor, "source_event_id", label)
        if source_event not in event_ids or source_event in selected_events:
            raise EditorialValidationError(
                f"{anchor_id}.source_event_id must be unique and reference a real event"
            )
        selected_events.add(source_event)
        for key in _REQUIRED_ANCHOR_TEXT:
            _text(anchor, key, label)
        functions = _string_list(anchor, "functions", label)
        if len(set(functions)) < 2:
            raise EditorialValidationError(f"{anchor_id} requires at least two functions")
        chapter_refs = _string_list(anchor, "chapter_refs", label)
        sources = set(_string_list(anchor, "source_ids", label))
        unknown = sorted((set(chapter_refs) | sources) - known_source_ids)
        if unknown:
            raise EditorialValidationError(f"{anchor_id} references unknown sources: {unknown}")
        anchor_sources[anchor_id] = sources

    omission = fate_anchors.get("omission_map")
    if not isinstance(omission, list):
        raise EditorialValidationError("omission_map must be an array")
    omitted_expected = event_ids - selected_events
    omitted_seen: set[str] = set()
    for index, item in enumerate(omission):
        if not isinstance(item, Mapping):
            raise EditorialValidationError(f"omission_map[{index}] must be an object")
        event_id = _text(item, "event_id", f"omission_map[{index}]")
        treatment = _text(item, "treatment", f"omission_map[{index}]")
        _text(item, "reason", f"omission_map[{index}]")
        if treatment not in _VALID_TREATMENTS:
            raise EditorialValidationError(f"invalid omission treatment: {treatment}")
        if event_id in omitted_seen or event_id not in omitted_expected:
            raise EditorialValidationError("omission_map contains duplicate, selected, or unknown event")
        omitted_seen.add(event_id)
    if omitted_seen != omitted_expected:
        missing = sorted(omitted_expected - omitted_seen)
        extra = sorted(omitted_seen - omitted_expected)
        raise EditorialValidationError(
            f"omission_map must cover every unselected event exactly once; missing={missing}, extra={extra}"
        )

    cards = event_cards.get("cards")
    if not isinstance(cards, list) or len(cards) != len(anchor_ids):
        raise EditorialValidationError("event cards must contain exactly one card per anchor")
    card_anchor_ids: set[str] = set()
    card_ids: set[str] = set()
    for index, card in enumerate(cards):
        if not isinstance(card, Mapping):
            raise EditorialValidationError(f"card[{index}] must be an object")
        if _placeholder(card):
            raise EditorialValidationError(f"card[{index}] contains placeholder content")
        label = f"card[{index}]"
        card_id = _text(card, "event_card_id", label)
        if card_id in card_ids:
            raise EditorialValidationError(f"duplicate event_card_id: {card_id}")
        card_ids.add(card_id)
        anchor_id = _text(card, "anchor_id", label)
        if anchor_id not in anchor_ids or anchor_id in card_anchor_ids:
            raise EditorialValidationError("event cards must map one-to-one to anchors")
        card_anchor_ids.add(anchor_id)
        for key in _REQUIRED_CARD_TEXT:
            _text(card, key, label)
        _string_list(card, "sensory_objects", label)
        _string_list(card, "character_actions", label)
        sources = set(_string_list(card, "source_ids", label))
        unknown = sorted(sources - known_source_ids)
        if unknown:
            raise EditorialValidationError(f"{card_id} references unknown sources: {unknown}")
        if not sources.intersection(anchor_sources[anchor_id]):
            raise EditorialValidationError(f"{card_id} source IDs must overlap anchor evidence")
    if card_anchor_ids != anchor_ids:
        raise EditorialValidationError("event cards do not cover all anchors")

    return {
        "schema_version": "editorial-graph-validation.v1",
        "status": "pass",
        "research": research_report,
        "route_count": len(routes),
        "anchor_count": len(anchors),
        "card_count": len(cards),
        "selected_route_id": selected,
        "event_ids": sorted(event_ids),
        "anchor_ids": sorted(anchor_ids),
        "card_ids": sorted(card_ids),
    }
