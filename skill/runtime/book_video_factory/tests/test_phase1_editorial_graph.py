from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.book_research import BookResearch
from book_video_factory.editorial_validation import (
    EditorialValidationError,
    validate_editorial_graph,
)
from book_video_factory.event_cards import build_event_cards
from book_video_factory.fact_ledger import FactLedger
from book_video_factory.fate_anchors import select_anchors
from book_video_factory.narrative_routing import RoutingError, generate_routes
from book_video_factory.source_ingestion import SourceLevel

SCORE_KEYS = (
    "audience_resonance",
    "textual_evidence",
    "emotional_range",
    "midpoint_power",
    "reinterpretation_power",
    "modern_mapping",
    "visual_potential",
    "originality",
    "character_complexity",
    "ending_specificity",
)


def _research() -> BookResearch:
    chapters = [
        {"chapter_id": f"CH{i:02d}", "summary": f"章节{i}"}
        for i in range(1, 21)
    ]
    events = [
        {
            "event_id": f"EV{i:03d}",
            "chapter_ref": f"CH{i:02d}",
            "summary": f"真实事件{i}",
            "changes_fate": True,
            "reveals_character": True,
            "proves_thesis": i % 2 == 0,
            "strong_visual": True,
            "key_dialogue": i % 3 == 0,
            "launches_question": True,
            "supports_payoff": i > 15,
            "source_ids": [f"CH{i:02d}", f"F{i:03d}"],
        }
        for i in range(1, 21)
    ]
    ledger = FactLedger([
        {"id": f"F{i:03d}", "type": "F", "claim": f"事实{i}", "source_ref": f"第{i}章", "verified": True}
        for i in range(1, 21)
    ])
    return BookResearch(
        book_title="老人与海",
        author="海明威",
        source_level=SourceLevel.A,
        research_status="ready",
        chapter_notes=chapters,
        character_map=[{"character_id": "C001", "name": "老人"}],
        event_candidates=events,
        fact_ledger=ledger,
        coverage="full_text_available",
    )


def _decision() -> dict:
    routes = []
    route_data = [
        ("route-1", "一个人连续失败八十四天，还要不要再出海？", "尊严不是赢，而是在失败中仍选择行动", "first_person_growth", 5),
        ("route-2", "人到底能不能战胜命运？", "人与自然的搏斗最终暴露的是自我边界", "moral_debate", 4),
        ("route-3", "失去所有成果后，努力还有意义吗？", "真正留下来的不是猎物，而是行动塑造的人", "psychological_mystery", 3),
    ]
    for idx, (rid, question, thesis, engine, score) in enumerate(route_data):
        evidence = [f"EV{i:03d}" for i in range(1 + idx, 11 + idx)]
        routes.append({
            "route_id": rid,
            "click_question": question,
            "deep_thesis": thesis,
            "narrative_engine": engine,
            "fate_evidence_ids": evidence,
            "midpoint_question": f"中点问题{idx + 1}：当大鱼出现，胜利为什么仍不确定？",
            "reinterpretation_evidence_ids": evidence[-3:],
            "narrator_persona": ["克制讲述者", "冷静辩论者", "温柔侦探"][idx],
            "main_concept": ["行动尊严", "边界", "努力的残留"][idx],
            "target_emotional_curve": [{"section": "hook", "emotion": "curiosity"}],
            "visual_potential": score,
            "ending_image": ["老人梦见狮子", "空船回港", "鱼骨留在岸边"][idx],
            "recommended_duration_minutes": 18,
            "scores": {key: score for key in SCORE_KEYS},
        })
    return {
        "schema_version": "creative-decision.v1",
        "book_id": "old-man-and-the-sea",
        "candidate_routes": routes,
        "selected_route_id": "route-1",
        "rejected_route_reasons": ["route-2: 证据链较弱", "route-3: 中点动力较弱"],
        "recommended_duration_minutes": 18,
    }


def _anchors() -> dict:
    anchors = []
    for i in range(1, 9):
        anchors.append({
            "anchor_id": f"FA{i:03d}",
            "source_event_id": f"EV{i:03d}",
            "chapter_refs": [f"CH{i:02d}"],
            "event_summary": f"老人面对第{i}次关键选择",
            "character_desire": "证明自己仍然是一个渔夫",
            "choice": "继续独自出海",
            "result": "距离岸边越来越远",
            "cost": "体力、孤独和失去援助的风险",
            "functions": ["changes_fate", "reveals_character", "strong_visual"],
            "treatment": "expand",
            "source_ids": [f"CH{i:02d}", f"F{i:03d}"],
        })
    omission = [
        {"event_id": f"EV{i:03d}", "treatment": "compress", "reason": "保留因果但压缩重复行动"}
        for i in range(9, 21)
    ]
    return {
        "schema_version": "fate-anchors.v1",
        "book_id": "old-man-and-the-sea",
        "candidate_event_count": 20,
        "anchors": anchors,
        "omission_map": omission,
    }


