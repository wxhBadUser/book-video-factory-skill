"""独立盲审。

两名评审各自独立打分，互相看不到对方的结论：
- reviewer_literary：文学性、证据密度、主播声音一致性
- reviewer_retention：钩子、中点、翻案、结尾的留存表现

合并规则（刻意保守）：
- 少于两名评审 → 抛错。评审身份重复 → 视为不独立，抛错。
- 共同评分项差距 >= DISAGREEMENT_DELTA 时标 disagreement，
  **不取平均掩盖分歧**；只要有人给 revise，合并结论就是 revise。
"""
from __future__ import annotations

import datetime as _dt
from typing import Mapping, Sequence

__all__ = [
    "REVIEWER_LITERARY",
    "REVIEWER_RETENTION",
    "DISAGREEMENT_DELTA",
    "VALID_VERDICTS",
    "BlindReviewError",
    "new_review",
    "merge_blind_reviews",
]

REVIEWER_LITERARY = "reviewer_literary"
REVIEWER_RETENTION = "reviewer_retention"
DISAGREEMENT_DELTA = 2
VALID_VERDICTS = ("pass", "revise", "reject")


class BlindReviewError(RuntimeError):
    """盲审配置或独立性不满足要求。"""


def new_review(
    reviewer_id: str,
    scores: Mapping[str, int],
    *,
    verdict: str,
    notes: str = "",
    blocking_issues: Sequence[str] = (),
) -> dict:
    if reviewer_id not in (REVIEWER_LITERARY, REVIEWER_RETENTION):
        raise BlindReviewError(f"未知评审身份：{reviewer_id}")
    if verdict not in VALID_VERDICTS:
        raise BlindReviewError(f"非法结论：{verdict}，应为 {VALID_VERDICTS}")
    if not scores:
        raise BlindReviewError(f"{reviewer_id} 未给出任何评分项")
    for key, val in scores.items():
        if not isinstance(val, int) or not 0 <= val <= 5:
            raise BlindReviewError(f"{reviewer_id}.{key} 评分应为 0-5 整数，得到 {val!r}")
    return {
        "reviewer_id": reviewer_id,
        "scores": dict(scores),
        "verdict": verdict,
        "notes": notes,
        "blocking_issues": list(blocking_issues),
        "reviewed_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }


def merge_blind_reviews(reviews: Sequence[Mapping]) -> dict:
    if len(reviews) < 2:
        raise BlindReviewError(
            f"独立盲审至少需要两名评审，当前 {len(reviews)} 名——"
            "单人评审不构成独立性。"
        )
    ids = [r["reviewer_id"] for r in reviews]
    if len(set(ids)) != len(ids):
        raise BlindReviewError(f"评审身份重复 {ids}：同一评审两次不构成独立盲审。")

    # 共同评分项的分歧
    key_sets = [set(r["scores"]) for r in reviews]
    shared = set.intersection(*key_sets) if key_sets else set()
    disagreements: dict[str, dict] = {}
    for key in sorted(shared):
        vals = {r["reviewer_id"]: r["scores"][key] for r in reviews}
        spread = max(vals.values()) - min(vals.values())
        if spread >= DISAGREEMENT_DELTA:
            disagreements[key] = {"scores": vals, "spread": spread}

    verdicts = [r["verdict"] for r in reviews]
    if "reject" in verdicts:
        merged_verdict = "reject"
    elif "revise" in verdicts:
        merged_verdict = "revise"
    else:
        merged_verdict = "pass"

    blocking: list[str] = []
    for r in reviews:
        blocking.extend(r.get("blocking_issues", []))

    return {
        "schema_version": "blind-review.v1",
        "reviewer_count": len(reviews),
        "reviewers": ids,
        "independent": True,
        "reviews": [dict(r) for r in reviews],
        "shared_keys": sorted(shared),
        "has_disagreement": bool(disagreements),
        "disagreements": disagreements,
        "verdict": merged_verdict,
        "blocking_issues": blocking,
        "merged_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
