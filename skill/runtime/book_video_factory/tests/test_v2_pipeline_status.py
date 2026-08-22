from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from book_video_factory.host_orchestration import register_action
from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.visual_covenant import (
    covenant_canonical_sha,
    promote_covenant_assets,
    record_visual_covenant_approval,
    verify_visual_covenant,
    world_profile_canonical_sha,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    script_text = "locked narration for pipeline status"
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
    image = project / "03_images_生成图片/covenant/jane.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"covenant-image")
    covenant_payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": name,
        "locked_script_sha256": _sha(script_text),
        "world_profile": {"period": "nineteenth-century Yorkshire", "palette": "muted earth tones"},
        "assets": [{
            "asset_id": "COV_JANE",
            "asset_family": "character:jane",
            "visual_function": "character_portrait",
            "source": "visual_covenant",
            "production_eligible": True,
            "technical_status": "verified",
            "path": "03_images_生成图片/covenant/jane.png",
            "file_sha256": _sha("covenant-image"),
            "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-jane"},
        }],
    }
    covenant_payload["world_profile_sha256"] = world_profile_canonical_sha(covenant_payload["world_profile"])
    covenant_payload["visual_covenant_sha256"] = covenant_canonical_sha(covenant_payload)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant_payload)
    record_visual_covenant_approval(project, reviewer="fixture-reviewer", approved_at="2026-08-22T00:00:00+08:00")
    promote_covenant_assets(project)
    return project


def _generate_action(*, key: str = "release-1:TASK_001:attempt-1", action_id: str = "ACT_0001") -> dict:
    return {
        "schema_version": "host-agent-action.v2",
        "action_id": action_id,
        "release_id": "release-1",
        "project_id": "pilot",
        "action_type": "generate_image",
        "idempotency_key": key,
        "attempt": 1,
        "inputs": [{"kind": "visual_paragraph", "path": "05_director/VISUAL_PARAGRAPH.md", "sha256": _sha("p")}],
        "expected_output": {"asset_id": "SCENE_S01", "asset_sha256": _sha("img")},
        "max_runtime_seconds": 900,
    }


def test_host_action_pending_is_not_a_human_gate(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    register_action(project, _generate_action())
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "host_orchestration"
    assert status["status"] == "host_action_pending"
    assert status["human_review_required"] is False
    assert status["host_action"]["action_id"] == "ACT_0001"


def test_valid_lock_with_no_action_is_script_locked(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "asset_catalog"
    assert status["status"] == "asset_catalog_ready"
    assert status["human_review_required"] is False
    assert "host_action" not in status


def test_blocked_rights_exposes_no_host_action(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
        "schema_version": "source-sanitization-report.v1",
        "rights_status": "in_review",
        "public_release_allowed": False,
        "detected_source_declarations": [],
    })
    register_action(project, _generate_action())
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "locked_script"
    assert status["status"] == "blocked_rights"
    assert "host_action" not in status


def test_next_action_cli_emits_the_only_legal_action(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    register_action(project, _generate_action())
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_full_pipeline.py"
    completed = subprocess.run(
        [sys.executable, str(script), "next-action", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ready"
    assert payload["action"]["action_id"] == "ACT_0001"


def _project_without_covenant(tmp_path: Path) -> Path:
    project = _locked_project(tmp_path)
    (project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json").unlink()
    (project / "manifests/asset_catalog/ASSET_CATALOG.v2.json").unlink()
    (project / "manifests/asset_catalog/ASSET_PROMOTION.v2.jsonl").unlink()
    return project


def test_missing_covenant_is_not_a_human_gate(tmp_path: Path) -> None:
    project = _project_without_covenant(tmp_path)
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "visual_covenant"
    assert status["status"] == "host_action_pending"
    assert status["human_review_required"] is False
    assert status["host_action"]["action_type"] == "plan_visual_covenant"


def test_missing_catalog_auto_promotes_covenant(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    catalog_dir = project / "manifests/asset_catalog"
    (catalog_dir / "ASSET_CATALOG.v2.json").unlink()
    (catalog_dir / "ASSET_PROMOTION.v2.jsonl").unlink()
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "asset_catalog"
    assert status["status"] == "asset_catalog_ready"
    assert status["human_review_required"] is False
    catalog = json.loads(
        (catalog_dir / "ASSET_CATALOG.v2.json").read_text(encoding="utf-8")
    )
    assert [item["asset_id"] for item in catalog["assets"]] == ["COV_JANE"]


def test_stale_catalog_triggers_re_promotion(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    # Simulate a divergent catalog approval by rewriting the covenant with a new asset.
    image2 = project / "03_images_生成图片/covenant/scene.png"
    image2.parent.mkdir(parents=True, exist_ok=True)
    image2.write_bytes(b"scene-image")
    covenant_payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "locked_script_sha256": _sha("locked narration for pipeline status"),
        "world_profile": {"period": "nineteenth-century Yorkshire", "palette": "muted earth tones"},
        "assets": [
            {
                "asset_id": "COV_JANE",
                "asset_family": "character:jane",
                "visual_function": "character_portrait",
                "source": "visual_covenant",
                "production_eligible": True,
                "technical_status": "verified",
                "path": "03_images_生成图片/covenant/jane.png",
                "file_sha256": _sha("covenant-image"),
                "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-jane"},
            },
            {
                "asset_id": "COV_SCENE",
                "asset_family": "location:thrushcross",
                "visual_function": "location_establishing",
                "source": "visual_covenant",
                "production_eligible": True,
                "technical_status": "verified",
                "path": "03_images_生成图片/covenant/scene.png",
                "file_sha256": _sha("scene-image"),
                "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-scene"},
            },
        ],
    }
    covenant_payload["world_profile_sha256"] = world_profile_canonical_sha(covenant_payload["world_profile"])
    covenant_payload["visual_covenant_sha256"] = covenant_canonical_sha(covenant_payload)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant_payload)
    assert verify_visual_covenant(project)["status"] == "visual_covenant_verified"
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "visual_covenant"
    assert status["status"] == "awaiting_visual_covenant_approval"
    assert status["human_review_required"] is True
    record_visual_covenant_approval(project, reviewer="fixture-reviewer", approved_at="2026-08-22T00:00:00+08:00")
    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        status = pipeline_status(project)
    assert status["stage"] == "asset_catalog"
    assert status["status"] == "asset_catalog_ready"
    catalog = json.loads(
        (project / "manifests/asset_catalog/ASSET_CATALOG.v2.json").read_text(encoding="utf-8")
    )
    assert {item["asset_id"] for item in catalog["assets"]} == {"COV_JANE", "COV_SCENE"}