def _cards() -> dict:
    cards = []
    for i in range(1, 9):
        cards.append({
            "event_card_id": f"EC{i:03d}",
            "anchor_id": f"FA{i:03d}",
            "scene_location": "古巴海边的小屋" if i == 1 else "墨西哥湾流上的小船",
            "sensory_objects": ["粗麻绳", "盐渍木板"],
            "character_actions": ["老人收紧手中的钓线"],
            "key_dialogue": "鱼啊，我尊敬你，但今天我必须杀死你。",
            "narrator_reaction": "读到这里，我第一次意识到，他并没有把对手当成战利品。",
            "hammer_line": "他要证明的不是鱼有多大，而是自己还没有被失败定义。",
            "next_question": "可当胜利终于出现，命运会让他完整带回去吗？",
            "emotion": "克制而紧张",
            "visual_function": "escalation",
            "source_ids": [f"CH{i:02d}", f"F{i:03d}"],
        })
    return {"schema_version": "event-cards.v1", "cards": cards}


def test_route_generation_without_agent_authored_routes_fails() -> None:
    with pytest.raises(RoutingError, match="agent-authored"):
        generate_routes(_research())


def test_three_routes_must_have_distinct_questions_theses_and_engines() -> None:
    decision = _decision()
    decision["candidate_routes"][1]["narrative_engine"] = decision["candidate_routes"][0]["narrative_engine"]
    with pytest.raises(EditorialValidationError, match="distinct.*engine"):
        validate_editorial_graph(_research(), decision, _anchors(), _cards())


def test_route_evidence_must_exist_in_research() -> None:
    decision = _decision()
    decision["candidate_routes"][0]["fate_evidence_ids"][0] = "EV999"
    with pytest.raises(EditorialValidationError, match="unknown.*EV999"):
        validate_editorial_graph(_research(), decision, _anchors(), _cards())


def test_selected_route_must_be_highest_scoring_eligible_route() -> None:
    decision = _decision()
    decision["selected_route_id"] = "route-3"
    with pytest.raises(EditorialValidationError, match="highest-scoring"):
        validate_editorial_graph(_research(), decision, _anchors(), _cards())


def test_anchor_generation_without_authored_anchors_fails() -> None:
    with pytest.raises(ValueError, match="agent-authored"):
        select_anchors(_research().event_candidates)


def test_anchor_source_event_must_exist_and_be_unique() -> None:
    anchors = _anchors()
    anchors["anchors"][1]["source_event_id"] = anchors["anchors"][0]["source_event_id"]
    with pytest.raises(EditorialValidationError, match="source_event_id"):
        validate_editorial_graph(_research(), _decision(), anchors, _cards())


def test_anchor_must_not_invent_missing_functions_or_story_logic() -> None:
    anchors = _anchors()
    anchors["anchors"][0]["character_desire"] = ""
    with pytest.raises(EditorialValidationError, match="character_desire"):
        validate_editorial_graph(_research(), _decision(), anchors, _cards())


def test_omission_map_covers_every_unselected_event_exactly_once() -> None:
    anchors = _anchors()
    anchors["omission_map"].pop()
    with pytest.raises(EditorialValidationError, match="omission_map"):
        validate_editorial_graph(_research(), _decision(), anchors, _cards())


def test_card_generation_without_authored_cards_fails() -> None:
    from book_video_factory.fate_anchors import FateAnchors

    payload = _anchors()
    fa = FateAnchors(
        book_id=payload["book_id"],
        candidate_event_count=payload["candidate_event_count"],
        anchors=payload["anchors"],
        omission_map=payload["omission_map"],
    )
    with pytest.raises(ValueError, match="agent-authored"):
        build_event_cards(fa, _research().fact_ledger)


def test_exactly_one_complete_card_per_anchor() -> None:
    cards = _cards()
    cards["cards"][0]["hammer_line"] = "待定"
    with pytest.raises(EditorialValidationError, match="placeholder"):
        validate_editorial_graph(_research(), _decision(), _anchors(), cards)


def test_card_sources_must_overlap_anchor_sources() -> None:
    cards = _cards()
    cards["cards"][0]["source_ids"] = ["CH20", "F020"]
    with pytest.raises(EditorialValidationError, match="overlap"):
        validate_editorial_graph(_research(), _decision(), _anchors(), cards)


def test_valid_graph_returns_counts_and_selected_route() -> None:
    report = validate_editorial_graph(_research(), _decision(), _anchors(), _cards())
    assert report["status"] == "pass"
    assert report["route_count"] == 3
    assert report["anchor_count"] == 8
    assert report["card_count"] == 8
    assert report["selected_route_id"] == "route-1"
