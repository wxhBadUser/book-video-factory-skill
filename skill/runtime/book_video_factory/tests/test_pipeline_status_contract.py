from __future__ import annotations

import json
import subprocess
import sys
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
