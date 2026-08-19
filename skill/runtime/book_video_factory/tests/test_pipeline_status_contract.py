from __future__ import annotations

import json
import subprocess
import sys
from unittest import mock
from pathlib import Path

from test_generation_scheduler import build_project

from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.project import initialize_project
from book_video_factory.production_visuals.scheduler import (
    finalize_generation_wave,
    next_generation_wave,
    plan_generation_run,
)


REQUIRED_STATUS_FIELDS = {
    "current_gate",
    "unique_next_action",
    "exact_cli",
    "ready_generation_wave",
    "concurrency_limit",
    "retry_jobs",
    "human_review_required",
    "blockers",
}


def test_status_reports_ready_wave_concurrency_and_retry_jobs(tmp_path: Path) -> None:
    project = build_project(tmp_path)
    plan_generation_run(project, concurrency=10)
    first = next_generation_wave(project)
    finalize_generation_wave(
        project,
        [
            (
                {"job_id": job["job_id"], "status": "failed", "error": "provider timeout"}
                if index == 0
                else {"job_id": job["job_id"], "status": "completed", "evidence_sha256": "a" * 64}
            )
            for index, job in enumerate(first.jobs)
        ],
    )

    status = pipeline_status(project)
    assert REQUIRED_STATUS_FIELDS <= set(status)
    assert status["current_gate"] == "generation_wave"
    assert status["concurrency_limit"] == {
        "configured": 10,
        "wave": 5,
        "hard_max": 10,
    }
    assert status["retry_jobs"] == [first.jobs[0]["job_id"]]
    assert status["ready_generation_wave"][0]["job_id"] == first.jobs[0]["job_id"]
    assert "next_generation_wave.py" in status["exact_cli"]
    assert status["human_review_required"] is False
    assert status["blockers"]["rights"]["source_state"] == "pending_rights_clearance"


def test_status_cli_emits_the_enhanced_contract(tmp_path: Path) -> None:
    project = build_project(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts/run_full_pipeline.py"
    completed = subprocess.run(
        [sys.executable, str(script), "status", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert REQUIRED_STATUS_FIELDS <= set(payload)
    assert payload["current_gate"] == "generation_plan"
    assert "plan_generation_run.py" in payload["exact_cli"]
    assert payload["ready_generation_wave"] == []


def test_status_cli_rejects_a_missing_project_path(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/run_full_pipeline.py"
    missing = tmp_path / "does-not-exist"
    completed = subprocess.run(
        [sys.executable, str(script), "status", "--project", str(missing)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert "does not exist" in payload["error"]


def test_status_exposes_exact_hash_bound_script_approval_command(tmp_path: Path) -> None:
    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    story = project / "02_story_script_故事脚本"
    (story / "SCRIPT_LOCK.json").write_text(
        json.dumps({"release_id": "pilot-r1"}), encoding="utf-8"
    )
    (story / "CONTENT_PACKAGE_MANIFEST.json").write_text("{}", encoding="utf-8")

    status = pipeline_status(project)

    assert status["status"] == "awaiting_script_approval"
    assert status["human_review_required"] is True
    assert "workflow.py approve" in status["exact_cli"]
    assert "--gate script" in status["exact_cli"]
    assert status["exact_cli"].count("--subject") == 5


def test_status_requires_current_scene_continuity_before_director(tmp_path: Path) -> None:
    """The Director may not be the first consumer to discover a missing span plan."""

    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    with mock.patch(
        "book_video_factory.pipeline_runtime.audio_stage_status",
        return_value="ready_for_image_task_planning",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.project_workflow",
        return_value={"visual_foundation_policy": "optional"},
    ):
        status = pipeline_status(project)

    assert status["current_gate"] == "scene_continuity"
    assert status["status"] == "awaiting_scene_continuity_spans"
    assert "build_scene_continuity_spans.py" in status["exact_cli"]


def test_status_requires_visual_foundation_before_audio_for_required_projects(tmp_path: Path) -> None:
    """Required visual foundations must be approved before an audio route is exposed."""

    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    with mock.patch(
        "book_video_factory.pipeline_runtime.repository_integrity_block",
        return_value=None,
    ), mock.patch(
        "book_video_factory.pipeline_runtime.visual_stage_next_status",
        return_value="ready_for_edge_tts",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.project_workflow",
        return_value={"visual_foundation_policy": "required"},
    ), mock.patch(
        "book_video_factory.pipeline_runtime.audio_stage_status",
        side_effect=AssertionError("audio status must not run before visual foundation"),
    ):
        status = pipeline_status(project)

    assert status["current_gate"] == "visual_foundation"
    assert status["status"] == "blocked_by_missing_visual_foundation"
    assert "build_visual_foundation.py" in status["exact_cli"]


def test_status_routes_required_projects_to_minimax_before_legacy_audio(tmp_path: Path) -> None:
    """A new expressive release must never expose the legacy Edge command."""

    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    with mock.patch(
        "book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None,
    ), mock.patch(
        "book_video_factory.pipeline_runtime.visual_stage_next_status", return_value="ready_for_edge_tts",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.project_workflow",
        return_value={"visual_foundation_policy": "required", "narration_provider_policy": "minimax_required"},
    ), mock.patch(
        "book_video_factory.visual_foundation.approval.visual_foundation_status",
        return_value="visual_foundation_approved",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.audio_stage_status",
        side_effect=AssertionError("legacy audio status must not run for MiniMax"),
    ):
        status = pipeline_status(project)

    assert status["current_gate"] == "minimax_voice_foundation"
    assert status["status"] == "awaiting_minimax_voice_foundation"
    assert "build_voice_foundation.py" in status["exact_cli"]
    assert "Edge" not in status["unique_next_action"]


def test_status_exposes_minimax_generate_command_after_foundations_are_ready(tmp_path: Path) -> None:
    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    (project / "04_audio/VOICE_FOUNDATION.json").write_text("{}", encoding="utf-8")
    with mock.patch(
        "book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None,
    ), mock.patch(
        "book_video_factory.pipeline_runtime.visual_stage_next_status", return_value="ready_for_narration",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.project_workflow",
        return_value={"visual_foundation_policy": "optional", "narration_provider_policy": "minimax_required"},
    ), mock.patch(
        "book_video_factory.pipeline_runtime.audio_stage_status", return_value="ready_for_narration",
    ):
        status = pipeline_status(project)

    assert status["current_gate"] == "audio"
    assert status["status"] == "ready_for_narration"
    assert "--provider minimax" in status["exact_cli"]
    assert "Edge" not in status["unique_next_action"]


def test_legacy_edge_route_does_not_require_voice_foundation(tmp_path: Path) -> None:
    project = initialize_project(tmp_path / "warehouse", "pilot", "狂人日记", "鲁迅")
    with mock.patch(
        "book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None,
    ), mock.patch(
        "book_video_factory.pipeline_runtime.visual_stage_next_status", return_value="ready_for_edge_tts",
    ), mock.patch(
        "book_video_factory.pipeline_runtime.project_workflow",
        return_value={"visual_foundation_policy": "optional", "narration_provider_policy": "legacy_edge"},
    ), mock.patch(
        "book_video_factory.pipeline_runtime.audio_stage_status", return_value="ready_for_edge_tts",
    ):
        status = pipeline_status(project)

    assert status["status"] == "ready_for_edge_tts"
    assert "--provider edge-tts" in status["exact_cli"]
