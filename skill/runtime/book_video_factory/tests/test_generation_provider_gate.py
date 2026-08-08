"""L9 adversarial test: generation-provider gate must fail closed.

Every production image task declares a ``generation_lane``. New work may only be
produced by the sanctioned lane (``host-imagegen``); the lanes huozhe-r1 shipped
173 misaligned assets on (``gemini-web`` / ``flow-web`` / ``imagegen``) and any
other string must be rejected at the generation dispatch (``plan_generation_run``
-> ``_load_tasks``), not silently accepted.

Mutation criterion (plan §0): on committed pre-remediation HEAD ``_load_tasks``
never calls ``validate_provider``, so an unsanctioned lane is accepted -- the
BLOCKING assertions below fail there (no raise).
"""

from __future__ import annotations

import hashlib
import json
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


def _task(number: int, *, lane: str, mode: str = "single") -> dict:
    task_id = f"SCENE_S{number:02d}"
    prompt = f"production prompt {number}"
    return {
        "schema_version": "production-image-task.v1",
        "task_id": task_id,
        "scene_id": f"S{number:02d}",
        "release_id": "r1",
        "generation_lane": lane,
        "generation_mode": mode,
        "identity_reference_task_ids": [],
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "output_target": f"assets/generated/scenes/S{number:02d}.png",
    }


def _make_project(base: Path, *, lane: str) -> Path:
    project = base / "warehouse/projects/pilot"
    director = project / "05_director"
    director.mkdir(parents=True)
    tasks = [_task(index, lane=lane, mode="2x2") for index in range(1, 5)] + [
        _task(index, lane=lane) for index in range(5, 8)
    ]
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
    return project


class GenerationProviderGateTests(unittest.TestCase):
    def test_sanctioned_host_imagegen_lane_plans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), lane="host-imagegen")
            result = plan_generation_run(project, concurrency=5)
            self.assertIn(result.status, {"created", "unchanged"})

    def test_unsanctioned_gemini_web_lane_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), lane="gemini-web")
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project, concurrency=5)

    def test_unsanctioned_flow_web_lane_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), lane="flow-web")
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project, concurrency=5)

    def test_empty_generation_lane_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp), lane="")
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project, concurrency=5)


if __name__ == "__main__":
    unittest.main()
