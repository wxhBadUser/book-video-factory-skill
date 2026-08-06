from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase3_fixture_factory import build_phase3_input
from phase2_fixture_factory import build_phase2_project, write_json
from book_video_factory.hbg_bridge.compiler import compile_hbg_bridge
from book_video_factory.orientation import (
    OrientationError,
    canvas_for_orientation,
    validate_orientation_contract,
)
from book_video_factory.project import initialize_project
from book_video_factory.reference_visuals.catalog import load_reference_catalog
from book_video_factory.visual_stage.contracts import validate_visual_stage_input
from book_video_factory.visual_stage.prompts import compile_visual_task_prompts


def test_landscape_remains_the_default(tmp_path: Path) -> None:
    project = initialize_project(tmp_path, "landscape", "样书", "作者")
    contract = json.loads((project / "project.json").read_text(encoding="utf-8"))
    bridge = json.loads(
        (project / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["workflow"]["orientation"] == "landscape"
    assert contract["workflow"]["release_profile_id"] == "book-classic-narrator-hbg-16x9-v1"
    assert bridge["orientation"] == "landscape"
    assert canvas_for_orientation("landscape") == {
        "width": 1920,
        "height": 1080,
        "orientation": "landscape",
    }


def test_portrait_requires_explicit_activation_and_propagates_to_visual_tasks(
    tmp_path: Path,
) -> None:
    project = initialize_project(
        tmp_path, "portrait", "样书", "作者", orientation="portrait"
    )
    contract = json.loads((project / "project.json").read_text(encoding="utf-8"))
    bridge = json.loads(
        (project / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["workflow"]["orientation"] == "portrait"
    assert contract["workflow"]["release_profile_id"] == "book-classic-narrator-hbg-9x16-v1"
    assert bridge["orientation"] == "portrait"

    catalog = load_reference_catalog()
    payload = build_phase3_input()
    payload["orientation"] = "portrait"
    normalized = validate_visual_stage_input(
        payload,
        phase2_characters=[
            {"character_id": "C001", "name": "圣地亚哥", "anchor_status": "pending"}
        ],
        catalog=catalog,
    )
    tasks = compile_visual_task_prompts(normalized, catalog)
    assert {tuple(task["canvas"].values()) for task in tasks} == {
        (1080, 1920, "portrait")
    }
    assert all("9:16 portrait" in task["prompt"] for task in tasks)
    assert all("16:9" not in task["prompt"] for task in tasks)


def test_mixed_orientation_is_rejected() -> None:
    with pytest.raises(OrientationError, match="contradicts"):
        validate_orientation_contract(
            "portrait", {"width": 1920, "height": 1080, "orientation": "landscape"}
        )


def test_portrait_bridge_selects_the_pristine_hbg_portrait_style(tmp_path: Path) -> None:
    project, _inputs, _content, bridge_input = build_phase2_project(
        tmp_path, orientation="portrait"
    )
    input_path = tmp_path / "portrait-bridge.json"
    write_json(input_path, bridge_input)
    result = compile_hbg_bridge(project, input_path)
    style = json.loads((project / "HBG_STYLE.json").read_text(encoding="utf-8"))
    stage = json.loads(result.stage_manifest_path.read_text(encoding="utf-8"))
    assert style["orientation"] == "portrait"
    assert style["canvas"] == {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "videoBitrate": "8M",
    }
    assert style["captions"]["bottom"] == 280
    assert style["opening"]["layout"]["titleCardWidth"] == 930
    assert stage["release_profile_id"] == "book-classic-narrator-hbg-9x16-v1"
