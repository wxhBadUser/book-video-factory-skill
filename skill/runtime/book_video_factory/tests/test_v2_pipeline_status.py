from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from book_video_factory.host_orchestration import register_action
from book_video_factory.pipeline_runtime import pipeline_status


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
    assert status["stage"] == "locked_script"
    assert status["status"] == "script_locked"
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
