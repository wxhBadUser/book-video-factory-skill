"""M4A Narration Performance Director tests (A4/A5/A11).

Uses the real approved H07-H13 narration from the R3 sequence pilot so the
segmentation is validated against production content, not toy fixtures.
"""

from __future__ import annotations

import unittest

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_narration_performance_plan,
)
from book_video_factory.audio_stage.performance import (
    NarrationPerformanceError,
    NarrationSourceUnit,
    VARIANTS,
    build_narration_performance_plan,
    split_performance_segments,
)

# Real approved narration, R3 sequence pilot H07-H13 (qa/sequence-pilot-v1-r3).
# Each entry: (unit_id, display text, emotion_hint or None, flags)
_H07_H13 = [
    ("caption-0100", "等福贵回到家，他娘已经死了。", "sad", False, False, True),
    ("caption-0101", "凤霞一年前发了一场高烧，", "sad", False, False, False),
    ("caption-0102", "再不会说话了，", "sad", False, False, False),
    ("caption-0103", "也听不见了。", "sad", False, False, False),
    ("caption-0104", "但他回来了，家珍在，凤霞在，有庆也在。", "warm", False, False, False),
    ("caption-0105", "日子还能过。", "warm", False, False, False),
    ("caption-0106", "然后，龙二死了。", "tense", False, False, False),
    ("caption-0107", "土改，龙二成了恶霸地主，被拉到邻村枪毙。", "tense", False, False, False),
    ("caption-0108", "福贵也去看，", "tense", False, False, False),
    ("caption-0109", "龙二被五花大绑押过来，", "tense", False, False, False),
    ("caption-0110", "走过福贵身边时，", "tense", False, False, False),
    ("caption-0111", "他回过头，哭着喊：福贵，", "tense", True, False, False),
    ("caption-0112", "我是替你去死啊。", "impactful", True, True, False),
    ("caption-0113", "枪响了五声。", "impactful", False, False, True),
    ("caption-0114", "福贵走在回家的路上，脖子一阵阵冒冷气。", "reflective", False, False, False),
    ("caption-0115", "他摸摸自己的脸，", "reflective", False, False, False),
    ("caption-0116", "摸摸自己的胳膊，", "reflective", False, False, False),
    ("caption-0117", "都是好好的。", "reflective", False, False, False),
]


def _units() -> list[NarrationSourceUnit]:
    units = []
    for uid, text, emotion, dialogue_start, dialogue_end, is_impact in _H07_H13:
        # Spoken and display are identical here (no pronunciation entries in
        # this range), which is the common case.
        units.append(NarrationSourceUnit(
            unit_id=uid,
            text=text,
            spoken_text=text,
            chapter_id="ch1",
            narrative_function="plot",
            emotion_hint=emotion,
            is_dialogue_start=dialogue_start,
            is_dialogue_end=dialogue_end,
            is_impact=is_impact,
        ))
    return units


