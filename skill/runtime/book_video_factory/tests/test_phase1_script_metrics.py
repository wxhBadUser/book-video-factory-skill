from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.narrator_essay_contracts import ContractError, validate_narrator_essay_script
from book_video_factory.script_metrics import ScriptMetricsError, compute_script_metrics


def _section(section_id: str, function: str, text: str) -> dict:
    return {"section_id": section_id, "narrative_function": function, "text": text}


def _payload() -> dict:
    sections = [
        _section("S01", "hook", "老人已经连续八十四天没有捕到鱼了。" * 20),
        _section("S02", "world_setup", "海边的人都觉得他的运气已经坏透。" * 25),
        _section("S03", "desire", "可他仍然把绳索一圈圈整理好，准备再次出海。" * 25),
        _section("S04", "midpoint_requestion", "当大鱼终于咬钩，问题却变成了他能不能活着把它带回去。" * 25),
        _section("S05", "reinterpretation", "鲨鱼夺走鱼肉以后，他才看见胜利从来不是完整占有。" * 20),
        _section("S06", "theory", "尊严不是结果，而是在结果崩塌后仍不否认自己的行动。" * 15),
        _section("S07", "ending_image", "老人睡着了，又梦见少年时代非洲海滩上的狮子。" * 15),
    ]
    release = {"sections": deepcopy(sections), "text": "".join(item["text"] for item in sections)}
    performance_sections = [
        {**item, "direction": {"energy": 0.6, "pause_after": 0.2}}
        for item in deepcopy(sections)
    ]
    audit_sections = [
        {**item, "source_ids": [f"F{index:03d}"]}
        for index, item in enumerate(deepcopy(sections), start=1)
    ]
    return {
        "schema_version": "script.narrator-essay.v1",
        "book_id": "old-man-and-the-sea",
        "release_id": "r1",
        "narrator_ratio": 0.90,
        "dialogue_ratio": 0.10,
        "duration_target_seconds": 1080,
        "main_concept_count": 1,
        "has_midpoint_requestion": True,
        "has_evidence_reinterpretation": True,
        "reinterpretation_source_ids": ["F005"],
        "midpoint_section_id": "S04",
        "performance_version": {"sections": performance_sections, "text": release["text"]},
        "release_version": release,
        "audit_version": {"sections": audit_sections, "text": release["text"]},
        "script_text": release["text"],
    }


def test_release_text_is_required_and_has_no_engineering_tags() -> None:
    payload = _payload()
    payload["release_version"]["sections"][0]["text"] = "【压低声音】老人八十四天没有鱼。"
    payload["release_version"]["text"] = "".join(
        item["text"] for item in payload["release_version"]["sections"]
    )
    payload["script_text"] = payload["release_version"]["text"]
    with pytest.raises(ContractError, match="engineering tag"):
        validate_narrator_essay_script(payload)


def test_performance_and_audit_versions_are_required() -> None:
    payload = _payload()
    del payload["performance_version"]
    with pytest.raises(ContractError, match="performance_version"):
        validate_narrator_essay_script(payload)


def test_all_versions_bind_to_same_section_ids() -> None:
    payload = _payload()
    payload["audit_version"]["sections"][2]["section_id"] = "OTHER"
    with pytest.raises(ContractError, match="section graph"):
        validate_narrator_essay_script(payload)


def test_metrics_count_spoken_chars_and_use_232_cpm_baseline() -> None:
    metrics = compute_script_metrics(_payload())
    assert metrics["total_spoken_chars"] > 1000
    assert metrics["estimated_minutes"] == pytest.approx(
        metrics["total_spoken_chars"] / 232.0, abs=0.001
    )
    assert metrics["baseline_cpm"] == 232


def test_midpoint_ratio_comes_from_section_boundaries() -> None:
    payload = _payload()
    metrics = compute_script_metrics(payload)
    section_counts = metrics["section_spoken_chars"]
    expected = sum(section_counts[key] for key in ("S01", "S02", "S03")) / metrics["total_spoken_chars"]
    assert metrics["midpoint_ratio"] == pytest.approx(expected, abs=0.0001)


def test_theory_ratio_comes_from_theory_sections() -> None:
    metrics = compute_script_metrics(_payload())
    expected = metrics["section_spoken_chars"]["S06"] / metrics["total_spoken_chars"]
    assert metrics["theory_ratio"] == pytest.approx(expected, abs=0.0001)


def test_script_text_must_equal_release_text() -> None:
    payload = _payload()
    payload["script_text"] += "多出来的一句"
    with pytest.raises(ScriptMetricsError, match="script_text"):
        compute_script_metrics(payload)


def test_speed_contract_matches_approved_profile() -> None:
    manifest = json.loads(
        (ROOT / "config" / "narrator_essay_context_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["speed_contract_cpm"] == {
        "average": [220, 245],
        "hook": [245, 285],
        "narrative": [225, 250],
        "confession_death_reinterpretation": [190, 225],
        "theory": [210, 235],
        "final_line": [185, 215],
    }
