from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase2_fixture_factory import write_json
from phase3_fixture_factory import build_phase3_project
from book_video_factory.visual_stage.compiler import (
    VisualStageCompileError,
    VisualStageConflict,
    compile_visual_stage,
    _required_recorded_at,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Phase3VisualCompilerTests(unittest.TestCase):
    def _input(self, base: Path, payload: dict) -> Path:
        path = base / "visual-input.json"
        write_json(path, payload)
        return path

    def test_compiles_profile_reference_manifest_tasks_and_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, bridge, _, _ = build_phase3_project(base)
            original_roots = {name: sha(project / name) for name in ("SCRIPT.md", "CHARACTERS.md", "PROJECT_SPEC.json", "STORYBOARD_BASE.json")}
            result = compile_visual_stage(project, self._input(base, payload))
            self.assertEqual(result.status, "created")
            expected = (
                "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
                "03_images_生成图片/VISUAL_REFERENCE_MANIFEST.json",
                "03_images_生成图片/ANCHOR_TASKS.jsonl",
                "03_images_生成图片/LOOKDEV_TASKS.jsonl",
                "03_images_生成图片/VISUAL_STAGE_INPUT.json",
                "03_images_生成图片/VISUAL_STAGE_MANIFEST.json",
                "PROMPTS.md",
            )
            for relative in expected:
                self.assertTrue((project / relative).is_file(), relative)
            anchors = (project / "03_images_生成图片/ANCHOR_TASKS.jsonl").read_text(encoding="utf-8").strip().splitlines()
            lookdev = (project / "03_images_生成图片/LOOKDEV_TASKS.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(anchors), 7)
            self.assertEqual(len(lookdev), 12)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["hbg_bridge_digest"], bridge.bridge_digest)
            self.assertEqual(manifest["next_stage_status"], "waiting_for_host_imagegen")
            self.assertEqual(original_roots, {name: sha(project / name) for name in original_roots})

    def test_mismatched_bridge_digest_or_release_hash_fails_without_outputs(self) -> None:
        for field in ("hbg_bridge_digest", "release_text_sha256"):
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                project, payload, _, _, _ = build_phase3_project(base)
                payload[field] = "0" * 64
                with self.assertRaisesRegex(VisualStageCompileError, field):
                    compile_visual_stage(project, self._input(base, payload))
                self.assertFalse((project / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json").exists())

    def test_tampered_phase2_output_blocks_phase3(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            (project / "CHARACTERS.md").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(VisualStageCompileError, "output hash|bridge"):
                compile_visual_stage(project, self._input(base, payload))

    def test_invalid_input_leaves_no_partial_formal_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            payload["lookdev_tasks"] = payload["lookdev_tasks"][:-1]
            original_prompt = (project / "PROMPTS.md").read_bytes()
            with self.assertRaises(VisualStageCompileError):
                compile_visual_stage(project, self._input(base, payload))
            self.assertEqual((project / "PROMPTS.md").read_bytes(), original_prompt)
            self.assertFalse((project / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json").exists())
            self.assertFalse(any(project.glob(".visual-stage-*")))

    def test_same_input_is_unchanged_and_reordered_keys_are_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            path = self._input(base, payload)
            first = compile_visual_stage(project, path)
            hashes = {relative: sha(project / relative) for relative in (
                "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
                "03_images_生成图片/ANCHOR_TASKS.jsonl",
                "03_images_生成图片/LOOKDEV_TASKS.jsonl",
                "PROMPTS.md",
            )}
            reordered = {key: payload[key] for key in reversed(list(payload))}
            second = compile_visual_stage(project, self._input(base, reordered))
            self.assertEqual(second.status, "unchanged")
            self.assertEqual(first.visual_stage_digest, second.visual_stage_digest)
            self.assertEqual(hashes, {relative: sha(project / relative) for relative in hashes})

    def test_changed_input_or_post_compile_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            path = self._input(base, payload)
            compile_visual_stage(project, path)
            changed = deepcopy(payload)
            changed["book_look"]["emotional_temperature"] = "不同的视觉方向"
            with self.assertRaisesRegex(VisualStageConflict, "different visual stage"):
                compile_visual_stage(project, self._input(base, changed))
            (project / "PROMPTS.md").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(VisualStageConflict, "output hash"):
                compile_visual_stage(project, path)

    def test_user_modified_prompt_placeholder_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            (project / "PROMPTS.md").write_text("用户自己的提示词", encoding="utf-8")
            with self.assertRaisesRegex(VisualStageConflict, "PROMPTS.md"):
                compile_visual_stage(project, self._input(base, payload))
            self.assertEqual((project / "PROMPTS.md").read_text(encoding="utf-8"), "用户自己的提示词")

    def test_stage_manifest_failure_rolls_back_every_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, payload, _, _, _ = build_phase3_project(base)
            original_prompt = (project / "PROMPTS.md").read_bytes()
            with mock.patch(
                "book_video_factory.visual_stage.compiler.write_stage_manifest",
                side_effect=RuntimeError("injected visual stage manifest failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected visual"):
                    compile_visual_stage(project, self._input(base, payload))
            self.assertEqual((project / "PROMPTS.md").read_bytes(), original_prompt)
            for relative in (
                "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
                "03_images_生成图片/VISUAL_REFERENCE_MANIFEST.json",
                "03_images_生成图片/ANCHOR_TASKS.jsonl",
                "03_images_生成图片/LOOKDEV_TASKS.jsonl",
                "03_images_生成图片/VISUAL_STAGE_MANIFEST.json",
            ):
                self.assertFalse((project / relative).exists(), relative)

    def test_reference_manifest_binds_profile_and_kernel_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,payload,_,_,_=build_phase3_project(base)
            compile_visual_stage(project,self._input(base,payload))
            manifest=json.loads((project/"03_images_生成图片/VISUAL_REFERENCE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["kernel_path"].endswith("literary-cinematic-realism-v1.json"))
            self.assertEqual(len(manifest["kernel_sha256"]),64)
            for entry in manifest["selected_references"]:
                self.assertEqual(len(entry["profile_sha256"]),64)

    def test_missing_or_blank_phase2_recorded_at_is_rejected(self) -> None:
        for payload in ({}, {"recorded_at":""}, {"recorded_at":123}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(VisualStageCompileError,"recorded_at"):
                    _required_recorded_at(payload)


if __name__ == "__main__":
    unittest.main()
