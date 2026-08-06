from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import build_bridge_input
from book_video_factory.hbg_bridge.script_export import (
    HbgScriptExportError,
    build_script_exports,
)


class Phase2ScriptExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = build_phase1_inputs()["script"]
        self.bridge = build_bridge_input()

    def test_source_is_exact_frozen_release_text_bytes(self) -> None:
        exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        expected = self.script["release_version"]["text"].encode("utf-8")
        self.assertEqual(exports.source_bytes, expected)

    def test_chaptered_script_preserves_all_spoken_text(self) -> None:
        exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        body = "".join(chapter.text for chapter in exports.chapters)
        self.assertEqual(body, self.script["release_version"]["text"])
        self.assertIn("## 第一章｜失败与再次出海", exports.script_markdown)
        self.assertIn("## 第四章｜回到岸上", exports.script_markdown)

    def test_every_section_is_covered_once_in_order(self) -> None:
        exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        ids = [sid for chapter in exports.chapters for sid in chapter.section_ids]
        expected = [item["section_id"] for item in self.script["release_version"]["sections"]]
        self.assertEqual(ids, expected)

    def test_chapter_cues_are_deterministic_unique_and_ordered(self) -> None:
        one = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        two = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        self.assertEqual([c.cue for c in one.chapters], [c.cue for c in two.chapters])
        release = self.script["release_version"]["text"]
        cursor = 0
        for chapter in one.chapters:
            self.assertEqual(release.count(chapter.cue), 1)
            found = release.find(chapter.cue, cursor)
            self.assertGreaterEqual(found, cursor)
            cursor = found + len(chapter.cue)

    def test_duplicate_or_missing_section_mapping_fails(self) -> None:
        chapters = deepcopy(self.bridge["chapters"])
        chapters[1]["section_ids"].append("S01")
        with self.assertRaises(HbgScriptExportError):
            build_script_exports("老人与海", self.script, chapters)

    def test_impossible_unique_cue_fails_closed(self) -> None:
        script = deepcopy(self.script)
        script["release_version"]["sections"] = [
            {"section_id": "S01", "narrative_function": "hook", "text": "甲"},
            {"section_id": "S02", "narrative_function": "ending_image", "text": "甲"},
        ]
        script["release_version"]["text"] = "甲甲"
        chapters = [
            {"chapter_id": "CH01", "title": "一", "section_ids": ["S01"]},
            {"chapter_id": "CH02", "title": "二", "section_ids": ["S02"]},
        ]
        with self.assertRaisesRegex(HbgScriptExportError, "unique cue"):
            build_script_exports("测试", script, chapters)

    def test_script_json_is_a_deep_copy(self) -> None:
        exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        exports.script_json["release_id"] = "changed"
        self.assertEqual(self.script["release_id"], "r1")


if __name__ == "__main__":
    unittest.main()
