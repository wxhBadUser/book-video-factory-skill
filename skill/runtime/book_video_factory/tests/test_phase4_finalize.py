from __future__ import annotations

import json
import tempfile
import unittest
import sys
from copy import deepcopy
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.manifests import sha256_file
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    symlink_or_skip,
    write_json,
    write_phase4_inputs,
)

try:
    from book_video_factory.audio_stage.compiler import (
        AudioStageConflict,
        AudioStageError,
        finalize_audio_stage,
        generate_audio_stage,
    )
except ImportError:
    finalize_audio_stage = None  # type: ignore
    AudioStageConflict = RuntimeError  # type: ignore
    AudioStageError = RuntimeError  # type: ignore


class Phase4FinalizeTests(unittest.TestCase):
    def prepare(self, base: Path):
        project = build_approved_phase3_project(base)
        input_path, lexicon_path = write_phase4_inputs(project)
        generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
        plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
        write_json(plan_path, build_storyboard_audio_plan(project))
        return project, plan_path

    def test_finalizes_storyboard_bindings_and_preserves_every_audio_hash(self) -> None:
        self.assertIsNotNone(finalize_audio_stage)
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            preliminary = json.loads((project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json").read_text(encoding="utf-8"))
            before = {path: digest for path, digest in preliminary["output_hashes"].items() if path.startswith("assets/audio/")}
            result = finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            self.assertEqual(result.status, "created")
            self.assertEqual(result.next_stage_status, "ready_for_image_task_planning")
            for relative in (
                "04_audio/CAPTION_BINDINGS.json",
                "04_audio/AUDIO_TIMELINE_AUDIT.json",
                "04_audio/AUDIO_STAGE_MANIFEST.json",
                "04_audio/STORYBOARD_BASE.audio-final.json",
                "audio_meta.json",
                "STORYBOARD.json",
            ):
                self.assertTrue((project / relative).is_file(), relative)
            after = {path: sha256_file(project / path) for path in before}
            self.assertEqual(after, before)
            storyboard = json.loads((project / "STORYBOARD.json").read_text(encoding="utf-8"))
            bindings = json.loads((project / "04_audio/CAPTION_BINDINGS.json").read_text(encoding="utf-8"))
            self.assertGreater(len(storyboard), 60)
            self.assertEqual(len(bindings["captions"]), len(json.loads((project / "audio_meta.json").read_text(encoding="utf-8"))["captions"]))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["next_stage_status"], "ready_for_image_task_planning")
            self.assertEqual(manifest["preliminary_audio_hashes"], before)
            self.assertFalse(manifest["external_edge_service_exercised"])

    def test_identical_rerun_is_unchanged_and_post_final_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            second = finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            self.assertEqual(second.status, "unchanged")
            target = project / "04_audio/CAPTION_BINDINGS.json"
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaises(AudioStageConflict):
                finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)

    def test_stale_preliminary_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["preliminary_manifest_sha256"] = "0" * 64
            write_json(plan_path, plan)
            with self.assertRaises(AudioStageError):
                finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)

    def test_changed_plan_after_finalization_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
            plan = json.loads(plan_path.read_text(encoding="utf-8")); plan["shots"][0]["description"] += "发生变化"
            write_json(plan_path, plan)
            with self.assertRaises(AudioStageConflict):
                finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)

    def test_audio_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            def drift(staging: Path) -> None:
                fake_hbg_audio_runner(staging)
                with (staging / "assets/audio/narration.m4a").open("ab") as stream:
                    stream.write(b"drift")
            with self.assertRaisesRegex(AudioStageError, "audio|hash|drift"):
                finalize_audio_stage(project, plan_path, runner=drift)
            self.assertFalse((project / "04_audio/AUDIO_STAGE_MANIFEST.json").exists())

    def test_failure_rolls_back_preliminary_root_files_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            before_meta = (project / "audio_meta.json").read_bytes()
            before_storyboard = (project / "STORYBOARD.json").read_bytes()
            def broken(_staging: Path) -> None:
                raise RuntimeError("injected HBG failure")
            with self.assertRaises(AudioStageError):
                finalize_audio_stage(project, plan_path, runner=broken)
            self.assertEqual((project / "audio_meta.json").read_bytes(), before_meta)
            self.assertEqual((project / "STORYBOARD.json").read_bytes(), before_storyboard)
            self.assertFalse((project / "04_audio/AUDIO_STAGE_MANIFEST.json").exists())


    def test_coordinated_preliminary_manifest_and_stage_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan_path = self.prepare(Path(temp))
            preliminary_path = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            preliminary = json.loads(preliminary_path.read_text(encoding="utf-8"))
            stage_relative = preliminary["stage_manifest_path"]
            stage_path = project / stage_relative
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            stage["producer"] = {"tool": "attacker"}
            write_json(stage_path, stage)
            stage_sha = sha256_file(stage_path)
            preliminary["output_hashes"][stage_relative] = stage_sha
            preliminary["stage_manifest_sha256"] = stage_sha
            write_json(preliminary_path, preliminary)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["preliminary_manifest_sha256"] = sha256_file(preliminary_path)
            write_json(plan_path, plan)
            with self.assertRaisesRegex(AudioStageError, "stage|manifest|producer|evidence"):
                finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)

    def test_finalize_rejects_storyboard_plan_under_symlinked_project_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, plan_path = self.prepare(base)
            official_dir = project / "04_audio"
            outside_dir = base / "outside-audio-stage"
            official_dir.rename(outside_dir)
            symlink_or_skip(self,official_dir,outside_dir,target_is_directory=True)
            linked_plan = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            with self.assertRaisesRegex(AudioStageError, "project-local|symlink|official"):
                finalize_audio_stage(project, linked_plan, runner=fake_hbg_audio_runner)


if __name__ == "__main__":
    unittest.main()
