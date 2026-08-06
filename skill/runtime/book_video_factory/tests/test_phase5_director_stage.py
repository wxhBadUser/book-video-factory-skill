from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    write_phase4_inputs,
)
from book_video_factory.audio_stage.compiler import generate_audio_stage, finalize_audio_stage
from book_video_factory.director_stage.compiler import (
    DirectorStageError,
    DirectorStageConflict,
    _anchor_context,
    _validate_storyboard,
    _sheet_plan,
    compile_director_stage,
)


class DirectorStageTests(unittest.TestCase):
    def test_anchor_context_uses_a_registered_crowd_view_instead_of_inventing_front(self) -> None:
        profile = {"character_anchors": [{
            "character_id": "C004", "anchor_id": "CHAR_C004", "prompt_subject": "period crowd",
            "invariants": ["varied faces"], "wardrobe": ["plain cloth"],
            "forbidden_changes": ["duplicate faces"],
        }]}
        visual_assets = {"assets": [
            {"task_id": "ANCHOR_C004_CROWD_MID"},
            {"task_id": "ANCHOR_C004_CROWD_WIDE"},
        ]}

        _continuity, identity_tasks = _anchor_context(profile, visual_assets)

        self.assertEqual(identity_tasks["C004"], "ANCHOR_C004_CROWD_MID")
        self.assertNotIn("ANCHOR_C004_FRONT", identity_tasks.values())

    def test_safe_sheet_grouping_spans_chapter_boundaries_for_matching_continuity(self) -> None:
        tasks = [
            {
                "task_id": f"SCENE_{index}", "scene_id": f"scene-{index}",
                "risk_flags": [], "generation_mode": "2x2", "anchor_refs": ["C001"],
                "palette_id": "P1", "lighting_id": "L1",
                "output_target": f"assets/generated/scenes/scene-{index}.png",
            }
            for index in range(1, 5)
        ]
        scenes = [
            {"id": f"scene-{index}", "chapter": index, "participants": {"allowed": ["C001"]}}
            for index in range(1, 5)
        ]

        sheet_map = _sheet_plan(tasks, scenes)

        self.assertEqual(sheet_map["sheet_task_count"], 4)
        self.assertEqual(sheet_map["single_task_count"], 0)
        self.assertEqual(sheet_map["sheet_groups"][0]["task_ids"], [f"SCENE_{i}" for i in range(1, 5)])

    def test_accepts_only_bounded_real_media_tail_after_last_edge_cue(self) -> None:
        audio_meta = {"opening": {"bodyStart": 9.503}, "body": {"duration": 5.88}}
        scene = {
            "id": "tail", "start": 9.503, "end": 15.328, "duration": 5.825,
            "captionIds": ["caption-0001"], "riskFlags": [], "motion": "hold",
            "intentionalHold": True,
        }

        self.assertEqual(_validate_storyboard([scene], audio_meta)[0]["id"], "tail")
        scene["end"] = 15.282
        scene["duration"] = round(scene["end"] - scene["start"], 3)
        with self.assertRaisesRegex(DirectorStageError, "complete body audio"):
            _validate_storyboard([scene], audio_meta)

    def prepare(self, base: Path) -> Path:
        project = build_approved_phase3_project(base)
        input_path, lexicon_path = write_phase4_inputs(project)
        generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
        plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
        write_json(plan_path, build_storyboard_audio_plan(project))
        finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
        return project

    def test_compiles_audio_bound_director_timeline_and_image_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.prepare(Path(temp))
            result = compile_director_stage(project)
            self.assertEqual(result.status, "created")
            self.assertEqual(result.next_stage_status, "awaiting_scene_assets")
            timeline = json.loads((project / "05_director/DIRECTOR_TIMELINE.json").read_text(encoding="utf-8"))
            storyboard = json.loads((project / "STORYBOARD.json").read_text(encoding="utf-8"))
            self.assertEqual(len(timeline["scenes"]), len(storyboard))
            self.assertEqual(timeline["audio_stage_manifest_sha256"], result.audio_stage_manifest_sha256)
            self.assertTrue(all(scene["caption_ids"] for scene in timeline["scenes"]))
            self.assertTrue(all(scene["duration"] > 0 for scene in timeline["scenes"]))
            tasks = [json.loads(line) for line in (project / "05_director/IMAGE_TASKS.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(tasks), len(storyboard))
            self.assertTrue(all(task["prompt_sha256"] for task in tasks))
            self.assertTrue(all(task["output_target"].startswith("assets/generated/scenes/") for task in tasks))
            sheets = json.loads((project / "05_director/SHEET_MAP.json").read_text(encoding="utf-8"))
            singles = {item["task_id"] for item in sheets["single_tasks"]}
            for task in tasks:
                if task["risk_flags"]:
                    self.assertIn(task["task_id"], singles)
                    self.assertEqual(task["generation_mode"], "single")

    def test_atomic_staging_is_created_on_the_project_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.prepare(Path(temp))
            real_temporary_directory = tempfile.TemporaryDirectory

            def same_volume_staging(*args, **kwargs):
                self.assertEqual(Path(kwargs["dir"]).resolve(), project.parent.resolve())
                return real_temporary_directory(*args, **kwargs)

            with mock.patch(
                "book_video_factory.director_stage.compiler.tempfile.TemporaryDirectory",
                side_effect=same_volume_staging,
            ):
                self.assertEqual(compile_director_stage(project).status, "created")

    def test_identical_rerun_is_unchanged_and_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.prepare(Path(temp))
            first = compile_director_stage(project)
            second = compile_director_stage(project)
            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "unchanged")
            path = project / "05_director/DIRECTOR_TIMELINE.json"
            path.write_bytes(path.read_bytes() + b"tamper")
            with self.assertRaises(DirectorStageConflict):
                compile_director_stage(project)

    def test_audio_or_visual_evidence_change_invalidates_director_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.prepare(Path(temp))
            compile_director_stage(project)
            audio = project / "audio_meta.json"
            payload = json.loads(audio.read_text(encoding="utf-8"))
            payload["voice"] = "tampered"
            write_json(audio, payload)
            with self.assertRaises(DirectorStageConflict):
                compile_director_stage(project)


if __name__ == "__main__":
    unittest.main()
