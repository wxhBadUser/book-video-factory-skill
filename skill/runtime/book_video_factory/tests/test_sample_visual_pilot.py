from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase2_fixture_factory import write_json
from phase3_fixture_factory import build_phase3_project
from book_video_factory.visual_stage.asset_registry import register_visual_asset, verify_visual_asset_manifest
from book_video_factory.visual_stage.compiler import compile_visual_stage
from book_video_factory.sample_visual_pilot import (
    SampleVisualPilotError,
    build_sample_visual_pilot,
    minimum_sample_scene_image_count,
)


def _tasks(project: Path) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for filename in ("ANCHOR_TASKS.jsonl", "LOOKDEV_TASKS.jsonl"):
        for line in (project / "03_images_生成图片" / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                task = json.loads(line)
                items[task["task_id"]] = task
    return items


def _register(project: Path, base: Path, task: dict, number: int) -> None:
    source = base / f"{task['task_id']}.png"
    with Image.new("RGB", (1920, 1080), (number * 17 % 250, number * 43 % 250, number * 79 % 250)) as image:
        image.save(source)
    register_visual_asset(
        project,
        task_id=task["task_id"],
        source=source,
        prompt_sha256=task["prompt_sha256"],
        tool_call_id=f"imagegen_sample_pilot_{number:06d}",
        style_reference_ids=task["style_reference_ids"],
        identity_reference_task_ids=task["identity_dependency_task_ids"],
    )


def build_multi_span_project(base: Path) -> tuple[Path, Path]:
    project, payload, _, _, _ = build_phase3_project(base)
    visual_input = base / "visual-input.json"
    write_json(visual_input, payload)
    compile_visual_stage(project, visual_input)
    tasks = _tasks(project)
    selected = ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR", "LOOKDEV_LD03"]
    for index, task_id in enumerate(selected, start=1):
        _register(project, base, tasks[task_id], index)
    excerpt_path = project / "02_story_script_故事脚本" / "SAMPLE_EXCERPT.json"
    text = "老人和他的船在港口。老人看向海面。船帆在风里鼓起来。海鸟落在船头。"
    excerpt = {
        "schema_version": "sample-excerpt.v1",
        "release_id": "r1",
        "sample_id": "pilot-60s",
        "spoken_text": text,
        "selection": [
            {"source_sequence": 1, "text": "老人和他的船在港口。"},
            {"source_sequence": 2, "text": "老人看向海面。"},
            {"source_sequence": 3, "text": "船帆在风里鼓起来。"},
            {"source_sequence": 4, "text": "海鸟落在船头。"},
        ],
        "timing": {
            "target_duration_seconds": 60.0,
            "min_duration_seconds": 45.0,
            "max_duration_seconds": 90.0,
            "characters_per_minute": 200.0,
            "estimated_duration_seconds": 60.0,
        },
        "integrity": {"selection_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "no_rewrite": True},
    }
    write_json(excerpt_path, excerpt)
    registered = verify_visual_asset_manifest(project).assets_by_task
    record_paths: list[str] = []
    for scene_id, sequence_ids, color in (
        ("SAMPLE_S001", [1], (81, 109, 137)),
        ("SAMPLE_S002", [2], (91, 119, 147)),
        ("SAMPLE_S003", [3], (101, 129, 157)),
        ("SAMPLE_S004", [4], (111, 139, 167)),
    ):
        asset_path = project / "assets" / "generated" / "sample_scenes" / f"{scene_id}.png"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", (1920, 1080), color) as image:
            image.save(asset_path)
        asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        record_path = project / "03_images_生成图片" / "sample_scene_assets" / f"{scene_id}.json"
        write_json(record_path, {
            "schema_version": "sample-scene-asset.v2",
            "release_id": "r1",
            "sample_id": "pilot-60s",
            "scene_id": scene_id,
            "sample_excerpt": {
                "path": "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
                "sha256": hashlib.sha256(excerpt_path.read_bytes()).hexdigest(),
            },
            "source_sequence_ids": sequence_ids,
            "asset": {"path": f"assets/generated/sample_scenes/{scene_id}.png", "sha256": asset_sha256, "width": 1920, "height": 1080, "mode": "RGB"},
            "generation": {"provider": "flow-web", "tool_call_id": "flow-web-prod-sample-0001", "prompt_sha256": "b" * 64},
            "identity_references": [{key: registered["ANCHOR_C001_FRONT"][key] for key in ("task_id", "path", "sha256")}],
            "location_anchors": [{key: registered["ANCHOR_SCENE_HARBOR"][key] for key in ("task_id", "path", "sha256")}],
        })
        record_paths.append(str(record_path.relative_to(project)).replace("\\", "/"))
    input_path = base / "sample-visual-pilot-input.json"
    write_json(input_path, {
        "schema_version": "sample-visual-pilot-input.v2",
        "release_id": "r1",
        "sample_excerpt_path": "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
        "sample_excerpt_sha256": hashlib.sha256(excerpt_path.read_bytes()).hexdigest(),
        "scene_spans": [
            {
                "span_id": "SPAN_1",
                "source_sequence_ids": [1],
                "representative_scene_asset_path": record_paths[0],
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "hard_cut",
            },
            {
                "span_id": "SPAN_2",
                "source_sequence_ids": [2],
                "representative_scene_asset_path": record_paths[1],
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "dissolve_6_8_frames",
            },
            {
                "span_id": "SPAN_3",
                "source_sequence_ids": [3],
                "representative_scene_asset_path": record_paths[2],
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "dissolve_6_8_frames",
            },
            {
                "span_id": "SPAN_4",
                "source_sequence_ids": [4],
                "representative_scene_asset_path": record_paths[3],
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "hard_cut",
            },
        ],
    })
    return project, input_path


class SampleVisualPilotTests(unittest.TestCase):
    def test_density_floor_is_explicit_at_75_and_90_second_boundaries(self) -> None:
        def excerpt(duration: float) -> dict:
            return {"timing": {"estimated_duration_seconds": duration}}

        self.assertEqual(minimum_sample_scene_image_count(excerpt(75.0)), 5)
        self.assertEqual(minimum_sample_scene_image_count(excerpt(90.0)), 6)

    def _project(self, base: Path) -> tuple[Path, Path]:
        project, payload, _, _, _ = build_phase3_project(base)
        visual_input = base / "visual-input.json"
        write_json(visual_input, payload)
        compile_visual_stage(project, visual_input)
        tasks = _tasks(project)
        # This deliberately registers only the assets that the sample itself needs.
        # A sample must not claim that the full-book visual stage is approved.
        selected = [
            "ANCHOR_C001_FRONT",
            "ANCHOR_SCENE_HARBOR",
            "LOOKDEV_LD01",
            "LOOKDEV_LD02",
            "LOOKDEV_LD03",
            "LOOKDEV_LD04",
        ]
        for index, task_id in enumerate(selected, start=1):
            _register(project, base, tasks[task_id], index)
        excerpt_path = project / "02_story_script_故事脚本" / "SAMPLE_EXCERPT.json"
        text = "老人和他的船在港口。老人看向海面。船帆在风里鼓起来。海鸟落在船头。"
        excerpt = {
            "schema_version": "sample-excerpt.v1",
            "release_id": "r1",
            "sample_id": "pilot-60s",
            "spoken_text": text,
            "selection": [
                {"source_sequence": 1, "text": "老人和他的船在港口。"},
                {"source_sequence": 2, "text": "老人看向海面。"},
                {"source_sequence": 3, "text": "船帆在风里鼓起来。"},
                {"source_sequence": 4, "text": "海鸟落在船头。"},
            ],
            "timing": {
                "target_duration_seconds": 60.0,
                "min_duration_seconds": 45.0,
                "max_duration_seconds": 90.0,
                "characters_per_minute": 200.0,
                "estimated_duration_seconds": 60.0,
            },
            "integrity": {"selection_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "no_rewrite": True},
        }
        write_json(excerpt_path, excerpt)
        input_path = base / "sample-visual-pilot-input.json"
        write_json(input_path, {
            "schema_version": "sample-visual-pilot-input.v2",
            "release_id": "r1",
            "sample_excerpt_path": "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
            "sample_excerpt_sha256": hashlib.sha256(excerpt_path.read_bytes()).hexdigest(),
            "scene_spans": [{
                "span_id": "SPAN_1",
                "source_sequence_ids": [1],
                "representative_task_id": "LOOKDEV_LD01",
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "hard_cut",
            }, {
                "span_id": "SPAN_2",
                "source_sequence_ids": [2],
                "representative_task_id": "LOOKDEV_LD02",
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "dissolve_6_8_frames",
            }, {
                "span_id": "SPAN_3",
                "source_sequence_ids": [3],
                "representative_task_id": "LOOKDEV_LD03",
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "dissolve_6_8_frames",
            }, {
                "span_id": "SPAN_4",
                "source_sequence_ids": [4],
                "representative_task_id": "LOOKDEV_LD04",
                "required_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_SCENE_HARBOR"],
                "transition_in": "hard_cut",
            }],
        })
        return project, input_path

    def test_creates_hash_bound_static_sample_pilot_without_full_book_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary))
            result = build_sample_visual_pilot(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "created")
            self.assertEqual(payload["sample_id"], "pilot-60s")
            self.assertEqual(payload["human_review_status"], "pending")
            self.assertTrue(payload["static_image_policy"]["no_camera_motion"])
            self.assertEqual(payload["scene_spans"][0]["transition_in"], "hard_cut")
            self.assertFalse((project / "03_images_生成图片" / "ANCHOR_APPROVAL.json").exists())

    def test_rejects_existing_pilot_when_bound_asset_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary))
            result = build_sample_visual_pilot(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            target = project / payload["scene_spans"][0]["representative_image"]["path"]
            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(SampleVisualPilotError, "stale|hash"):
                build_sample_visual_pilot(project, input_path)

    def test_ignores_new_unrelated_full_book_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project, input_path = self._project(base)
            build_sample_visual_pilot(project, input_path)
            unrelated = _tasks(project)["LOOKDEV_LD05"]
            _register(project, base, unrelated, 42)
            self.assertEqual(build_sample_visual_pilot(project, input_path).status, "unchanged")

    def test_binds_a_hash_checked_sample_scene_asset_instead_of_a_lookdev(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project, input_path = self._project(base)
            asset_path = project / "assets" / "generated" / "sample_scenes" / "SAMPLE_S001.png"
            asset_path.parent.mkdir(parents=True)
            with Image.new("RGB", (1920, 1080), (81, 109, 137)) as image:
                image.save(asset_path)
            asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
            registered_assets = verify_visual_asset_manifest(project).assets_by_task
            record_path = project / "03_images_生成图片" / "sample_scene_assets" / "SAMPLE_S001.json"
            write_json(record_path, {
                "schema_version": "sample-scene-asset.v2",
                "release_id": "r1",
                "sample_id": "pilot-60s",
                "scene_id": "SAMPLE_S001",
                "sample_excerpt": {
                    "path": "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
                    "sha256": hashlib.sha256((project / "02_story_script_故事脚本" / "SAMPLE_EXCERPT.json").read_bytes()).hexdigest(),
                },
                "source_sequence_ids": [1],
                "asset": {"path": "assets/generated/sample_scenes/SAMPLE_S001.png", "sha256": asset_sha256, "width": 1920, "height": 1080, "mode": "RGB"},
                "generation": {"provider": "flow-web", "tool_call_id": "flow-web-prod-scene-0001", "prompt_sha256": "a" * 64},
                "identity_references": [{key: registered_assets["ANCHOR_C001_FRONT"][key] for key in ("task_id", "path", "sha256")}],
                "location_anchors": [{key: registered_assets["ANCHOR_SCENE_HARBOR"][key] for key in ("task_id", "path", "sha256")}],
            })
            pilot_input = json.loads(input_path.read_text(encoding="utf-8"))
            span = pilot_input["scene_spans"][0]
            span.pop("representative_task_id")
            span["representative_scene_asset_path"] = "03_images_生成图片/sample_scene_assets/SAMPLE_S001.json"
            input_path.write_text(json.dumps(pilot_input, ensure_ascii=False), encoding="utf-8")

            result = build_sample_visual_pilot(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            representative = payload["scene_spans"][0]["representative_image"]
            self.assertEqual(representative["kind"], "sample_scene_asset")
            self.assertEqual(representative["sha256"], asset_sha256)
            asset_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(SampleVisualPilotError, "stale|hash"):
                build_sample_visual_pilot(project, input_path)

    def test_builds_multi_span_pilot_from_sub_sequence_sample_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = build_multi_span_project(Path(temporary))
            result = build_sample_visual_pilot(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "created")
            self.assertEqual(result.scene_span_count, 4)
            self.assertEqual(payload["schema_version"], "sample-visual-pilot-manifest.v2")
            self.assertEqual(payload["scene_spans"][0]["source_sequence_ids"], [1])
            self.assertEqual(payload["scene_spans"][1]["source_sequence_ids"], [2])
            self.assertEqual(payload["scene_spans"][2]["source_sequence_ids"], [3])
            self.assertEqual(payload["scene_spans"][3]["source_sequence_ids"], [4])
            self.assertEqual(payload["scene_spans"][1]["transition_in"], "dissolve_6_8_frames")
            self.assertEqual(payload["scene_spans"][0]["representative_image"]["scene_id"], "SAMPLE_S001")
            self.assertEqual(payload["scene_spans"][1]["representative_image"]["scene_id"], "SAMPLE_S002")
            self.assertEqual(payload["scene_spans"][2]["representative_image"]["scene_id"], "SAMPLE_S003")
            self.assertEqual(payload["scene_spans"][3]["representative_image"]["scene_id"], "SAMPLE_S004")

    def test_rejects_60s_visual_sample_with_a_single_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary))
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            first = payload["scene_spans"][0]
            first["source_sequence_ids"] = [1, 2, 3, 4]
            payload["scene_spans"] = [first]
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleVisualPilotError, "at least 4 scene images"):
                build_sample_visual_pilot(project, input_path)

    def test_rejects_overlapping_multi_span_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = build_multi_span_project(Path(temporary))
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["scene_spans"][1]["source_sequence_ids"] = [1, 2]
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleVisualPilotError, "stale|invalid|exactly cover|subset"):
                build_sample_visual_pilot(project, input_path)


if __name__ == "__main__":
    unittest.main()
