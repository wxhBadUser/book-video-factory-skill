from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    write_phase4_inputs,
)
from book_video_factory.audio_stage.compiler import finalize_audio_stage, generate_audio_stage
from book_video_factory.director_stage.compiler import compile_director_stage
from book_video_factory.production_visuals.registry import SceneAssetError, register_scene_asset
from book_video_factory.production_visuals.review import _default_contact_sheet, build_scene_asset_review


class SceneAssetTests(unittest.TestCase):
    def test_scene_review_stages_on_project_volume_for_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "warehouse/projects/project"
            manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text("{}\n", encoding="utf-8")
            current = (
                {"B01": {}},
                {"B01": {"path": "assets/generated/scenes/B01.png", "sha256": "1" * 64}},
                "2" * 64,
                {"release_id": "r1"},
            )
            decision = {
                "reviewer": "human",
                "decisions": [{
                    "task_id": "B01",
                    "semantic_review_status": "pass",
                    "reality_review_status": "pass",
                    "identity_review_status": "pass",
                    "note": "ok",
                }],
            }
            with mock.patch(
                "book_video_factory.production_visuals.review._current_assets",
                return_value=current,
            ), mock.patch(
                "book_video_factory.production_visuals.review._decision",
                return_value=decision,
            ), mock.patch(
                "book_video_factory.production_visuals.review.tempfile.TemporaryDirectory",
                side_effect=RuntimeError("stop before render"),
            ) as temporary:
                with self.assertRaisesRegex(RuntimeError, "stop before render"):
                    build_scene_asset_review(root, root / "decision.json")

            temporary.assert_called_once_with(prefix="scene-review-", dir=root.parent)

    def test_hbg_contact_sheet_uses_the_cross_platform_bash_path_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "sheet.jpg"
            image = Path(temp) / "scene.png"
            Image.new("RGB", (1920, 1080), (10, 20, 30)).save(image)
            with mock.patch(
                "book_video_factory.production_visuals.review._verify_vendor",
            ), mock.patch(
                "book_video_factory.production_visuals.review.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ) as runner:
                _default_contact_sheet(output, [image])

            command = runner.call_args.args[0]
            self.assertTrue(str(command[0]).lower().endswith("bash.exe"))
            self.assertEqual(command[1], "-c")
            self.assertIn("builtin pwd -W", command[2])
            self.assertEqual(command[3], "hbg-contact-sheet")
            self.assertNotIn("\\", command[4])
            self.assertNotIn("\\", command[5])
            self.assertNotIn("\\", command[-1])

    def prepare(self, base: Path) -> tuple[Path, dict]:
        project = build_approved_phase3_project(base)
        input_path, lexicon_path = write_phase4_inputs(project)
        generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
        plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
        write_json(plan_path, build_storyboard_audio_plan(project))
        finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
        compile_director_stage(project)
        task = json.loads((project / "05_director/IMAGE_TASKS.jsonl").read_text(encoding="utf-8").splitlines()[0])
        return project, task

    def test_registers_real_scene_png_with_style_and_identity_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, task = self.prepare(base)
            source = base / "scene.png"
            Image.new("RGB", (1920, 1080), (41, 92, 133)).save(source)
            result = register_scene_asset(
                project,
                task_id=task["task_id"], source=source,
                tool_call_id="imagegen_call_scene_000001",
                style_reference_ids=task["style_reference_ids"],
                identity_reference_task_ids=task["identity_reference_task_ids"],
            )
            self.assertEqual(result.status, "created")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            asset = manifest["assets"][0]
            self.assertEqual(asset["prompt_sha256"], task["prompt_sha256"])
            self.assertEqual(len(asset["style_reference_evidence"]), len(task["style_reference_ids"]))
            self.assertEqual(len(asset["identity_reference_evidence"]), len(task["identity_reference_task_ids"]))
            self.assertTrue(result.asset_path.is_file())

    def test_rejects_wrong_reference_declaration_and_duplicate_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, task = self.prepare(base)
            source = base / "scene.png"
            Image.new("RGB", (1920, 1080), (41, 92, 133)).save(source)
            with self.assertRaises(SceneAssetError):
                register_scene_asset(
                    project, task_id=task["task_id"], source=source,
                    tool_call_id="imagegen_call_scene_000001",
                    style_reference_ids=[],
                    identity_reference_task_ids=task["identity_reference_task_ids"],
                )

    def test_registers_an_in_place_pristine_hbg_sheet_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, task = self.prepare(Path(temp))
            output = project / task["output_target"]
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1920, 1080), (31, 62, 93)).save(output)

            result = register_scene_asset(
                project,
                task_id=task["task_id"], source=output,
                tool_call_id="imagegen_call_sheet_000001",
                style_reference_ids=task["style_reference_ids"],
                identity_reference_task_ids=task["identity_reference_task_ids"],
            )

            self.assertEqual(result.status, "created")
            self.assertEqual(result.asset_path, output)


if __name__ == "__main__":
    unittest.main()
