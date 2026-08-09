"""Chain 3 (B06/B07): production captions must carry their REAL section register.

Two checks:
1. ``restore_display_captions`` deterministically tags each caption with the
   narrative_function (and characters/location/time_of_day) of the script
   section it falls within, via ``section_register``. A caption not covered by
   exactly one section is rejected (fail closed) -- no silent "plot".
2. ``CaptionUnit.from_mapping`` fails closed when ``narrative_function`` is
   missing/empty/unknown instead of defaulting to "plot".

These are the re-review's B06/B07: production captions previously carried no
register metadata and ``from_mapping`` collapsed everything to "plot", so the
caption-grouping gate was effectively dead in production.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.compiler import (
    AudioStageError,
    _build_section_register,
    _combine,
)
from book_video_factory.audio_stage.media_probe import VttCue
from book_video_factory.audio_stage.pronunciation import compile_spoken_script
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    CaptionSectionRegister,
    CaptionUnit,
    normalize_script_register,
)

EMPTY_LEX = {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": []}


def _merged_two_chapters() -> tuple:
    comp1 = compile_spoken_script("第一章出海。", EMPTY_LEX, scope="body")
    comp2 = compile_spoken_script("第二章理论。", EMPTY_LEX, scope="body")
    merged, chapter_ranges = _combine([comp1, comp2])
    return merged, chapter_ranges


class CaptionRegisterTaggingTests(unittest.TestCase):
    def test_restore_display_captions_tags_real_narrative_function(self) -> None:
        merged, chapter_ranges = _merged_two_chapters()
        register = [
            CaptionSectionRegister(
                display_start=chapter_ranges[0][0],
                display_end=chapter_ranges[0][1],
                narrative_function="plot",
            ),
            CaptionSectionRegister(
                display_start=chapter_ranges[1][0],
                display_end=chapter_ranges[1][1],
                narrative_function="theory",
            ),
        ]
        cues = [VttCue(0, 1, "第一章出海。"), VttCue(1, 2, "第二章理论。")]
        captions = restore_display_captions_tagged(cues, merged, register)
        self.assertEqual(captions[0]["narrative_function"], "plot")
        self.assertEqual(captions[1]["narrative_function"], "theory")

    def test_restore_display_captions_carries_characters_location_time_of_day(self) -> None:
        merged, chapter_ranges = _merged_two_chapters()
        register = [
            CaptionSectionRegister(
                display_start=chapter_ranges[0][0],
                display_end=chapter_ranges[0][1],
                narrative_function="plot",
                characters=("圣地亚哥",),
                location="海上",
                time_of_day="白天",
            ),
            CaptionSectionRegister(
                display_start=chapter_ranges[1][0],
                display_end=chapter_ranges[1][1],
                narrative_function="theory",
            ),
        ]
        cues = [VttCue(0, 1, "第一章出海。"), VttCue(1, 2, "第二章理论。")]
        captions = restore_display_captions_tagged(cues, merged, register)
        self.assertEqual(captions[0]["characters"], ["圣地亚哥"])
        self.assertEqual(captions[0]["location"], "海上")
        self.assertEqual(captions[0]["time_of_day"], "白天")
        self.assertEqual(captions[1]["characters"], [])

    def test_uncovered_caption_fails_closed(self) -> None:
        from book_video_factory.audio_stage.captions import CaptionAlignmentError, restore_display_captions

        merged, chapter_ranges = _merged_two_chapters()
        # Register only covers chapter 1; chapter-2 caption is left uncovered.
        register = [
            CaptionSectionRegister(
                display_start=chapter_ranges[0][0],
                display_end=chapter_ranges[0][1],
                narrative_function="plot",
            ),
        ]
        cues = [VttCue(0, 1, "第一章出海。"), VttCue(1, 2, "第二章理论。")]
        with self.assertRaises(CaptionAlignmentError):
            restore_display_captions(
                cues, merged, min_chars=2, max_chars=30, min_duration=0.1, section_register=register
            )

    def test_caption_unit_from_mapping_fails_closed_without_narrative_function(self) -> None:
        with self.assertRaises(CaptionGroupingError):
            CaptionUnit.from_mapping({"caption_id": "c1", "text": "x", "start": 0.0, "end": 1.0})
        with self.assertRaises(CaptionGroupingError):
            CaptionUnit.from_mapping(
                {"caption_id": "c1", "text": "x", "start": 0.0, "end": 1.0, "narrative_function": "vibes"}
            )

    def test_normalize_script_register_maps_and_rejects_unknown(self) -> None:
        self.assertEqual(normalize_script_register("hook"), "opening")
        self.assertEqual(normalize_script_register("ending_image"), "closing")
        self.assertEqual(normalize_script_register("theory"), "theory")
        self.assertEqual(normalize_script_register("world_setup"), "plot")
        with self.assertRaises(CaptionGroupingError):
            normalize_script_register("vibes")


def restore_display_captions_tagged(cues, merged, register):
    from book_video_factory.audio_stage.captions import restore_display_captions

    return restore_display_captions(
        cues, merged, min_chars=2, max_chars=30, min_duration=0.1, section_register=register
    )


_SECTION_FIXTURE = [
    {"narrative_function": "hook"},
    {"narrative_function": "character_entry", "characters": ["福贵"], "location": "村口", "timeOfDay": "白天"},
    {"narrative_function": "theory"},
    {"narrative_function": "ending_image"},
]


class SectionRegisterBuildTests(unittest.TestCase):
    """Chain 3 wiring: ``_build_section_register`` reads the real section source.

    The script package carries each section's true narrative_function (and any
    characters/location/time_of_day). Two layouts exist in the wild -- the
    canonical top-level ``performance_version.sections`` and the older
    ``script.performance_version.sections`` used by shipped book projects -- and
    both must resolve to a deterministic register. Anything else (missing
    sections, a count that disagrees with the compiled chapters, an unknown
    narrative_function) must fail closed so captions cannot be silently
    collapsed to "plot".
    """

    def _write_package(self, tmp: str, package: dict) -> Path:
        target = Path(tmp) / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        return Path(tmp)

    def _ranges(self, n: int) -> list[tuple[float, float]]:
        return [(float(i * 5), float((i + 1) * 5)) for i in range(n)]

    def test_build_section_register_nested_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"script": {"performance_version": {"sections": _SECTION_FIXTURE}}})
            register = _build_section_register(root, self._ranges(4))
        self.assertEqual(
            [entry.narrative_function for entry in register],
            ["opening", "plot", "theory", "closing"],
        )
        self.assertEqual(register[1].characters, ("福贵",))
        self.assertEqual(register[1].location, "村口")
        self.assertEqual(register[1].time_of_day, "白天")

    def test_build_section_register_top_level_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"performance_version": {"sections": _SECTION_FIXTURE}})
            register = _build_section_register(root, self._ranges(4))
        self.assertEqual(
            [entry.narrative_function for entry in register],
            ["opening", "plot", "theory", "closing"],
        )

    def test_build_section_register_null_fields_are_safe(self) -> None:
        sections = [
            {"narrative_function": "hook", "characters": None, "location": None, "timeOfDay": None},
            {"narrative_function": "plot", "characters": None},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"performance_version": {"sections": sections}})
            register = _build_section_register(root, self._ranges(2))
        self.assertEqual(register[0].characters, ())
        self.assertEqual(register[0].location, "")
        self.assertEqual(register[0].time_of_day, "")
        self.assertEqual(register[1].characters, ())

    def test_build_section_register_unknown_narrative_function_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"performance_version": {"sections": [{"narrative_function": "vibes"}]}})
            with self.assertRaises(CaptionGroupingError):
                _build_section_register(root, self._ranges(1))

    def test_build_section_register_count_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"performance_version": {"sections": _SECTION_FIXTURE}})
            with self.assertRaises(AudioStageError):
                _build_section_register(root, self._ranges(3))

    def test_build_section_register_missing_sections_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_package(tmp, {"schema_version": "x", "script": {"performance_version": {}}})
            with self.assertRaises(AudioStageError):
                _build_section_register(root, self._ranges(2))


if __name__ == "__main__":
    unittest.main()
