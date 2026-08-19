from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from unittest import mock
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase2_fixture_factory import build_phase2_project, write_json
from book_video_factory.hbg_bridge.compiler import (
    HbgBridgeCompileError,
    HbgBridgeConflict,
    compile_hbg_bridge,
)
from book_video_factory.hbg_bridge.runner import HbgRunnerError, run_hbg_node


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Phase2BridgeCompilerTests(unittest.TestCase):
    def _input(self, base: Path, payload: dict) -> Path:
        path = base / "bridge-input.json"
        write_json(path, payload)
        return path

    def test_runtime_runner_rejects_non_phase_two_hbg_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(HbgRunnerError, "not permitted.*Phase 2"):
                run_hbg_node("build_narration.mjs", Path(temp))

    def test_valid_bridge_compiles_native_hbg_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, result, payload = build_phase2_project(base, approve=True)
            outcome = compile_hbg_bridge(project, self._input(base, payload))
            self.assertEqual(outcome.status, "created")
            for relative in (
                "SCRIPT_SOURCE.md", "SCRIPT.md", "CHARACTERS.md", "PROJECT_SPEC.json",
                "STORYBOARD_BASE.json", "HBG_STYLE.json",
                "02_story_script_故事脚本/script.narrator-essay.v1.json",
                "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json",
                "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json",
            ):
                self.assertTrue((project / relative).is_file(), relative)
            self.assertEqual((project / "SCRIPT_SOURCE.md").read_text(encoding="utf-8"),
                             result and json.loads((project / "02_story_script_故事脚本/SCRIPT_PACKAGE.json").read_text(encoding="utf-8"))["script"]["release_version"]["text"])
            style = json.loads((project / "HBG_STYLE.json").read_text(encoding="utf-8"))
            self.assertEqual(style["orientation"], "landscape")
            storyboard = json.loads((project / "STORYBOARD_BASE.json").read_text(encoding="utf-8"))
            self.assertEqual(storyboard[0]["chapterTitle"], "失败与再次出海")
            self.assertEqual(storyboard[0]["motion"], "hold")
            self.assertTrue(all(beat["motion"] == "hold" for beat in storyboard))

    def test_bridge_rejects_vendor_injected_camera_motion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            path = self._input(base, payload)
            original = (project / "STORYBOARD_BASE.json").read_bytes()

            def inject_motion(project_dir: Path) -> None:
                target = Path(project_dir) / "STORYBOARD_BASE.json"
                beats = json.loads(target.read_text(encoding="utf-8"))
                for beat in beats:
                    beat["motion"] = "zoom-in"
                target.write_text(
                    json.dumps(beats, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

            with mock.patch(
                "book_video_factory.hbg_bridge.compiler.validate_storyboard",
                side_effect=inject_motion,
            ):
                with self.assertRaisesRegex(HbgBridgeCompileError, "motion must be 'hold'"):
                    compile_hbg_bridge(project, path)
            self.assertEqual((project / "STORYBOARD_BASE.json").read_bytes(), original)
            self.assertFalse(
                (project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json").exists()
            )

    def test_canonical_project_input_path_can_be_used_as_compiler_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            path = project / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json"
            write_json(path, payload)

            outcome = compile_hbg_bridge(project, path)

            self.assertEqual(outcome.status, "created")
            self.assertTrue(outcome.manifest_path.is_file())

    def test_missing_approval_fails_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=False)
            original = (project / "SCRIPT.md").read_bytes()
            with self.assertRaisesRegex(HbgBridgeCompileError, "script approval"):
                compile_hbg_bridge(project, self._input(base, payload))
            self.assertEqual((project / "SCRIPT.md").read_bytes(), original)
            self.assertFalse((project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json").exists())
            self.assertFalse(any(project.glob(".bridge-staging-*")))

    def test_invalid_input_leaves_no_partial_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            payload["storyboard_beats"][0]["description"] = ""
            initial = {name: (project / name).read_bytes() for name in ("SCRIPT.md", "CHARACTERS.md", "PROJECT_SPEC.json", "STORYBOARD_BASE.json")}
            with self.assertRaises(HbgBridgeCompileError):
                compile_hbg_bridge(project, self._input(base, payload))
            for name, content in initial.items():
                self.assertEqual((project / name).read_bytes(), content)
            self.assertFalse((project / "HBG_STYLE.json").exists())
            self.assertFalse(any(project.glob(".bridge-staging-*")))

    def test_same_input_rerun_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            path = self._input(base, payload)
            first = compile_hbg_bridge(project, path)
            hashes = {p.name: sha(p) for p in [project / "SCRIPT.md", project / "PROJECT_SPEC.json", project / "HBG_STYLE.json"]}
            second = compile_hbg_bridge(project, path)
            self.assertEqual(first.bridge_digest, second.bridge_digest)
            self.assertEqual(second.status, "unchanged")
            self.assertEqual(hashes, {p.name: sha(p) for p in [project / "SCRIPT.md", project / "PROJECT_SPEC.json", project / "HBG_STYLE.json"]})

    def test_changed_bridge_input_conflicts_with_existing_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            compile_hbg_bridge(project, self._input(base, payload))
            changed = deepcopy(payload)
            changed["brand"]["episode_number"] = 2
            with self.assertRaisesRegex(HbgBridgeConflict, "different bridge"):
                compile_hbg_bridge(project, self._input(base, changed))

    def test_post_bridge_output_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            path = self._input(base, payload)
            compile_hbg_bridge(project, path)
            (project / "SCRIPT.md").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(HbgBridgeConflict, "output hash"):
                compile_hbg_bridge(project, path)

    def test_user_modified_placeholder_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            (project / "SCRIPT.md").write_text("用户自己的内容", encoding="utf-8")
            with self.assertRaisesRegex(HbgBridgeConflict, "SCRIPT.md"):
                compile_hbg_bridge(project, self._input(base, payload))
            self.assertEqual((project / "SCRIPT.md").read_text(encoding="utf-8"), "用户自己的内容")

    def test_stage_manifest_failure_restores_every_preexisting_root_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            tracked = (
                "SCRIPT_SOURCE.md", "SCRIPT.md", "CHARACTERS.md",
                "PROJECT_SPEC.json", "STORYBOARD_BASE.json",
            )
            originals = {relative: (project / relative).read_bytes() for relative in tracked}
            with mock.patch(
                "book_video_factory.hbg_bridge.compiler.write_stage_manifest",
                side_effect=RuntimeError("injected stage manifest failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected stage manifest failure"):
                    compile_hbg_bridge(project, self._input(base, payload))
            for relative, content in originals.items():
                self.assertEqual((project / relative).read_bytes(), content, relative)
            self.assertFalse((project / "HBG_STYLE.json").exists())
            self.assertFalse((project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json").exists())
            self.assertFalse(any(project.glob(".bridge-staging-*")))

    def test_semantically_identical_reordered_input_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            path = self._input(base, payload)
            first = compile_hbg_bridge(project, path)
            reordered = {key: payload[key] for key in reversed(list(payload))}
            second = compile_hbg_bridge(project, self._input(base, reordered))
            self.assertEqual(first.bridge_digest, second.bridge_digest)
            self.assertEqual(second.status, "unchanged")

    def test_bridge_manifest_publish_failure_restores_outputs_and_removes_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            tracked = ("SCRIPT_SOURCE.md", "SCRIPT.md", "CHARACTERS.md", "PROJECT_SPEC.json", "STORYBOARD_BASE.json")
            originals = {relative: (project / relative).read_bytes() for relative in tracked}
            real_replace = __import__("os").replace

            def fail_bridge_manifest(source, destination):
                if Path(source).name == ".HBG_BRIDGE_MANIFEST.json.tmp":
                    raise OSError("injected bridge manifest publish failure")
                return real_replace(source, destination)

            with mock.patch("book_video_factory.hbg_bridge.compiler.os.replace", side_effect=fail_bridge_manifest):
                with self.assertRaisesRegex(HbgBridgeCompileError, "injected bridge manifest"):
                    compile_hbg_bridge(project, self._input(base, payload))
            self.assertEqual({relative: (project / relative).read_bytes() for relative in tracked}, originals)
            self.assertFalse((project / "HBG_STYLE.json").exists())
            self.assertFalse(any((project / "02_story_script_故事脚本").glob(".HBG_BRIDGE_MANIFEST*")))

    def test_bridge_waits_for_visual_anchor_before_edge_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            outcome = compile_hbg_bridge(project, self._input(base, payload))
            manifest = json.loads(outcome.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["next_stage_status"], "ready_for_visual_anchor_generation")

    def test_modified_field_inside_initial_project_spec_is_not_treated_as_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            target = project / "PROJECT_SPEC.json"
            spec = json.loads(target.read_text(encoding="utf-8"))
            spec["narration"]["voice"] = "用户指定声线"
            target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            original = target.read_bytes()
            with self.assertRaisesRegex(HbgBridgeConflict, "PROJECT_SPEC.json"):
                compile_hbg_bridge(project, self._input(base, payload))
            self.assertEqual(target.read_bytes(), original)

    def test_hbg_vendor_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            lock = json.loads((ROOT.parent / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
            before = {relative: sha(ROOT.parent / "vendor/hbg-life-simulation" / relative) for relative in lock["files"]}
            compile_hbg_bridge(project, self._input(base, payload))
            after = {relative: sha(ROOT.parent / "vendor/hbg-life-simulation" / relative) for relative in lock["files"]}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
