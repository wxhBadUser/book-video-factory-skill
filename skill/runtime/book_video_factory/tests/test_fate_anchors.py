"""Phase 5 失败测试：命运锚点 + 事件卡。

20-40 候选事件 → 8-12 命运锚点（每锚≥2 功能）；未选事件标记 expand/compress/merge/delete/analysis_only。
事件卡十要素：scene_location/sensory_object/character_action/key_dialogue/narrator_reaction/
hammer_line/next_question/source_ids/emotion/visual_function。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.fate_anchors import (  # noqa: E402
    FateAnchors,
    select_anchors,
)
from book_video_factory.event_cards import (  # noqa: E402
    EventCards,
    build_event_cards,
)
from book_video_factory.narrator_essay_contracts import (  # noqa: E402
    validate_fate_anchors,
    validate_event_cards,
)
from book_video_factory.fact_ledger import FactLedger


def _candidates(n: int = 30):
    return [
        {
            "event_id": f"EV{i+1:03d}",
            "chapter_ref": f"CH{i:02d}",
            "summary": f"事件{i+1}",
            "changes_fate": i % 3 == 0,
            "reveals_character": i % 2 == 0,
            "proves_thesis": i % 4 == 0,
            "strong_visual": i % 2 == 1,
            "key_dialogue": i % 5 == 0,
            "launches_question": i % 3 == 1,
            "supports_payoff": i % 4 == 1,
            "source_ids": [f"CH{i:02d}"],
        }
        for i in range(n)
    ]


def _authored_anchors(cands):
    anchors = []
    for i, cand in enumerate(cands[:8], start=1):
        anchors.append({
            "anchor_id": f"FA{i:03d}",
            "source_event_id": cand["event_id"],
            "chapter_refs": [cand["chapter_ref"]],
            "event_summary": cand["summary"],
            "character_desire": "改变自己的命运",
            "choice": "主动做出选择",
            "result": "局势发生变化",
            "cost": "承担真实代价",
            "functions": ["changes_fate", "reveals_character"],
            "treatment": "expand",
            "source_ids": cand["source_ids"],
            "changes_fate": True,
            "reveals_character": True,
        })
    omission = [
        {"event_id": cand["event_id"], "treatment": "compress", "reason": "压缩重复推进"}
        for cand in cands[8:]
    ]
    return anchors, omission


def _select_authored(cands):
    anchors, omission = _authored_anchors(cands)
    return select_anchors(cands, anchors=anchors, omission_map=omission)


def _authored_cards(fa):
    return [{
        "event_card_id": f"EC{i:03d}",
        "anchor_id": anchor["anchor_id"],
        "scene_location": "庄园大厅",
        "sensory_objects": ["烛台"],
        "character_actions": ["人物走进房间"],
        "key_dialogue": "我必须做出决定。",
        "narrator_reaction": "读到这里，我开始意识到代价已经出现。",
        "hammer_line": "选择从来不是免费的。",
        "next_question": "接下来他会失去什么？",
        "source_ids": anchor["source_ids"],
        "emotion": "紧张",
        "visual_function": "escalation",
    } for i, anchor in enumerate(fa.anchors, start=1)]


class FateAnchorsTests(unittest.TestCase):
    def test_selects_8_to_12_anchors(self) -> None:
        fa = _select_authored(_candidates(30))
        self.assertIsInstance(fa, FateAnchors)
        self.assertTrue(8 <= len(fa.anchors) <= 12)

    def test_each_anchor_at_least_two_functions(self) -> None:
        fa = _select_authored(_candidates(30))
        funcs = ["changes_fate", "reveals_character", "proves_thesis",
                 "strong_visual", "key_dialogue", "launches_question", "supports_payoff"]
        for a in fa.anchors:
            count = sum(1 for f in funcs if a.get(f))
            self.assertGreaterEqual(count, 2, f"anchor {a.get('anchor_id')} 功能<2")

    def test_unselected_events_have_treatment(self) -> None:
        fa = _select_authored(_candidates(30))
        valid_treatments = {"expand", "compress", "merge", "delete", "analysis_only"}
        for o in fa.omission_map:
            self.assertIn(o["treatment"], valid_treatments)

    def test_no_important_event_lost_without_treatment(self) -> None:
        cands = _candidates(30)
        fa = _select_authored(cands)
        selected_ids = {a["source_event_id"] for a in fa.anchors}
        omitted = [c for c in cands if c["event_id"] not in selected_ids]
        # 每个未选事件必须在 omission_map 中有交代
        omitted_ids = {o["event_id"] for o in fa.omission_map}
        for c in omitted:
            self.assertIn(c["event_id"], omitted_ids, f"未选事件 {c['event_id']} 无交代")

    def test_validates_against_contract(self) -> None:
        fa = _select_authored(_candidates(30))
        validate_fate_anchors(fa.to_dict())


class EventCardsTests(unittest.TestCase):
    def test_cards_match_anchor_count(self) -> None:
        fa = _select_authored(_candidates(30))
        cards = build_event_cards(fa, FactLedger(), cards=_authored_cards(fa))
        self.assertIsInstance(cards, EventCards)
        self.assertEqual(len(cards.cards), len(fa.anchors))

    def test_each_card_has_ten_elements(self) -> None:
        fa = _select_authored(_candidates(30))
        cards = build_event_cards(fa, FactLedger(), cards=_authored_cards(fa))
        required = ("scene_location", "sensory_objects", "character_actions",
                    "key_dialogue", "narrator_reaction", "hammer_line",
                    "next_question", "source_ids", "emotion", "visual_function")
        for c in cards.cards:
            for f in required:
                self.assertIn(f, c, f"card missing {f}")
                self.assertTrue(c[f], f"card field {f} empty")

    def test_validates_against_contract(self) -> None:
        fa = _select_authored(_candidates(30))
        cards = build_event_cards(fa, FactLedger(), cards=_authored_cards(fa))
        validate_event_cards(cards.to_dict())


if __name__ == "__main__":
    unittest.main()
