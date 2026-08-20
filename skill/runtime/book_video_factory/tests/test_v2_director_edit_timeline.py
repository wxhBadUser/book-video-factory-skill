"""V2 low-density Director resolver tests (Stage 4, offline)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from book_video_factory.director_stage.contracts import (
    DirectorStageV2Error,
    EDIT_TIMELINE_REL,
    resolve_edit_timeline,
)
from book_video_factory.manifests import sha256_file
from book_video_factory.visual_covenant import (
    covenant_canonical_sha,
    promote_covenant_assets,
)


def _sha(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _assert_fails_closed(fn, needle: str) -> None:
    with pytest.raises(DirectorStageV2Error) as excinfo:
        fn()
    assert needle in str(excinfo.value)


def _locked_project(tmp_path: Path, *, name: str = "pilot") -> Path:
    project = tmp_path / "warehouse" / "projects" / name
    _write_json(project / "01_research_资料搜集/SOURCE_MANIFEST.json", {
        "schema_version": "1.0",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "source_dir": "source",
        "files": [],
    })
    _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
        "schema_version": "source-sanitization-report.v1",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "detected_source_declarations": [],
    })
    script_text = "director narration"
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script_text),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    return project


def _asset_file(project: Path, relative: str, content: bytes = b"asset-png-bytes") -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _eligible_asset(asset_id: str, *, relative: str, family: str = "character:jane", func: str = "character_portrait") -> dict:
    return {
        "asset_id": asset_id,
        "asset_family": family,
        "visual_function": func,
        "source": "visual_covenant",
        "production_eligible": True,
        "technical_status": "verified",
        "path": relative,
        "file_sha256": _sha("asset-png-bytes"),
        "provenance": {"provider": "host-imagegen", "tool_call_id": f"call-{asset_id}"},
    }


def _promote(project: Path, *assets: dict) -> None:
    if not assets:
        assets = (_eligible_asset("COV_ASSET_02", relative="03_images_生成图片/covenant/jane.png"),)
    for asset in assets:
        _asset_file(project, asset["path"])
    payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "locked_script_sha256": _sha("locked"),
        "assets": list(assets),
    }
    payload["visual_covenant_sha256"] = covenant_canonical_sha(payload)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", payload)
    promote_covenant_assets(project)


def _audio_timeline(project: Path, *, duration: float = 24.0) -> str:
    audio = {
        "schema_version": "audio-timeline.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "narration_master_path": "04_audio/master.wav",
        "narration_master_sha256": _sha("master"),
        "provider_vtt_path": "04_audio/provider.vtt",
        "provider_vtt_sha256": _sha("vtt"),
        "caption_timeline_path": "04_audio/CAPTION_TIMELINE.json",
        "caption_timeline_sha256": _sha("captions"),
        "timing_authority": "provider",
        "narration_duration_seconds": duration,
        "bgm": {"bgm_mode": "none"},
    }
    _write_json(project / "04_audio/AUDIO_TIMELINE.v2.json", audio)
    return sha256_file(project / "04_audio/AUDIO_TIMELINE.v2.json")


def _write_vp_and_decisions(project: Path, *, audio_sha: str, paragraphs: list[dict], decisions: list[dict]) -> None:
    _write_json(project / "04_director/VISUAL_PARAGRAPHS.json", {
        "schema_version": "visual-paragraphs.v1",
        "release_id": "release-1",
        "project_id": "pilot",
        "audio_timeline_sha256": audio_sha,
        "narration_duration_seconds": 24.0,
        "paragraphs": paragraphs,
    })
    _write_json(project / "04_director/EDIT_DECISIONS.v2.json", {
        "schema_version": "edit-decisions.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "visual_paragraphs_sha256": sha256_file(project / "04_director/VISUAL_PARAGRAPHS.json"),
        "decisions": decisions,
    })


def _vp(paragraph_id: str, start: float, end: float, *caption_ids: str) -> dict:
    return {
        "paragraph_id": paragraph_id,
        "start": start,
        "end": end,
        "source_caption_ids": list(caption_ids) or ["C1"],
        "visual_intent": "stable hold of the established portrait",
        "mood": "steadfast",
    }


def _decision(paragraph_id: str, decision: str, asset_id: str | None = None) -> dict:
    item = {"paragraph_id": paragraph_id, "decision": decision}
    if asset_id is not None:
        item["asset_id"] = asset_id
    return item


def _events(project: Path) -> list[dict]:
    return json.loads((project / EDIT_TIMELINE_REL).read_text(encoding="utf-8"))["events"]


def test_first_hold_current_rejected(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[_vp("VP_001", 0.0, 12.0), _vp("VP_002", 12.0, 24.0)],
        decisions=[_decision("VP_001", "hold_current"), _decision("VP_002", "generate_new", "COV_ASSET_02")],
    )
    _assert_fails_closed(lambda: resolve_edit_timeline(project), "hold_current")


def test_hold_current_resolves_to_previous_asset(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[_vp("VP_001", 0.0, 8.0), _vp("VP_002", 8.0, 16.0), _vp("VP_003", 16.0, 24.0)],
        decisions=[
            _decision("VP_001", "generate_new", "COV_ASSET_02"),
            _decision("VP_002", "hold_current"),
            _decision("VP_003", "hold_current"),
        ],
    )
    timeline = resolve_edit_timeline(project)
    assert timeline["status"] == "edit_timeline_resolved"
    events = _events(project)
    # Both the explicit generate and the following holds resolve to the same
    # concrete asset_id; no hold_current or "previous asset" lookup survives.
    assert [item["asset_id"] for item in events] == ["COV_ASSET_02"]


def test_every_event_has_explicit_asset_and_no_gap_overlap(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[
            _vp("VP_001", 0.0, 8.0),
            _vp("VP_002", 8.0, 16.0),
            _vp("VP_003", 16.0, 24.0),
        ],
        decisions=[
            _decision("VP_001", "generate_new", "COV_ASSET_02"),
            _decision("VP_002", "hold_current"),
            _decision("VP_003", "hold_current"),
        ],
    )
    result = resolve_edit_timeline(project)
    payload = json.loads((project / EDIT_TIMELINE_REL).read_text(encoding="utf-8"))
    events = payload["events"]
    assert result["event_count"] == 1
    assert all(item["asset_id"] for item in events)
    assert all(item["asset_sha256"] for item in events)
    assert abs(events[0]["start"] - 0.0) < 1e-3
    assert abs(events[0]["end"] - 24.0) < 1e-3
    assert payload["narration_duration_seconds"] == 24.0


def test_same_asset_reused_non_contiguous(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(
        project,
        _eligible_asset("COV_A1", relative="03_images_生成图片/covenant/a1.png"),
        _eligible_asset("COV_B1", relative="03_images_生成图片/covenant/b1.png"),
    )
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[
            _vp("VP_001", 0.0, 8.0),
            _vp("VP_002", 8.0, 16.0),
            _vp("VP_003", 16.0, 24.0),
        ],
        decisions=[
            _decision("VP_001", "generate_new", "COV_A1"),
            _decision("VP_002", "generate_new", "COV_B1"),
            _decision("VP_003", "reuse_asset", "COV_A1"),
        ],
    )
    resolve_edit_timeline(project)
    events = _events(project)
    assert len(events) == 3
    assert [item["asset_id"] for item in events] == ["COV_A1", "COV_B1", "COV_A1"]
    assert events[0]["edit_id"] != events[2]["edit_id"]


def test_overlap_or_gap_fails_closed(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[_vp("VP_001", 0.0, 15.0), _vp("VP_002", 10.0, 24.0)],
        decisions=[
            _decision("VP_001", "generate_new", "COV_ASSET_02"),
            _decision("VP_002", "hold_current"),
        ],
    )
    _assert_fails_closed(lambda: resolve_edit_timeline(project), "overlap")
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[_vp("VP_001", 0.0, 10.0), _vp("VP_002", 12.0, 24.0)],
        decisions=[
            _decision("VP_001", "generate_new", "COV_ASSET_02"),
            _decision("VP_002", "hold_current"),
        ],
    )
    _assert_fails_closed(lambda: resolve_edit_timeline(project), "gap")


def test_stale_asset_hash_fails_closed(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=[_vp("VP_001", 0.0, 24.0)],
        decisions=[_decision("VP_001", "generate_new", "COV_ASSET_02")],
    )
    asset_path = project / "03_images_生成图片/covenant/jane.png"
    asset_path.write_bytes(b"tampered-asset-bytes")
    # The runtime re-verifies host paths/files and recomputes SHA-256. Tampering
    # the promoted asset must fail closed, whether the covenant/catalog
    # integrity check or the resolver's own recompute catches it first.
    _assert_fails_closed(lambda: resolve_edit_timeline(project), "integrity")


def test_eight_captions_do_not_force_eight_events(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _promote(project)
    audio_sha = _audio_timeline(project)
    paragraphs = [
        _vp("VP_001", 0.0, 12.0, "C1", "C2", "C3", "C4"),
        _vp("VP_002", 12.0, 24.0, "C5", "C6", "C7", "C8"),
    ]
    _write_vp_and_decisions(
        project,
        audio_sha=audio_sha,
        paragraphs=paragraphs,
        decisions=[
            _decision("VP_001", "generate_new", "COV_ASSET_02"),
            _decision("VP_002", "hold_current"),
        ],
    )
    result = resolve_edit_timeline(project)
    events = _events(project)
    assert len(events) == 1
    assert result["event_count"] == 1
    assert events[0]["source_paragraph_ids"] == ["VP_001", "VP_002"]


def test_schemas_are_closed_and_versioned() -> None:
    schemas = Path(__file__).resolve().parents[1] / "schemas"
    expected = {
        "visual_paragraphs.v1.schema.json": "visual-paragraphs.v1",
        "edit_decisions.v2.schema.json": "edit-decisions.v2",
        "edit_timeline.v2.schema.json": "edit-timeline.v2",
    }
    for filename, schema_version in expected.items():
        payload = json.loads((schemas / filename).read_text(encoding="utf-8"))
        assert payload["additionalProperties"] is False
        assert "schema_version" in payload["properties"]
        assert payload["properties"]["schema_version"]["const"] == schema_version
