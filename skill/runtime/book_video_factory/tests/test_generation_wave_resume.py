from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_generation_scheduler import _task, _write_json, build_project
from book_video_factory.production_visuals.scheduler import (
    finalize_generation_wave,
    next_generation_wave,
    plan_generation_run,
)


class GenerationWaveResumeTests(unittest.TestCase):
    def test_wave_is_bounded_and_retries_only_failed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project, concurrency=5)
            first = next_generation_wave(project)
            self.assertLessEqual(len(first.jobs), 5)
            self.assertEqual([job["job_id"] for job in first.jobs], ["SHEET_0001", "SCENE_S05", "SCENE_S07"])
            finalize_generation_wave(project, [
                {"job_id": "SHEET_0001", "status": "completed", "evidence_sha256": "1" * 64},
                {"job_id": "SCENE_S05", "status": "failed", "error": "provider timeout"},
                {"job_id": "SCENE_S07", "status": "completed", "evidence_sha256": "7" * 64},
            ])
            retry = next_generation_wave(project)
            self.assertEqual([job["job_id"] for job in retry.jobs], ["SCENE_S05"])
            finalize_generation_wave(project, [{"job_id": "SCENE_S05", "status": "completed", "evidence_sha256": "5" * 64}])
            downstream = next_generation_wave(project)
            self.assertEqual([job["job_id"] for job in downstream.jobs], ["SCENE_S06"])
            attempts = [json.loads(line) for line in (project / "06_visual_production/GENERATION_ATTEMPTS.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["attempt"] for item in attempts if item["job_id"] == "SCENE_S05"], [1, 2])
            self.assertNotIn("SHEET_0001", [job["job_id"] for job in downstream.jobs])
            self.assertNotIn("SCENE_S07", [job["job_id"] for job in downstream.jobs])

    def test_explicit_limit_cannot_exceed_run_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project, concurrency=2)
            wave = next_generation_wave(project, limit=10)
            self.assertEqual(len(wave.jobs), 2)

    def test_status_wave_never_returns_more_than_five_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            task_path = project / "05_director/IMAGE_TASKS.jsonl"
            task_path.write_text(task_path.read_text(encoding="utf-8") + "".join(json.dumps(_task(index)) + "\n" for index in range(8, 14)), encoding="utf-8")
            sheet_path = project / "05_director/SHEET_MAP.json"
            sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
            sheet["single_tasks"].extend({"task_id": f"SCENE_S{index:02d}", "reason": "single"} for index in range(8, 14))
            sheet["single_task_count"] += 6
            _write_json(sheet_path, sheet)
            plan_generation_run(project, concurrency=10)
            wave = next_generation_wave(project)
            self.assertLessEqual(len(wave.jobs), 5)


if __name__ == "__main__":
    unittest.main()
