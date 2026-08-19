from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import phase1_fixture_factory
import phase2_fixture_factory
from phase1_fixture_factory import build_phase1_inputs, build_phase1_originality
from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    write_phase4_inputs,
)
from book_video_factory.audio_stage.compiler import generate_audio_stage, finalize_audio_stage
from book_video_factory.manifests import sha256_file
from book_video_factory.semantic_alignment.caption_contract import build_caption_visual_contract_from_project
from book_video_factory.semantic_alignment.caption_grouping import build_caption_grouping_from_project
from book_video_factory.semantic_alignment.scene_continuity import build_scene_continuity_from_project
from book_video_factory.director_stage.compiler import (
    DirectorStageError,
    DirectorStageConflict,
    _anchor_context,
    _expected,
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
        inputs = copy.deepcopy(build_phase1_inputs())
        old_opening = "一个人连续失败八十四天，还会不会再出海？"
        new_opening = "圣地亚哥连续失败八十四天，圣地亚哥还会不会再出海？"
        script = inputs["script"]
        for version_name in ("release_version", "performance_version", "audit_version"):
            version = script[version_name]
            for section in version["sections"]:
                if section["section_id"] == "S01":
                    section["text"] = section["text"].replace(old_opening, new_opening)
            version["text"] = "".join(section["text"] for section in version["sections"])
        script["script_text"] = script["release_version"]["text"]
        inputs["originality"] = build_phase1_originality(script["release_version"]["text"])
        with mock.patch.object(phase1_fixture_factory, "build_phase1_inputs", return_value=inputs), \
             mock.patch.object(phase2_fixture_factory, "build_phase1_inputs", return_value=inputs):
            project = build_approved_phase3_project(base)
        input_path, lexicon_path = write_phase4_inputs(project)
        generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
        plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
        write_json(plan_path, build_storyboard_audio_plan(project))
        finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
        build_caption_visual_contract_from_project(project, release_id="r1")
        build_caption_grouping_from_project(project)
        build_scene_continuity_from_project(project)
        return project

    def test_compiles_audio_bound_director_timeline_and_image_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.prepare(Path(temp))
            storyboard = json.loads((project / "STORYBOARD.json").read_text(encoding="utf-8"))
            grouping = json.loads(
                (project / "04_audio/CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8")
            )
            script_package = json.loads(
                (project / "02_story_script_故事脚本/SCRIPT_PACKAGE.json").read_text(encoding="utf-8")
            )
            s09 = next(
                section
                for section in script_package["script"]["performance_version"]["sections"]
                if section["section_id"] == "S09"
            )
            self.assertEqual(s09["narrative_function"], "theory")
            contract_document = json.loads(
                (project / "04_audio/CAPTION_VISUAL_CONTRACT.json").read_text(encoding="utf-8")
            )
            s09_contracts = [
                contract
                for contract in contract_document["contracts"].values()
                if contract["section_id"] == "S09"
            ]
            self.assertTrue(s09_contracts)
            self.assertTrue(all(contract["narrative_function"] == "theory" for contract in s09_contracts))
            self.assertTrue(all(contract["visual_mode"] == "symbolic_or_abstract" for contract in s09_contracts))
            groups = grouping["groups"]
            continuity = json.loads(
                (project / "04_audio/SCENE_CONTINUITY_SPANS.json").read_text(encoding="utf-8")
            )
            spans = continuity["spans"]
            self.assertEqual(storyboard[0]["captionIds"], [f"caption-{index:04d}" for index in range(1, 7)])
            # Semantic Shot Group: the first 26 consecutive captions share one
            # image (cast growth / same scene), which is coarser than the audio
            # storyboard's 6-caption first shot.
            self.assertEqual(groups[0]["caption_ids"], [f"caption-{index:04d}" for index in range(1, 27)])
            self.assertNotEqual(len(storyboard), len(groups))
            media_hashes_before = {
                path.relative_to(project).as_posix(): sha256_file(path)
                for path in project.rglob("*")
                if path.is_file() and path.suffix.lower() in {".vtt", ".wav", ".mp3", ".m4a"}
            }

            result = compile_director_stage(project)

            self.assertEqual(result.status, "created")
            self.assertEqual(result.next_stage_status, "awaiting_scene_assets")
            timeline = json.loads((project / "05_director/DIRECTOR_TIMELINE.json").read_text(encoding="utf-8"))
            self.assertEqual(len(timeline["scenes"]), len(spans))
            self.assertEqual(timeline["audio_stage_manifest_sha256"], result.audio_stage_manifest_sha256)
            self.assertTrue(all(scene["caption_ids"] for scene in timeline["scenes"]))
            self.assertTrue(all(scene["duration"] > 0 for scene in timeline["scenes"]))
            tasks = [json.loads(line) for line in (project / "05_director/IMAGE_TASKS.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(tasks), len(spans))
            self.assertTrue(all(task["prompt_sha256"] for task in tasks))
            self.assertTrue(all(task["output_target"].startswith("assets/generated/scenes/") for task in tasks))
            for span, scene, task in zip(spans, timeline["scenes"], tasks):
                self.assertEqual(scene["caption_ids"], span["caption_ids"])
                self.assertEqual(scene["start"], span["start"])
                self.assertEqual(scene["end"], span["end"])
                self.assertEqual(task["caption_ids"], span["caption_ids"])
                self.assertEqual(task["prompt_binding"]["group_id"], span["span_id"])
                self.assertEqual(task["prompt_binding"]["caption_ids"], span["caption_ids"])
                self.assertEqual(task["scene_id"], f"scene-continuity-{span['span_id'].lower()}")
                self.assertEqual(task["prompt_binding"]["scene_id"], task["scene_id"])
                self.assertEqual(task["prompt_binding"]["shot_id"], f"SHOT_SCENE_CONTINUITY_{span['span_id']}")
                self.assertTrue(task["source_beat_ids"])
                self.assertIsInstance(task["visual_proposition"], dict)
                self.assertTrue(task["prompt"])
            flattened = [caption_id for task in tasks for caption_id in task["caption_ids"]]
            expected_caption_ids = [caption_id for span in spans for caption_id in span["caption_ids"]]
            self.assertEqual(flattened, expected_caption_ids)
            self.assertEqual(len(flattened), len(set(flattened)))
            media_hashes_after = {
                path.relative_to(project).as_posix(): sha256_file(path)
                for path in project.rglob("*")
                if path.is_file() and path.suffix.lower() in {".vtt", ".wav", ".mp3", ".m4a"}
            }
            self.assertEqual(media_hashes_after, media_hashes_before)
            sheets = json.loads((project / "05_director/SHEET_MAP.json").read_text(encoding="utf-8"))
            singles = {item["task_id"] for item in sheets["single_tasks"]}
            for task in tasks:
                if task["risk_flags"]:
                    self.assertIn(task["task_id"], singles)
                    self.assertEqual(task["generation_mode"], "single")

    def test_missing_current_caption_contract_blocks_director(self) -> None:
        """A remediation release cannot enter Pilot through the legacy Director path."""
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "legacy"}})
            audio_dir = project / "04_audio"
            audio_dir.mkdir()
            write_json(audio_dir / "AUDIO_STAGE_MANIFEST.json", {"release_id": "r1"})
            with mock.patch(
                "book_video_factory.director_stage.compiler.audio_stage_status",
                return_value="ready_for_image_task_planning",
            ):
                with self.assertRaisesRegex(DirectorStageError, "caption visual contract"):
                    _expected(project)

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
