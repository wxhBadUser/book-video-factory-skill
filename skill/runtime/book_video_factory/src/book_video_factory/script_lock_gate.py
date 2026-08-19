"""正式脚本锁定门禁（fail-closed）。

Phase 1 refactoring: quality thresholds are now the single authoritative
configuration. Duration is no longer a hard gate — target_duration_minutes
is decided by the Creative Route and only triggers a warning if outside
the broad 11.5–22.5 min production band. CPM is diagnostic only.

Quality tiers (single source of truth):
  0–65:  FAIL
  66–71: REVIEWER_READY (machine passes, human may review)
  72–75: GOLD_READY (required to enter video production)

Production entry requires GOLD_READY + fact_reliability=5 + key items >=4
+ no fact blocking issues + 100% verifiable direct quotes.
"""
from __future__ import annotations

import datetime as _dt
from typing import Mapping, Sequence

__all__ = [
    "QUALITY_FAIL_MAX",
    "QUALITY_REVIEWER_MIN",
    "QUALITY_REVIEWER_MAX",
    "QUALITY_GOLD_MIN",
    "QUALITY_MAX",
    "QUALITY_ITEM_MIN",
    "PRODUCTION_DURATION_BAND_MIN",
    "PRODUCTION_DURATION_BAND_MAX",
    "FACT_RELIABILITY_KEY",
    "ScriptLockError",
    "quality_tier",
    "evaluate_script_lock",
]

# --- Single authoritative quality thresholds ---
QUALITY_FAIL_MAX = 65
QUALITY_REVIEWER_MIN = 66
QUALITY_REVIEWER_MAX = 71
QUALITY_GOLD_MIN = 72
QUALITY_MAX = 75
QUALITY_ITEM_MIN = 4

# --- Duration: soft advisory band, not a hard gate ---
# Creative Route decides target_duration_minutes. Outside this band is a
# warning only; the script is never rejected for duration alone.
PRODUCTION_DURATION_BAND_MIN = 11.5
PRODUCTION_DURATION_BAND_MAX = 22.5

MIDPOINT_RANGE = (0.40, 0.55)
THEORY_MAX_RATIO = 0.30
FACT_RELIABILITY_KEY = "fact_reliability"

# Legacy pilot ranges retained for hbg-parity-pilot qualification scope only.
PILOT_CHARS_RANGE = (232, 348)
PILOT_MINUTES_RANGE = (1.0, 1.5)


def quality_tier(total_score: float | int | None) -> str:
    """Return the quality tier for a total score.

    FAIL: score <= 65 or missing
    REVIEWER_READY: 66 <= score <= 71
    GOLD_READY: score >= 72
    """
    if total_score is None:
        return "FAIL"
    try:
        score = float(total_score)
    except (TypeError, ValueError):
        return "FAIL"
    if score >= QUALITY_GOLD_MIN:
        return "GOLD_READY"
    if score >= QUALITY_REVIEWER_MIN:
        return "REVIEWER_READY"
    return "FAIL"

# source_ingestion 对 Level A 产出的状态词是 "ready"；改造计划书里写的是 "complete"。
# 两者语义相同（研究来源已完备，可以进入写作），这里同时接受，避免因词汇不一致
# 而误判。真正应当拦截的是 limited / insufficient_source。
RESEARCH_COMPLETE_STATES = ("ready", "complete")
RESEARCH_BLOCKING_STATES = ("limited", "insufficient_source")


class ScriptLockError(RuntimeError):
    """门禁输入缺失——缺证据即视为不通过，不得跳过检查。"""


def _require(name: str, value) -> None:
    if value is None:
        raise ScriptLockError(
            f"脚本锁定门禁缺少必需证据：{name}。缺失证据一律 fail-closed，"
            "不得按'未提供即通过'处理。"
        )


