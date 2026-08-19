from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    symlink_or_skip,
    write_json,
    write_phase4_inputs,
)

try:
    from book_video_factory.audio_stage.compiler import finalize_audio_stage, generate_audio_stage
    from book_video_factory.audio_stage.status import audio_stage_status
    from book_video_factory.project import initialize_project
except ImportError:
    audio_stage_status = None  # type: ignore


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO_ROOT = _repository_root()
CLI = REPO_ROOT / "book_video_factory/scripts/run_audio_stage.py"
RUNTIME_CLI = REPO_ROOT / "skill/runtime/book_video_factory/scripts/run_audio_stage.py"


class Phase4CliTests(unittest.TestCase):
    def test_help_lists_generate_finalize_and_status(self) -> None:
        completed = subprocess.run([sys.executable, str(CLI), "--help"], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in ("generate", "finalize", "status"):
            self.assertIn(command, completed.stdout)

        for command in ("generate", "finalize"):
            command_help = subprocess.run(
                [sys.executable, str(CLI), command, "--help"],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )
            self.assertEqual(command_help.returncode, 0, command_help.stderr)
            self.assertIn("--provider", command_help.stdout)


    def test_source_and_bundled_runtime_cli_have_matching_help(self) -> None:
        outputs = []
        for cli in (CLI, RUNTIME_CLI):
            self.assertTrue(cli.is_file(), cli)
            completed = subprocess.run([sys.executable, str(cli), "--help"], cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_project_initialization_creates_strict_examples_but_no_audio_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp) / "warehouse", "book", "书名", "作者")
            for relative in (
                "04_audio/AUDIO_STAGE_INPUT.example.json",
                "04_audio/PRONUNCIATION_LEXICON.example.json",
                "04_audio/STORYBOARD_AUDIO_PLAN.example.json",
            ):
                self.assertTrue((project / relative).is_file(), relative)
                json.loads((project / relative).read_text(encoding="utf-8"))
            for relative in (
                "assets/audio/narration.m4a",
                "assets/audio/narration-full.vtt",
                "audio_meta.json",
                "04_audio/AUDIO_PRELIMINARY_MANIFEST.json",
            ):
                self.assertFalse((project / relative).exists(), relative)
            self.assertFalse((project / "progress.json").exists())

    def test_derived_status_transitions_from_visual_approval_to_final_audio(self) -> None:
        self.assertIsNotNone(audio_stage_status)
        with tempfile.TemporaryDirectory() as temp:
            project = build_approved_phase3_project(Path(temp))
            self.assertEqual(audio_stage_status(project, "r1"), "blocked_by_missing_audio_stage_input")
            input_path, lexicon_path = write_phase4_inputs(project)
            self.assertEqual(audio_stage_status(project, "r1"), "ready_for_edge_tts")
            generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
            self.assertEqual(audio_stage_status(project, "r1"), "awaiting_audio_storyboard_plan")
            plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path, build_storyboard_audio_plan(project))
            finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            self.assertEqual(audio_stage_status(project, "r1"), "ready_for_image_task_planning")
            (project / "04_audio/CAPTION_BINDINGS.json").write_bytes(b"tamper")
            self.assertEqual(audio_stage_status(project, "r1"), "blocked_by_audio_manifest_integrity")

    def test_status_rejects_symlinked_final_output_even_when_bytes_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_approved_phase3_project(Path(temp))
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
            plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path, build_storyboard_audio_plan(project))
            finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            bindings = project / "04_audio/CAPTION_BINDINGS.json"
            outside = Path(temp) / "outside-bindings.json"
            outside.write_bytes(bindings.read_bytes())
            bindings.unlink()
            symlink_or_skip(self,bindings,outside)
            self.assertEqual(audio_stage_status(project, "r1"), "blocked_by_audio_manifest_integrity")
            bindings.unlink()
            bindings.write_bytes(outside.read_bytes())

            manifest_path = project / "04_audio/AUDIO_STAGE_MANIFEST.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            original_stage = project / manifest["stage_manifest_path"]
            outside_stage = Path(temp) / "outside-stage.json"
            outside_stage.write_bytes(original_stage.read_bytes())
            manifest["stage_manifest_path"] = "../outside-stage.json"
            manifest["stage_manifest_sha256"] = __import__("hashlib").sha256(outside_stage.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(audio_stage_status(project, "r1"), "blocked_by_audio_manifest_integrity")
            manifest_path.write_bytes(manifest_bytes)

    def test_generate_cli_missing_edge_returns_one_json_error_and_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_approved_phase3_project(Path(temp))
            input_path, lexicon_path = write_phase4_inputs(project)
            # Use a child interpreter that cannot discover edge_tts. The current
            # CI environment is expected to be offline, but the test remains
            # deterministic by shadowing the package lookup through env.
            env = dict(**__import__("os").environ)
            env["BOOK_VIDEO_FACTORY_FORCE_MISSING_EDGE_TTS"] = "1"
            completed = subprocess.run(
                [sys.executable, str(CLI), "generate", "--project", str(project),
                 "--input", str(input_path), "--lexicon", str(lexicon_path),
                 "--provider", "edge-tts"],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["status"], "failed")
            self.assertIn("edge", payload["error"].lower())
            self.assertFalse((project / "audio_meta.json").exists())

    def test_generate_cli_rejects_provider_disagreement_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            audio_dir = project / "04_audio"
            audio_dir.mkdir(parents=True)
            input_path = audio_dir / "AUDIO_STAGE_INPUT.json"
            input_path.write_text(json.dumps({"provider": "edge-tts"}), encoding="utf-8")
            lexicon_path = audio_dir / "PRONUNCIATION_LEXICON.json"
            lexicon_path.write_text("{}", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(CLI), "generate", "--project", str(project),
                 "--input", str(input_path), "--lexicon", str(lexicon_path),
                 "--provider", "minimax"],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("disagrees", payload["error"])

    def test_status_cli_prints_one_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_approved_phase3_project(Path(temp))
            completed = subprocess.run(
                [sys.executable, str(CLI), "status", "--project", str(project), "--release-id", "r1"],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload, {"status": "blocked_by_missing_audio_stage_input", "release_id": "r1"})

    def test_codex_audio_reference_names_only_real_phase4_artifacts(self) -> None:
        reference = (REPO_ROOT / "skill/references/audio-stage.md").read_text(encoding="utf-8")
        self.assertIn("04_audio/AUDIO_STORYBOARD_GAPS.json", reference)
        self.assertIn("audio_meta.json", reference)
        self.assertNotIn("CAPTION_DISPLAY.json", reference)
        self.assertNotIn("AUDIO_STORYBOARD_GAP_REPORT.json", reference)
        self.assertNotIn("audio_meta.json\naudio_meta.json", reference)


if __name__ == "__main__":
    unittest.main()
