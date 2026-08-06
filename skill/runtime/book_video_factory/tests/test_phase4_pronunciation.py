from __future__ import annotations

import unittest

from book_video_factory.audio_stage.contracts import AudioStageContractError

try:
    from book_video_factory.audio_stage.pronunciation import (
        PronunciationError,
        compile_spoken_script,
        map_spoken_interval_to_display,
        restore_spoken_span,
    )
except ModuleNotFoundError:
    PronunciationError = RuntimeError  # type: ignore


class Phase4PronunciationTests(unittest.TestCase):
    def lexicon(self, entries):
        return {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": entries}

    def entry(self, entry_id, display, spoken, *, scope="body", mode="all", count=None):
        policy = {"mode": mode}
        if count is not None:
            policy["count"] = count
        return {
            "entry_id": entry_id,
            "display": display,
            "spoken": spoken,
            "scope": scope,
            "occurrence_policy": policy,
            "note": "test",
        }

    def test_longest_first_replacement_and_exact_restore(self) -> None:
        text = "圣地亚哥看见亚哥。圣地亚哥没有回头。"
        lexicon = self.lexicon([
            self.entry("short", "亚哥", "亚哥儿"),
            self.entry("long", "圣地亚哥", "圣地亚哥奥"),
        ])
        result = compile_spoken_script(text, lexicon, scope="body")
        self.assertEqual(result.spoken_text, "圣地亚哥奥看见亚哥儿。圣地亚哥奥没有回头。")
        self.assertEqual(restore_spoken_span(result, 0, len(result.spoken_text)), text)
        self.assertEqual([s.entry_id for s in result.segments if s.entry_id], ["long", "short", "long"])

    def test_exact_occurrence_missing_required_and_noop_entries(self) -> None:
        entry = self.entry("name", "老人", "老仁", mode="exact", count=2)
        result = compile_spoken_script("老人看海，老人回家。", self.lexicon([entry]), scope="body")
        self.assertEqual(result.spoken_text.count("老仁"), 2)
        with self.assertRaises(PronunciationError):
            compile_spoken_script("老人只出现一次。", self.lexicon([entry]), scope="body")
        noop = self.entry("noop", "老人", "老人")
        with self.assertRaises(PronunciationError):
            compile_spoken_script("老人。", self.lexicon([noop]), scope="body")

    def test_scope_filters_entries(self) -> None:
        lexicon = self.lexicon([
            self.entry("body", "海明威", "海明微", scope="body"),
            self.entry("reveal", "海明威", "海明维", scope="reveal"),
        ])
        self.assertEqual(compile_spoken_script("海明威", lexicon, scope="body").spoken_text, "海明微")
        self.assertEqual(compile_spoken_script("海明威", lexicon, scope="reveal").spoken_text, "海明维")

    def test_punctuation_structure_change_and_overlapping_source_entries_fail(self) -> None:
        with self.assertRaises(PronunciationError):
            compile_spoken_script("老人。", self.lexicon([self.entry("bad", "老人", "老，仁")]), scope="body")
        with self.assertRaises(PronunciationError):
            compile_spoken_script(
                "圣地亚哥。",
                self.lexicon([
                    self.entry("a", "圣地亚哥", "圣地亚哥奥"),
                    self.entry("b", "地亚", "地呀"),
                ]),
                scope="body",
            )

    def test_interval_mapping_inside_expanded_replacement_is_monotonic(self) -> None:
        result = compile_spoken_script(
            "甲圣地亚哥乙",
            self.lexicon([self.entry("name", "圣地亚哥", "圣地亚哥奥")]),
            scope="body",
        )
        start, end = map_spoken_interval_to_display(result, 1, len(result.spoken_text) - 1)
        self.assertEqual((start, end), (1, 5))
        self.assertEqual(restore_spoken_span(result, 1, len(result.spoken_text) - 1), "圣地亚哥")

    def test_control_and_command_content_is_rejected_by_contract(self) -> None:
        from book_video_factory.audio_stage.contracts import validate_pronunciation_lexicon
        payload = self.lexicon([self.entry("cmd", "老人", "$(rm -rf /)")])
        with self.assertRaises(AudioStageContractError):
            validate_pronunciation_lexicon(__import__('pathlib').Path('.'), payload)


if __name__ == "__main__":
    unittest.main()
