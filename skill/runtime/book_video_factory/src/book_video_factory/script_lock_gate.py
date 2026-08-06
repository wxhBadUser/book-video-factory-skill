"""正式脚本锁定门禁（fail-closed）。

汇总以下证据，任何一项不达标或缺失都不得锁定脚本：
1. 来源等级：level=A / is_full_text / research_status=complete
2. 篇幅与节奏：字数区间 / 时长区间 / 中点位置 / 理论占比 / 单一主概念
3. 内容质量：总分 >= 66/75、任一项 >= 4、事实可靠性 = 5
4. 原创性：无超阈值连续重合
5. 独立盲审：两名互相独立的评审，且结论非 reject/revise
6. 无 blocking_issues

即使全部通过，phase_7 仍保持 blocked_by_script_approval，
必须由人工确认脚本质量后才放行——机器不替人签字。
"""
from __future__ import annotations

import datetime as _dt
from typing import Mapping, Sequence

__all__ = [
    "CHARS_RANGE",
    "MINUTES_RANGE",
    "MIDPOINT_RANGE",
    "THEORY_MAX_RATIO",
    "QUALITY_TOTAL_MIN",
    "QUALITY_ITEM_MIN",
    "ScriptLockError",
    "evaluate_script_lock",
]

CHARS_RANGE = (3600, 5400)
MINUTES_RANGE = (15.0, 23.0)
PILOT_CHARS_RANGE = (232, 348)
PILOT_MINUTES_RANGE = (1.0, 1.5)
MIDPOINT_RANGE = (0.40, 0.55)
THEORY_MAX_RATIO = 0.30
QUALITY_TOTAL_MIN = 66
QUALITY_MAX = 75
QUALITY_ITEM_MIN = 4
FACT_RELIABILITY_KEY = "fact_reliability"

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

    # 2. 篇幅与节奏
    if qualification_scope not in {"production", "hbg-parity-pilot"}:
        raise ScriptLockError(f"unsupported qualification_scope: {qualification_scope}")
    chars_range = PILOT_CHARS_RANGE if qualification_scope == "hbg-parity-pilot" else CHARS_RANGE
    minutes_range = PILOT_MINUTES_RANGE if qualification_scope == "hbg-parity-pilot" else MINUTES_RANGE
    chars = metrics.get("total_spoken_chars")
    add("chars_range", chars is not None and chars_range[0] <= chars <= chars_range[1],
        {"value": chars, "range": list(chars_range)})

    minutes = metrics.get("estimated_minutes")
    add("duration_range",
        minutes is not None and minutes_range[0] <= minutes <= minutes_range[1],
        {"value": minutes, "range": list(minutes_range)})

    mid = metrics.get("midpoint_ratio")
    add("midpoint_range",
        mid is not None and MIDPOINT_RANGE[0] <= mid <= MIDPOINT_RANGE[1],
        {"value": mid, "range": list(MIDPOINT_RANGE)})

    theory = metrics.get("theory_ratio")
    add("theory_ratio_max", theory is not None and theory <= THEORY_MAX_RATIO,
        {"value": theory, "max": THEORY_MAX_RATIO})

    add("single_main_concept", metrics.get("main_concept_count") == 1,
        metrics.get("main_concept_count"))

    # 3. 内容质量
    items: Mapping[str, int] = quality.get("items") or {}
    total = quality.get("total")
    add("quality_total_min_66", total is not None and total >= QUALITY_TOTAL_MIN,
        {"value": total, "min": QUALITY_TOTAL_MIN, "max": quality.get("max_total", QUALITY_MAX)})

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
        "checks": checks,
        "failed_checks": failed,
        "passed_checks": [n for n in checks if n not in failed],
        "blocking_issues": all_blocking,
        "human_approved": bool(human_approved),
        # 机器通过 ≠ 人工验收；Phase 7 始终等待人工确认
        "phase_7_status": ("ready_for_human_approval" if locked and human_approved
                           else "blocked_by_script_approval"),
        "evaluated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