class PerformanceDirectorTests(unittest.TestCase):
    def test_A4_plan_covers_approved_narration_exactly_once(self) -> None:
        units = _units()
        for variant in ("A", "B", "C"):
            with self.subTest(variant=variant):
                plan = build_narration_performance_plan(
                    units, release_id="r3", variant=variant
                )
                covered = "".join(seg["spoken_text"] for seg in plan["segments"])
                expected = "".join(u.spoken_text for u in units)
                self.assertEqual(covered, expected, f"variant {variant} coverage broken")

    def test_A4_validation_accepts_built_plan(self) -> None:
        plan = build_narration_performance_plan(_units(), release_id="r3", variant="B")
        normalized = validate_narration_performance_plan(plan)
        self.assertEqual(normalized["variant"], "B")
        self.assertEqual(len(normalized["segments"]), len(plan["segments"]))

    def test_A5_performance_segment_differs_from_caption(self) -> None:
        units = _units()
        segments = split_performance_segments(units)
        # 18 captions should be compressed into a smaller number of expressive
        # beats (never one per caption; short captions may group up to 4).
        self.assertLess(len(segments), len(units))
        for segment in segments:
            self.assertLessEqual(len(segment.source_unit_ids), 4)
            self.assertGreaterEqual(len(segment.source_unit_ids), 1)
        # No caption may be dropped or duplicated.
        ids = [uid for seg in segments for uid in seg.source_unit_ids]
        self.assertEqual(ids, [u.unit_id for u in units])

    def test_A5_emotion_spectrum_is_preserved(self) -> None:
        segments = split_performance_segments(_units())
        modes = [seg.delivery_mode for seg in segments]
        # The H07-H13 pilot must keep the grief -> warmth -> tension -> impact
        # -> reflection arc (exact sequence may evolve, but the arc endpoints
        # and ordering must be respected).
        self.assertEqual(modes[0], "grief")
        self.assertIn("warmth", modes)
        self.assertIn("impact", modes)
        self.assertEqual(modes[-1], "reflection")

    def test_A5_intensity_ordering_a_lt_b_lt_c(self) -> None:
        plans = {
            v: build_narration_performance_plan(_units(), release_id="r3", variant=v)
            for v in ("A", "B", "C")
        }
        for segment_id in range(len(plans["A"]["segments"])):
            a = plans["A"]["segments"][segment_id]["intensity"]
            b = plans["B"]["segments"][segment_id]["intensity"]
            c = plans["C"]["segments"][segment_id]["intensity"]
            self.assertLessEqual(a, b)
            self.assertLessEqual(b, c)
            self.assertGreater(c, a)

    def test_A11_pronunciation_only_changes_spoken_not_display(self) -> None:
        unit = NarrationSourceUnit(
            unit_id="u1",
            text="《老人与海》，海明威。",
            spoken_text="《老人与海》，海明维。",  # compiled with the lexicon
        )
        plan = build_narration_performance_plan([unit], release_id="r1", variant="B")
        seg = plan["segments"][0]
        # Display (source) text keeps the dictionary name; spoken carries the
        # pronunciation replacement.
        self.assertIn("海明威", seg["source_text"])
        self.assertIn("海明维", seg["spoken_text"])
        self.assertNotIn("海明维", seg["source_text"])

    def test_A11_rejects_empty_unit(self) -> None:
        with self.assertRaises(NarrationPerformanceError):
            build_narration_performance_plan(
                [NarrationSourceUnit(unit_id="bad", text=" ", spoken_text=" ")],
                release_id="r1",
                variant="B",
            )

    def test_validation_rejects_bad_variant_and_bad_mode(self) -> None:
        plan = build_narration_performance_plan(_units(), release_id="r3", variant="B")
        broken = dict(plan)
        broken["variant"] = "D"
        with self.assertRaises(AudioStageContractError):
            validate_narration_performance_plan(broken)
        broken = dict(plan)
        bad_segment = dict(broken["segments"][0])
        bad_segment["delivery_mode"] = "shouting"
        broken["segments"] = [bad_segment] + broken["segments"][1:]
        with self.assertRaises(AudioStageContractError):
            validate_narration_performance_plan(broken)

    def test_variant_factors_match_spec(self) -> None:
        self.assertAlmostEqual(VARIANTS["A"]["intensity_factor"], 0.7)
        self.assertAlmostEqual(VARIANTS["B"]["intensity_factor"], 1.0)
        self.assertAlmostEqual(VARIANTS["C"]["intensity_factor"], 1.3)

    def test_plain_question_and_generic_zai_do_not_force_a_tense_or_warm_delivery(self) -> None:
        units = [
            NarrationSourceUnit(
                unit_id="opening-1",
                text="一个年轻人在田边遇到赶牛的老人。",
                spoken_text="一个年轻人在田边遇到赶牛的老人。",
            ),
            NarrationSourceUnit(
                unit_id="opening-2",
                text="年轻人问：这牛叫什么名字？",
                spoken_text="年轻人问：这牛叫什么名字？",
            ),
        ]
        plan = build_narration_performance_plan(units, release_id="r3", variant="B")
        self.assertEqual(plan["segments"][0]["delivery_mode"], "storytelling")


if __name__ == "__main__":
    unittest.main()
