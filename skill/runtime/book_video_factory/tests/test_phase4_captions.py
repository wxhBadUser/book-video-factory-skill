from __future__ import annotations

import unittest

from book_video_factory.audio_stage.media_probe import VttCue
from book_video_factory.audio_stage.pronunciation import compile_spoken_script

try:
    from book_video_factory.audio_stage.captions import CaptionAlignmentError, restore_display_captions
except ModuleNotFoundError:
    CaptionAlignmentError = RuntimeError  # type: ignore


def lex(display="圣地亚哥", spoken="圣地亚哥奥"):
    return {
        "schema_version":"pronunciation-lexicon.v1","release_id":"r1",
        "entries":[{"entry_id":"name","display":display,"spoken":spoken,"scope":"body","occurrence_policy":{"mode":"all"},"note":"test"}],
    }


class Phase4CaptionTests(unittest.TestCase):
    def test_restores_display_text_without_helper_leakage(self) -> None:
        comp=compile_spoken_script("圣地亚哥看海。",lex(),scope="body")
        cues=[VttCue(0,1,"圣地亚哥奥"),VttCue(1,2,"看海。")]
        captions=restore_display_captions(cues,comp,min_chars=2,max_chars=18,min_duration=0.2)
        self.assertEqual([c["text"] for c in captions],["圣地亚哥","看海。"])
        self.assertNotIn("奥","".join(c["text"] for c in captions))

    def test_split_replacement_is_merged_before_restoration(self) -> None:
        comp=compile_spoken_script("圣地亚哥出海。",lex(),scope="body")
        cues=[VttCue(0,0.5,"圣地"),VttCue(0.5,1,"亚哥奥"),VttCue(1,2,"出海。")]
        captions=restore_display_captions(cues,comp,min_chars=2,max_chars=18,min_duration=0.2)
        self.assertEqual(captions[0]["text"],"圣地亚哥")
        self.assertEqual(captions[0]["start"],0)
        self.assertEqual(captions[0]["end"],1)
        self.assertEqual(captions[0]["rawCueIndexes"],[0,1])
        self.assertEqual(captions[1]["rawCueIndexes"],[2])

    def test_chapter_separator_whitespace_does_not_leak_into_visible_caption(self) -> None:
        empty={"schema_version":"pronunciation-lexicon.v1","release_id":"r1","entries":[]}
        comp=compile_spoken_script("第一章。\n第二章。",empty,scope="body")
        cues=[VttCue(0,1,"第一章。"),VttCue(1,2,"第二章。")]
        captions=restore_display_captions(cues,comp,min_chars=2,max_chars=18,min_duration=0.2)
        self.assertEqual([item["text"] for item in captions],["第一章。","第二章。"] )
        self.assertTrue(all(item["text"] == item["text"].strip() for item in captions))

    def test_reordered_omitted_duplicated_or_unknown_spoken_text_fails(self) -> None:
        comp=compile_spoken_script("老人出海看鱼。",{"schema_version":"pronunciation-lexicon.v1","release_id":"r1","entries":[]},scope="body")
        bad=[
            [VttCue(0,1,"看鱼"),VttCue(1,2,"老人出海。")],
            [VttCue(0,1,"老人出海。")],
            [VttCue(0,1,"老人"),VttCue(1,2,"老人出海看鱼。")],
            [VttCue(0,1,"外星人")],
        ]
        for cues in bad:
            with self.assertRaises(CaptionAlignmentError):
                restore_display_captions(cues,comp,min_chars=1,max_chars=18,min_duration=0.1)

    def test_length_and_duration_rules_are_enforced(self) -> None:
        comp=compile_spoken_script("老人出海看见一条非常巨大的鱼。",{"schema_version":"pronunciation-lexicon.v1","release_id":"r1","entries":[]},scope="body")
        with self.assertRaises(CaptionAlignmentError):
            restore_display_captions([VttCue(0,1,"老人出海看见一条非常巨大的鱼。")],comp,min_chars=2,max_chars=6,min_duration=0.2)
        with self.assertRaises(CaptionAlignmentError):
            restore_display_captions([VttCue(0,0.1,"老人出海看见一条非常巨大的鱼。")],comp,min_chars=2,max_chars=30,min_duration=0.5)


if __name__=="__main__": unittest.main()
