"""Phase 4 失败测试：三路线竞争。

每本书必须生成 3 条完整路线（非伪造），按 10 项评分，选 1。
路线缺少至少 8 个有效事件证据不得成为最终选择。
click_question 与 deep_thesis 必须分离。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.narrative_routing import (  # noqa: E402
    CreativeDecision,
    Route,
    RoutingError,
    build_creative_decision,
    generate_routes,
    score_routes,
    select_route,
)
from book_video_factory.book_research import BookResearch
from book_video_factory.source_ingestion import SourceLevel
from book_video_factory.fact_ledger import FactLedger


def _mock_research() -> BookResearch:
    return BookResearch(
        book_title="呼啸山庄", author="艾米莉·勃朗特",
        source_level=SourceLevel.A, research_status="ready",
        chapter_notes=[{"chapter_id": f"CH{i:02d}"} for i in range(34)],
        event_candidates=[
            {"event_id": f"EV{i+1:03d}", "chapter_ref": f"CH{i:02d}",
             "changes_fate": True, "reveals_character": True, "strong_visual": True}
            for i in range(30)
        ],
        fact_ledger=FactLedger(),
    )


def _route(rid: str, n_evidence: int = 10, *, with_scores: bool = True) -> Route:
    route = Route(
        route_id=rid,
        click_question=f"点击问题{rid}",
        deep_thesis=f"深层命题{rid}",
        narrative_engine="generational_cycle_and_repair",
        fate_evidence_ids=[f"EV{i+1:03d}" for i in range(n_evidence)],
        midpoint_question=f"中点{rid}",
        reinterpretation_evidence=[f"EV{i+1:03d}" for i in range(3)],
        narrator_persona="阴郁故事讲述者",
        main_concept=f"主概念{rid}",
        emotion_curve=[{"section": "hook", "emotion": "curiosity"}],
        visual_potential=4,
        ending_image=f"结尾图像{rid}",
        recommended_duration=18,
    )
    if with_scores:
        route.scores = {
            "audience_resonance": 4,
            "textual_evidence": 4,
            "emotional_range": 4,
            "midpoint_power": 4,
            "reinterpretation_power": 4,
            "modern_mapping": 4,
            "visual_potential": 4,
            "originality": 4,
            "character_complexity": 4,
            "ending_specificity": 4,
        }
    return route


class GenerateRoutesTests(unittest.TestCase):
    def test_generates_three_distinct_routes(self) -> None:
        routes = generate_routes(_mock_research(), routes=[
            _route("route-1"), _route("route-2"), _route("route-3"),
        ])
        self.assertEqual(len(routes), 3)
        ids = [r.route_id for r in routes]
        self.assertEqual(len(set(ids)), 3)
        for r in routes:
            self.assertNotEqual(r.click_question, r.deep_thesis)

    def test_each_route_has_required_fields(self) -> None:
        r = _route("route-1")
        for field in ("click_question", "deep_thesis", "narrative_engine",
                      "fate_evidence_ids", "midpoint_question", "reinterpretation_evidence",
                      "narrator_persona", "main_concept", "emotion_curve", "visual_potential",
                      "ending_image", "recommended_duration"):
            self.assertTrue(getattr(r, field) is not None and getattr(r, field) != "",
                            f"route missing {field}")


class ScoreRoutesTests(unittest.TestCase):
    def test_missing_agent_authored_scores_fails(self) -> None:
        routes = [_route("route-1", with_scores=False), _route("route-2"), _route("route-3")]
        with self.assertRaisesRegex(RoutingError, "agent-authored.*scores"):
            score_routes(routes)

    def test_ten_scores_each_route(self) -> None:
        routes = [_route("route-1"), _route("route-2"), _route("route-3")]
        scored = score_routes(routes)
        for r in scored:
            self.assertEqual(len(r.scores), 10)
            for v in r.scores.values():
                self.assertTrue(1 <= v <= 5)


class SelectRouteTests(unittest.TestCase):
    def test_route_with_fewer_than_8_evidence_cannot_be_selected(self) -> None:
        routes = [_route("route-1", n_evidence=10), _route("route-2", n_evidence=5),
                  _route("route-3", n_evidence=10)]
        scored = score_routes(routes)
        selected = select_route(scored)
        self.assertNotEqual(selected.route_id, "route-2")  # 5 < 8 不可选

    def test_selects_highest_scoring_eligible(self) -> None:
        routes = [_route("route-1", n_evidence=10), _route("route-2", n_evidence=10),
                  _route("route-3", n_evidence=10)]
        scored = score_routes(routes)
        selected = select_route(scored)
        self.assertIn(selected.route_id, ["route-1", "route-2", "route-3"])


class CreativeDecisionTests(unittest.TestCase):
    def test_build_creative_decision_contract(self) -> None:
        routes = [_route("route-1"), _route("route-2"), _route("route-3")]
        scored = score_routes(routes)
        selected = select_route(scored)
        cd = build_creative_decision(_mock_research(), scored, selected)
        self.assertIsInstance(cd, CreativeDecision)
        self.assertEqual(cd.selected_route_id, selected.route_id)
        self.assertEqual(len(cd.candidate_routes), 3)
        # 委托给 narrator_essay_contracts 校验
        from book_video_factory.narrator_essay_contracts import validate_creative_decision
        validate_creative_decision(cd.to_dict())


if __name__ == "__main__":
    unittest.main()
