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
from book_video_factory.sample_scene_asset import (
    SampleSceneAssetError,
    register_sample_scene_asset,
)
from book_video_factory.visual_stage.asset_registry import register_visual_asset
from book_video_factory.visual_stage.compiler import compile_visual_stage


def _tasks(project: Path) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    for filename in ("ANCHOR_TASKS.jsonl", "LOOKDEV_TASKS.jsonl"):
        for line in (project / "03_images_生成图片" / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                task = json.loads(line)
                tasks[task["task_id"]] = task
    return tasks


def _register(project: Path, base: Path, task: dict, index: int) -> None:
    source = base / f"{task['task_id']}.png"
    with Image.new("RGB", (1920, 1080), (index * 23 % 250, index * 47 % 250, index * 71 % 250)) as image:
        image.save(source)
    register_visual_asset(
        project,
        task_id=task["task_id"],
        source=source,
        prompt_sha256=task["prompt_sha256"],
        tool_call_id=f"imagegen_sample_scene_{index:06d}",
        style_reference_ids=task["style_reference_ids"],
        identity_reference_task_ids=task["identity_dependency_task_ids"],
    )


class SampleSceneAssetTests(unittest.TestCase):
    def _input(self, base: Path) -> tuple[Path, Path]:
        project, visual_input, _, _, _ = build_phase3_project(base)
        visual_input_path = base / "visual-input.json"
        write_json(visual_input_path, visual_input)
        compile_visual_stage(project, visual_input_path)
        tasks = _tasks(project)
        _register(project, base, tasks["ANCHOR_C001_FRONT"], 1)
        _register(project, base, tasks["ANCHOR_SCENE_HARBOR"], 2)
        excerpt_path = project / "02_story_script_故事脚本" / "SAMPLE_EXCERPT.json"
        text = "老人回到港口。"
        write_json(excerpt_path, {
            "schema_version": "sample-excerpt.v1", "release_id": "r1", "sample_id": "pilot-60s",
            "spoken_text": text,
            "selection": [{"source_sequence": 1, "text": text}],
            "integrity": {"selection_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "no_rewrite": True},
        })
        source = base / "real-host-image.png"
        with Image.new("RGB", (1920, 1080), (89, 107, 131)) as image:
            image.save(source)
        prompt = "A period-correct harbor scene with the old fisherman."
        input_path = base / "sample-scene-asset-input.json"
        write_json(input_path, {
            "schema_version": "sample-scene-asset-input.v2",
            "release_id": "r1",
            "sample_excerpt_path": "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
            "sample_excerpt_sha256": hashlib.sha256(excerpt_path.read_bytes()).hexdigest(),
            "scene_id": "SAMPLE_S001",
            "source_sequence_ids": [1],
            "source": str(source),
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "tool_call_id": "exec-sample-scene-000001",
            "provider": "flow-web",
            "identity_reference_task_ids": ["ANCHOR_C001_FRONT"],
            "location_anchor_task_ids": ["ANCHOR_SCENE_HARBOR"],
            "required_entities": ["老人", "港口"],
            "forbidden_entities": ["现代游艇", "生成文字"],
        })
        return project, input_path

    def test_registers_real_scene_candidate_with_current_source_and_reference_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._input(Path(temporary))
            result = register_sample_scene_asset(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "created")
            self.assertEqual(payload["scene_id"], "SAMPLE_S001")
            self.assertTrue((project / payload["asset"]["path"]).is_file())
            self.assertEqual(payload["identity_references"][0]["task_id"], "ANCHOR_C001_FRONT")
            self.assertEqual(payload["location_anchors"][0]["task_id"], "ANCHOR_SCENE_HARBOR")
            self.assertEqual(payload["human_review_status"], "pending")
            self.assertEqual(payload["generation"]["provider"], "flow-web")

    def _multi_sequence_input(self, base: Path) -> tuple[Path, Path]:
        project, input_path = self._input(base)
        excerpt_path = project / "02_story_script_故事脚本" / "SAMPLE_EXCERPT.json"
        text = "老人回到港口。"
        write_json(excerpt_path, {
            "schema_version": "sample-excerpt.v1", "release_id": "r1", "sample_id": "pilot-60s",
            "spoken_text": text,
            "selection": [
                {"source_sequence": 1, "text": "老人回到港口。"},
                {"source_sequence": 2, "text": "船靠岸。"},
            ],
            "integrity": {"selection_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "no_rewrite": True},
        })
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        payload["sample_excerpt_sha256"] = hashlib.sha256(excerpt_path.read_bytes()).hexdigest()
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return project, input_path

    def test_registers_sub_sequence_with_whitelisted_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._multi_sequence_input(Path(temporary))
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["source_sequence_ids"] = [1]
            payload["provider"] = "gemini-web"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = register_sample_scene_asset(project, input_path)
            record = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(record["source_sequence_ids"], [1])
            self.assertEqual(record["generation"]["provider"], "gemini-web")

    def test_rejects_non_subset_source_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._multi_sequence_input(Path(temporary))
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["source_sequence_ids"] = [2, 3]
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleSceneAssetError, "subset"):
                register_sample_scene_asset(project, input_path)

    def test_rejects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._input(Path(temporary))
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["provider"] = "openai-image"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleSceneAssetError, "generation call evidence"):
                register_sample_scene_asset(project, input_path)

    def test_rejects_current_asset_when_staged_source_hash_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._input(Path(temporary))
            result = register_sample_scene_asset(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            (project / payload["asset"]["path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(SampleSceneAssetError, "stale|hash"):
                register_sample_scene_asset(project, input_path)


if __name__ == "__main__":
    unittest.main()
