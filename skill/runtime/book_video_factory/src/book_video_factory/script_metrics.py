"""Deterministic metrics for a three-version narrator essay script.

Phase 1 refactoring: BASELINE_CPM and estimated_minutes are DIAGNOSTIC ONLY.
They never gate script lock or production entry. Real timing comes from the
MiniMax provider word timestamps; the CPM estimate is only a rough planning
aid for the Creative Route.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .narrator_essay_contracts import ContractError, validate_narrator_essay_script

# Diagnostic baseline for rough planning only. NOT a production gate.
BASELINE_CPM = 232
_SPOKEN_CHAR = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")


class ScriptMetricsError(ValueError):
    """The script cannot produce trustworthy deterministic metrics."""


def spoken_char_count(text: str) -> int:
    return len(_SPOKEN_CHAR.findall(text or ""))


def compute_script_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute reproducible metrics from the release section graph."""
    data = dict(payload)
    try:
        validate_narrator_essay_script(data)
    except ContractError as error:
        raise ScriptMetricsError(str(error)) from error

    release = data["release_version"]
    sections = release["sections"]
    release_text = release["text"]
    if data.get("script_text") != release_text:
        raise ScriptMetricsError("script_text must equal release_version.text")

    section_counts: dict[str, int] = {}
    functions: dict[str, str] = {}
    for section in sections:
        section_id = section["section_id"]
        section_counts[section_id] = spoken_char_count(section["text"])
        functions[section_id] = section["narrative_function"]

    total = sum(section_counts.values())
    if total <= 0:
        raise ScriptMetricsError("release script contains no spoken characters")
    midpoint_id = data["midpoint_section_id"]
    before_midpoint = 0
    found_midpoint = False
    theory_chars = 0
    for section in sections:
        sid = section["section_id"]
        if sid == midpoint_id:
            found_midpoint = True
        elif not found_midpoint:
            before_midpoint += section_counts[sid]
        if section["narrative_function"] == "theory":
            theory_chars += section_counts[sid]
    if not found_midpoint:
        raise ScriptMetricsError("midpoint_section_id is absent from release section graph")

    return {
        "schema_version": "script-metrics.v1",
        "total_spoken_chars": total,
        "estimated_minutes": round(total / BASELINE_CPM, 3),
        "baseline_cpm": BASELINE_CPM,
        "midpoint_section_id": midpoint_id,
        "midpoint_ratio": round(before_midpoint / total, 6),
        "theory_ratio": round(theory_chars / total, 6),
        "main_concept_count": data["main_concept_count"],
        "section_count": len(sections),
        "section_spoken_chars": section_counts,
        "section_functions": functions,
    }
