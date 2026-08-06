"""Phase 6.5 测试：脚本锁定门禁 / 独立盲审 / 原创性泄漏检测。

三个模块的职责边界：
- originality_check：把候选稿与"参考创作者转录稿 + 用户满意稿"做 n-gram 比对，
  连续重合 > MAX_CONSECUTIVE 判 blocking，不做任何质量判断。
- blind_review：两名互相看不到对方结论的评审（文学性 / 留存率）各自打分，
  合并时若单项分差 >= 2 必须标 disagreement，不自动取平均掩盖分歧。
- script_lock_gate：汇总来源等级、质量分、时长、中点、理论占比、原创性、盲审，
  fail-closed —— 任何一项缺失即视为不通过，不得"缺省放行"。

本文件先于实现编写（红阶段），实现后应全绿。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.originality_check import (  # noqa: E402
    MAX_CONSECUTIVE_CHARS,
    OriginalityError,
    check_originality,
)
from book_video_factory.blind_review import (  # noqa: E402
    REVIEWER_LITERARY,
    REVIEWER_RETENTION,
    BlindReviewError,
    merge_blind_reviews,
    new_review,
)
from book_video_factory.script_lock_gate import (  # noqa: E402
    ScriptLockError,
    evaluate_script_lock,
)


def _good_quality() -> dict:
    """15 项 ×0-5，总分 >=66，任一 >=4，事实可靠性 =5。"""
    items = {f"item_{i:02d}": 4 for i in range(1, 16)}
    items["fact_reliability"] = 5
    # 4*14 + 5 = 61 -> 补到 68
    for k in ("item_01", "item_02", "item_03", "item_04", "item_05", "item_06", "item_07"):
        items[k] = 5
    return {"items": items, "total": sum(items.values()), "max_total": 75}


def _good_metrics() -> dict:
    return {
        "total_spoken_chars": 4658,
        "estimated_minutes": 17.58,
        "midpoint_ratio": 0.5125,
        "theory_ratio": 0.0436,
        "main_concept_count": 1,
    }


def _good_source() -> dict:
    return {"level": "A", "is_full_text": True, "research_status": "complete"}


def _good_reviews() -> list:
    return [
        new_review(REVIEWER_LITERARY, {"depth": 4, "evidence": 5, "voice": 4}, verdict="pass"),
        new_review(REVIEWER_RETENTION, {"hook": 5, "midpoint": 4, "payoff": 4}, verdict="pass"),
    ]


class TestOriginality(unittest.TestCase):
    def test_clean_script_passes(self):
        rep = check_originality("我们回到荒原上的那两座庄园，说一说债是怎么传下去的。",
                                {"ref-1": "完全不相干的一段参考转录文本，讲的是别的书。"})
        self.assertTrue(rep["passed"])
        self.assertEqual(rep["violations"], [])

    def test_long_verbatim_overlap_blocks(self):
        stolen = "这是一段被原样搬运过来的参考创作者的固定话术并且长度明显超过阈值上限"
        rep = check_originality(f"开头一句。{stolen}。结尾一句。", {"ref-1": f"前文。{stolen}。后文。"})
        self.assertFalse(rep["passed"])
        self.assertTrue(rep["violations"])
        self.assertGreater(rep["violations"][0]["length"], MAX_CONSECUTIVE_CHARS)
        self.assertEqual(rep["violations"][0]["source_id"], "ref-1")

    def test_raise_mode_is_fail_closed(self):
        stolen = "这是一段被原样搬运过来的参考创作者的固定话术并且长度明显超过阈值上限"
        with self.assertRaises(OriginalityError):
            check_originality(stolen, {"ref-1": stolen}, raise_on_violation=True)

    def test_empty_corpus_is_rejected_not_silently_passed(self):
        """没有比对语料时不得默认通过——那等于没检测。"""
        with self.assertRaises(OriginalityError):
            check_originality("任意文本", {})


class TestBlindReview(unittest.TestCase):
    def test_two_independent_reviewers_merge(self):
        merged = merge_blind_reviews(_good_reviews())
        self.assertEqual(merged["reviewer_count"], 2)
        self.assertIn(REVIEWER_LITERARY, merged["reviewers"])
        self.assertIn(REVIEWER_RETENTION, merged["reviewers"])
        self.assertTrue(merged["independent"])

    def test_single_reviewer_rejected(self):
        with self.assertRaises(BlindReviewError):
            merge_blind_reviews([new_review(REVIEWER_LITERARY, {"depth": 4}, verdict="pass")])

    def test_same_reviewer_twice_is_not_independent(self):
        with self.assertRaises(BlindReviewError):
            merge_blind_reviews([
                new_review(REVIEWER_LITERARY, {"depth": 4}, verdict="pass"),
                new_review(REVIEWER_LITERARY, {"depth": 5}, verdict="pass"),
            ])

    def test_disagreement_is_surfaced_not_averaged(self):
        merged = merge_blind_reviews([
            new_review(REVIEWER_LITERARY, {"shared": 5}, verdict="pass"),
            new_review(REVIEWER_RETENTION, {"shared": 2}, verdict="revise"),
        ])
        self.assertTrue(merged["has_disagreement"])
        self.assertIn("shared", merged["disagreements"])
        self.assertEqual(merged["verdict"], "revise")


class TestScriptLockGate(unittest.TestCase):
    def _eval(self, **over):
        kwargs = dict(
            source=_good_source(),
            metrics=_good_metrics(),
            quality=_good_quality(),
            originality={"passed": True, "violations": []},
            blind_review=merge_blind_reviews(_good_reviews()),
            blocking_issues=[],
        )
        kwargs.update(over)
        return evaluate_script_lock(**kwargs)

    def test_all_conditions_met_locks(self):
        rep = self._eval()
        self.assertTrue(rep["script_locked"], rep["failed_checks"])
        self.assertEqual(rep["failed_checks"], [])

    def test_source_level_b_blocks(self):
        rep = self._eval(source={"level": "B", "is_full_text": False, "research_status": "partial"})
        self.assertFalse(rep["script_locked"])
        self.assertIn("source_level_A", rep["failed_checks"])

    def test_ready_is_accepted_as_complete_vocabulary(self):
        """source_ingestion 对 Level A 产出 'ready'，与计划书的 'complete' 同义。"""
        rep = self._eval(source={"level": "A", "is_full_text": True, "research_status": "ready"})
        self.assertTrue(rep["script_locked"], rep["failed_checks"])

    def test_limited_research_blocks(self):
        rep = self._eval(source={"level": "A", "is_full_text": True, "research_status": "limited"})
        self.assertFalse(rep["script_locked"])
        self.assertIn("research_complete", rep["failed_checks"])

    def test_duration_out_of_range_blocks(self):
        m = _good_metrics()
        m["estimated_minutes"] = 10.87
        rep = self._eval(metrics=m)
        self.assertFalse(rep["script_locked"])
        self.assertIn("duration_range", rep["failed_checks"])

    def test_midpoint_out_of_range_blocks(self):
        m = _good_metrics()
        m["midpoint_ratio"] = 0.5572
        rep = self._eval(metrics=m)
        self.assertFalse(rep["script_locked"])
        self.assertIn("midpoint_range", rep["failed_checks"])

    def test_any_quality_item_below_4_blocks(self):
        q = _good_quality()
        q["items"]["item_09"] = 3
        q["total"] = sum(q["items"].values())
        rep = self._eval(quality=q)
        self.assertFalse(rep["script_locked"])
        self.assertIn("quality_each_item_min_4", rep["failed_checks"])

    def test_fact_reliability_must_be_5(self):
        q = _good_quality()
        q["items"]["fact_reliability"] = 4
        q["total"] = sum(q["items"].values())
        rep = self._eval(quality=q)
        self.assertFalse(rep["script_locked"])
        self.assertIn("fact_reliability_5", rep["failed_checks"])

    def test_originality_violation_blocks(self):
        rep = self._eval(originality={"passed": False, "violations": [{"length": 30}]})
        self.assertFalse(rep["script_locked"])
        self.assertIn("originality", rep["failed_checks"])

    def test_multiple_main_concepts_block(self):
        m = _good_metrics()
        m["main_concept_count"] = 2
        rep = self._eval(metrics=m)
        self.assertFalse(rep["script_locked"])
        self.assertIn("single_main_concept", rep["failed_checks"])

    def test_blocking_issues_block(self):
        rep = self._eval(blocking_issues=["视觉圣经缺失"])
        self.assertFalse(rep["script_locked"])
        self.assertIn("no_blocking_issues", rep["failed_checks"])

    def test_missing_section_is_fail_closed_not_skipped(self):
        with self.assertRaises(ScriptLockError):
            evaluate_script_lock(source=_good_source(), metrics=_good_metrics(),
                                 quality=_good_quality(), originality=None,
                                 blind_review=merge_blind_reviews(_good_reviews()),
                                 blocking_issues=[])

    def test_phase_7_stays_blocked_until_human_approval(self):
        rep = self._eval()
        self.assertEqual(rep["phase_7_status"], "blocked_by_script_approval")
        self.assertFalse(rep["human_approved"])


if __name__ == "__main__":
    unittest.main()
