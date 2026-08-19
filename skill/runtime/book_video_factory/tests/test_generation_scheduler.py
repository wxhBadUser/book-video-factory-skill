from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from book_video_factory.production_visuals.scheduler import (
    GenerationScheduleError,
    plan_generation_run,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _task(number: int, *, identity: list[str] | None = None, mode: str = "single") -> dict:
    task_id = f"SCENE_S{number:02d}"
    prompt = f"production prompt {number}"
    return {
        "schema_version": "production-image-task.v1",
        "task_id": task_id,
        "scene_id": f"S{number:02d}",
        "release_id": "r1",
        "generation_lane": "host-imagegen",
        "generation_mode": mode,
        "identity_reference_task_ids": list(identity or []),
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "output_target": f"assets/generated/scenes/S{number:02d}.png",
    }


def build_project(base: Path, *, registered: list[str] | None = None) -> Path:
    project = base / "warehouse/projects/pilot"
    _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "legacy"}})
    director = project / "05_director"
    director.mkdir(parents=True)
    tasks = [*[_task(index, mode="2x2") for index in range(1, 5)], _task(5, identity=["ANCHOR_MAIN"]), _task(6, identity=["ANCHOR_MAIN"]), _task(7)]
    (director / "IMAGE_TASKS.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in tasks),
        encoding="utf-8",
    )
    _write_json(director / "SHEET_MAP.json", {
        "schema_version": "sheet-map.v1",
        "sheet_groups": [{
            "sheet_id": "SHEET_0001",
            "task_ids": [f"SCENE_S{index:02d}" for index in range(1, 5)],
            "output_target": "assets/generated/sheets/SHEET_0001.png",
            "split_targets": [f"assets/generated/scenes/S{index:02d}.png" for index in range(1, 5)],
        }],
        "single_tasks": [{"task_id": f"SCENE_S{index:02d}", "reason": "single"} for index in range(5, 8)],
        "sheet_task_count": 4,
        "single_task_count": 3,
    })
    _write_json(director / "DIRECTOR_STAGE_MANIFEST.json", {"schema_version": "director-stage-manifest.v1", "release_id": "r1"})
    if registered:
        _write_json(project / "06_visual_production/SCENE_ASSET_MANIFEST.json", {
            "schema_version": "scene-asset-manifest.v1",
            "release_id": "r1",
            "assets": [{"task_id": task_id} for task_id in registered],
        })
    return project


class GenerationSchedulerTests(unittest.TestCase):
    def test_generation_cli_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_project(base)
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            planned = subprocess.run(
                [sys.executable, str(scripts / "plan_generation_run.py"), "--project", str(project), "--concurrency", "2"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
            self.assertEqual(json.loads(planned.stdout)["status"], "created")
            wave = subprocess.run(
                [sys.executable, str(scripts / "next_generation_wave.py"), "--project", str(project)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(wave.returncode, 0, wave.stderr)
            jobs = json.loads(wave.stdout)["jobs"]
            results = base / "results.json"
            _write_json(results, [{"job_id": item["job_id"], "status": "completed", "evidence_sha256": "a" * 64} for item in jobs])
            finalized = subprocess.run(
                [sys.executable, str(scripts / "finalize_generation_wave.py"), "--project", str(project), "--results", str(results)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            self.assertEqual(json.loads(finalized.stdout)["status"], "settled")

    def test_plans_sheet_and_single_jobs_with_identity_conflict_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            result = plan_generation_run(project)
            self.assertEqual(result.status, "created")
            self.assertEqual(result.concurrency, 5)
            plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
            jobs = [json.loads(line) for line in result.jobs_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(plan["job_count"], 4)
            self.assertEqual(jobs[0]["job_id"], "SHEET_0001")
            self.assertEqual(jobs[0]["generation_mode"], "2x2")
            self.assertEqual(len(jobs[0]["task_ids"]), 4)
            by_id = {item["job_id"]: item for item in jobs}
            self.assertEqual(by_id["SCENE_S06"]["dependencies"], ["SCENE_S05"])
            run = json.loads(result.run_manifest_path.read_text(encoding="utf-8"))
            statuses = {item["job_id"]: item["status"] for item in run["jobs"]}
            self.assertEqual(statuses["SCENE_S05"], "ready")
            self.assertEqual(statuses["SCENE_S06"], "blocked")

    def test_rejects_concurrency_over_ten_and_preserves_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            with self.assertRaisesRegex(GenerationScheduleError, "concurrency"):
                plan_generation_run(project, concurrency=11)
            first = plan_generation_run(project, concurrency=3)
            second = plan_generation_run(project, concurrency=3)
            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "unchanged")
            tasks = project / "05_director/IMAGE_TASKS.jsonl"
            tasks.write_text(tasks.read_text(encoding="utf-8") + json.dumps(_task(8)) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(GenerationScheduleError, "different|stale|input|cover"):
                plan_generation_run(project, concurrency=3)

    def test_already_registered_assets_are_completed_and_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp), registered=["SCENE_S07"])
            result = plan_generation_run(project)
            run = json.loads(result.run_manifest_path.read_text(encoding="utf-8"))
            status = {item["job_id"]: item["status"] for item in run["jobs"]}
            self.assertEqual(status["SCENE_S07"], "completed")

    def test_partially_registered_sheet_is_not_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp), registered=["SCENE_S01"])
            with self.assertRaisesRegex(GenerationScheduleError, "partial|sheet|registered"):
                plan_generation_run(project)


if __name__ == "__main__":
    unittest.main()