def evaluate_script_lock(
    *,
    source: Mapping,
    metrics: Mapping,
    quality: Mapping,
    originality: Mapping | None,
    blind_review: Mapping | None,
    blocking_issues: Sequence[str] | None,
    human_approved: bool = False,
    qualification_scope: str = "production",
) -> dict:
    _require("source", source)
    _require("metrics", metrics)
    _require("quality", quality)
    _require("originality", originality)
    _require("blind_review", blind_review)
    _require("blocking_issues", blocking_issues)

    checks: dict[str, dict] = {}

    def add(name: str, passed: bool, detail) -> None:
        checks[name] = {"passed": bool(passed), "detail": detail}

    # 1. 来源等级
    add("source_level_A", source.get("level") == "A", source.get("level"))
    add("full_text", bool(source.get("is_full_text")), source.get("is_full_text"))
    research_status = source.get("research_status")
    add("research_complete", research_status in RESEARCH_COMPLETE_STATES,
        {"value": research_status, "accepted": list(RESEARCH_COMPLETE_STATES),
         "blocking": list(RESEARCH_BLOCKING_STATES)})

    # 2. 篇幅与节奏 (Phase 1: duration is advisory, not a hard gate)
    if qualification_scope not in {"production", "hbg-parity-pilot"}:
        raise ScriptLockError(f"unsupported qualification_scope: {qualification_scope}")

    chars = metrics.get("total_spoken_chars")
    # Character count is recorded for diagnostics; no hard range is enforced
    # because the Creative Route decides target length.
    add("chars_recorded", chars is not None, {"value": chars})

    minutes = metrics.get("estimated_minutes")
    if qualification_scope == "hbg-parity-pilot":
        # Pilot scope keeps its short-duration contract.
        in_band = minutes is not None and PILOT_MINUTES_RANGE[0] <= minutes <= PILOT_MINUTES_RANGE[1]
        add("duration_range", in_band,
            {"value": minutes, "range": list(PILOT_MINUTES_RANGE), "scope": "pilot"})
    else:
        # Production: duration outside 11.5–22.5 min is a WARNING only.
        # The script is never rejected for duration alone.
        in_band = (
            minutes is not None
            and PRODUCTION_DURATION_BAND_MIN <= minutes <= PRODUCTION_DURATION_BAND_MAX
        )
        add("duration_advisory", True,
            {"value": minutes,
             "advisory_band": [PRODUCTION_DURATION_BAND_MIN, PRODUCTION_DURATION_BAND_MAX],
             "within_band": in_band,
             "note": "duration is advisory; Creative Route sets the target"})

    mid = metrics.get("midpoint_ratio")
    add("midpoint_range",
        mid is not None and MIDPOINT_RANGE[0] <= mid <= MIDPOINT_RANGE[1],
        {"value": mid, "range": list(MIDPOINT_RANGE)})

    theory = metrics.get("theory_ratio")
    add("theory_ratio_max", theory is not None and theory <= THEORY_MAX_RATIO,
        {"value": theory, "max": THEORY_MAX_RATIO})

    add("single_main_concept", metrics.get("main_concept_count") == 1,
        metrics.get("main_concept_count"))

    # 3. 内容质量 — single authoritative thresholds
    items: Mapping[str, int] = quality.get("items") or {}
    total = quality.get("total")
    tier = quality_tier(total)
    add("quality_gold_ready", tier == "GOLD_READY",
        {"value": total, "tier": tier,
         "gold_min": QUALITY_GOLD_MIN, "max": QUALITY_MAX})

    below = {k: v for k, v in items.items() if v < QUALITY_ITEM_MIN}
    add("quality_each_item_min_4", bool(items) and not below,
        {"below_threshold": below, "min": QUALITY_ITEM_MIN})

    add("fact_reliability_5", items.get(FACT_RELIABILITY_KEY) == 5,
        items.get(FACT_RELIABILITY_KEY))

    # 4. 原创性
    add("originality", bool(originality.get("passed")),
        {"longest_overlap": originality.get("longest_overlap"),
         "violations": len(originality.get("violations") or [])})

    # 5. 独立盲审
    add("blind_review_independent",
        bool(blind_review.get("independent")) and blind_review.get("reviewer_count", 0) >= 2,
        {"reviewers": blind_review.get("reviewers"),
         "count": blind_review.get("reviewer_count")})
    add("blind_review_verdict", blind_review.get("verdict") == "pass",
        blind_review.get("verdict"))

    # 6. 阻塞项
    all_blocking = list(blocking_issues) + list(blind_review.get("blocking_issues") or [])
    add("no_blocking_issues", not all_blocking, all_blocking)

    failed = [name for name, c in checks.items() if not c["passed"]]
    locked = not failed

    return {
        "schema_version": "script-lock-gate.v1",
        "qualification_scope": qualification_scope,
        "script_locked": locked,
        "quality_tier": quality_tier(quality.get("total")),
        "checks": checks,
        "failed_checks": failed,
        "passed_checks": [n for n in checks if n not in failed],
        "blocking_issues": all_blocking,
        "human_approved": bool(human_approved),
        # 机器通过 ≠ 人工验收；Gate 1 始终等待人工确认脚本
        "gate_1_status": ("ready_for_human_approval" if locked and human_approved
                          else "blocked_by_script_approval"),
        "evaluated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
