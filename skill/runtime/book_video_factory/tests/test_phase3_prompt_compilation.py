from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase3_fixture_factory import build_phase3_input
from book_video_factory.reference_visuals.catalog import load_reference_catalog
from book_video_factory.visual_stage.contracts import validate_visual_stage_input
from book_video_factory.visual_stage.prompts import compile_visual_task_prompts


class Phase3PromptCompilationTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_reference_catalog()
        payload = validate_visual_stage_input(
            build_phase3_input(),
            phase2_characters=[{"character_id": "C001", "name": "圣地亚哥", "anchor_status": "pending"}],
            catalog=catalog,
        )
        self.catalog = catalog
        self.tasks = compile_visual_task_prompts(payload, catalog)

    def test_compiles_character_scene_object_and_twelve_lookdev_tasks(self) -> None:
        kinds = [task["task_kind"] for task in self.tasks]
        self.assertEqual(kinds.count("character_anchor"), 5)
        self.assertEqual(kinds.count("scene_anchor"), 1)
        self.assertEqual(kinds.count("object_anchor"), 1)
        self.assertEqual(kinds.count("lookdev"), 12)
        self.assertEqual(len(self.tasks), 19)
        self.assertEqual(len({task["task_id"] for task in self.tasks}), 19)

    def test_prompts_include_book_world_period_geography_materials_and_negatives(self) -> None:
        prompt = next(task["prompt"] for task in self.tasks if task["task_kind"] == "lookdev")
        for phrase in (
            "20世纪中叶",
            "古巴哈瓦那近海",
            "海明威式克制",
            "盐渍皮肤",
            "现代游艇",
            "no text",
            "style only",
            "do not copy",
        ):
            self.assertIn(phrase, prompt)

    def test_character_prompts_repeat_identity_invariants(self) -> None:
        prompts = [task["prompt"] for task in self.tasks if task["task_kind"] == "character_anchor"]
        self.assertEqual(len(prompts), 5)
        for prompt in prompts:
            self.assertIn("瘦削脸型", prompt)
            self.assertIn("深陷眼窝", prompt)
            self.assertIn("年龄改变", prompt)

    def test_non_front_character_views_depend_on_front_anchor(self) -> None:
        front = next(task for task in self.tasks if task.get("view") == "front")
        others = [task for task in self.tasks if task["task_kind"] == "character_anchor" and task.get("view") != "front"]
        self.assertEqual(front["depends_on_task_ids"], [])
        for task in others:
            self.assertEqual(task["identity_dependency_task_ids"], [front["task_id"]])
            self.assertIn(front["task_id"], task["depends_on_task_ids"])

    def test_lookdev_character_tasks_depend_on_character_front_not_gold_identity(self) -> None:
        front = next(task for task in self.tasks if task.get("view") == "front")
        character_lookdev = [
            task for task in self.tasks
            if task["task_kind"] == "lookdev" and "CHAR_C001" in task["anchor_refs"]
        ]
        self.assertTrue(character_lookdev)
        for task in character_lookdev:
            self.assertIn(front["task_id"], task["identity_dependency_task_ids"])
            self.assertEqual(task["identity_reference_ids"], [])
            self.assertTrue(set(task["style_reference_ids"]).issubset(self.catalog.by_id()))


    def test_lookdev_prompts_repeat_character_identity_locks(self) -> None:
        character_lookdev = [
            task for task in self.tasks
            if task["task_kind"] == "lookdev" and "CHAR_C001" in task["anchor_refs"]
        ]
        self.assertTrue(character_lookdev)
        for task in character_lookdev:
            prompt = task["prompt"]
            for phrase in (
                "古巴老渔夫圣地亚哥",
                "瘦削脸型",
                "深陷眼窝",
                "褪色浅色衬衫",
                "年龄改变",
                "脸型改变",
            ):
                self.assertIn(phrase, prompt)

    def test_task_records_are_deterministic_and_hash_bound(self) -> None:
        again = compile_visual_task_prompts(
            validate_visual_stage_input(
                build_phase3_input(),
                phase2_characters=[{"character_id": "C001", "name": "圣地亚哥", "anchor_status": "pending"}],
                catalog=self.catalog,
            ),
            self.catalog,
        )
        self.assertEqual(self.tasks, again)
        for task in self.tasks:
            self.assertEqual(len(task["prompt_sha256"]), 64)
            self.assertEqual(task["canvas"], {"width": 1920, "height": 1080, "orientation": "landscape"})
            self.assertEqual(task["generation_lane"], "host-imagegen")
            self.assertTrue(task["output_target"].endswith(".png"))

    def test_prompts_do_not_leak_local_absolute_paths(self) -> None:
        for task in self.tasks:
            prompt = task["prompt"]
            self.assertNotIn("/mnt/", prompt)
            self.assertNotIn("C:\\", prompt)
            self.assertNotIn("I:\\", prompt)


if __name__ == "__main__":
    unittest.main()
