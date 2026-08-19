# -*- coding: utf-8 -*-
"""M4A static-still motion contract tests (M1-M10, offline)."""

import unittest

from book_video_factory.director_stage.simple_zoom import (
    ALLOWED_MOTIONS,
    BLOCKED_MOTIONS,
    MAX_ZOOM,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    SimpleZoomError,
    build_zoompan_filter,
    caption_safe_zone,
    plan_simple_zoom,
    validate_simple_zoom_plan,
)

HOLDS = [
    {"hold_id": "H07", "start": 0.0, "end": 3.35, "emotion": "sad", "narrative_function": "plot"},
    {"hold_id": "H08", "start": 3.35, "end": 8.6, "emotion": "sad", "narrative_function": "plot"},
    {"hold_id": "H09", "start": 8.6, "end": 15.1, "emotion": "warm", "narrative_function": "plot"},
    {"hold_id": "H10", "start": 15.1, "end": 23.0, "emotion": "tense", "narrative_function": "plot"},
    {"hold_id": "H11", "start": 23.0, "end": 31.2, "emotion": "tense", "narrative_function": "plot"},
    {"hold_id": "H12", "start": 31.2, "end": 32.8, "emotion": "impactful", "narrative_function": "plot"},
    {"hold_id": "H13", "start": 32.8, "end": 41.6, "emotion": "reflective", "narrative_function": "plot"},
]


class SimpleZoomTests(unittest.TestCase):
    def test_M1_new_production_allowed_set(self) -> None:
        self.assertEqual(ALLOWED_MOTIONS, {"hold"})

    def test_M2_pan_left_blocks(self) -> None:
        with self.assertRaises(SimpleZoomError):
            plan_simple_zoom([{**HOLDS[0], "motion": "pan-left"}])

    def test_M3_pan_right_blocks(self) -> None:
        with self.assertRaises(SimpleZoomError):
            plan_simple_zoom([{**HOLDS[0], "motion": "pan-right"}])

    def test_M3_legacy_zoom_names_are_not_translated(self) -> None:
        for legacy in ("zoom-in", "zoom-out", "slow_zoom_in", "slow_zoom_out", "static"):
            with self.subTest(motion=legacy):
                with self.assertRaises(SimpleZoomError):
                    plan_simple_zoom([{**HOLDS[0], "motion": legacy}])

    def test_M3_camera_intents_are_rejected(self) -> None:
        for intent in ("in", "out", "hold"):
            with self.subTest(intent=intent):
                with self.assertRaises(SimpleZoomError):
                    plan_simple_zoom(HOLDS, intents={"H07": intent})

    def test_M4_short_hold_stays_static(self) -> None:
        plan = plan_simple_zoom([{"hold_id": "H12", "start": 0.0, "end": 1.6}])
        entry = plan["motions"][0]
        self.assertEqual(entry["motion"], "hold")
        self.assertEqual(entry["zoom"], 0.0)
        validate_simple_zoom_plan(plan)

    def test_M5_zoom_is_always_zero(self) -> None:
        plan = plan_simple_zoom([{"hold_id": "H13", "start": 0.0, "end": 120.0}])
        entry = plan["motions"][0]
        self.assertEqual(entry["zoom"], 0.0)
        self.assertEqual(entry["zoom"], MAX_ZOOM)
        validate_simple_zoom_plan(plan)

    def test_M5_long_hold_stays_static(self) -> None:
        plan = plan_simple_zoom([
            {"hold_id": "a", "start": 0.0, "end": 3.0},
            {"hold_id": "b", "start": 3.0, "end": 8.0},
            {"hold_id": "c", "start": 8.0, "end": 20.0},
        ])
        zooms = {entry["hold_id"]: entry["zoom"] for entry in plan["motions"]}
        self.assertEqual(zooms, {"a": 0.0, "b": 0.0, "c": 0.0})
        self.assertTrue(all(entry["motion"] == "hold" for entry in plan["motions"]))

    def test_M6_every_hold_is_static(self) -> None:
        plan = plan_simple_zoom(HOLDS)
        validate_simple_zoom_plan(plan)
        for entry in plan["motions"]:
            frames = max(1, int(entry["duration"] * 30))
            f = build_zoompan_filter(entry, frames=frames, fps=30)
            self.assertEqual(entry["motion"], "hold")
            self.assertEqual(entry["zoom"], 0.0)
            self.assertNotIn("zoompan", f)
        self.assertTrue(all(entry["rationale"] for entry in plan["motions"]))

    def test_M7_no_zoom_plus_pan_combo(self) -> None:
        # Planning a slow zoom is rejected; zoompan is never emitted.
        with self.assertRaises(SimpleZoomError):
            plan_simple_zoom([{**HOLDS[0], "motion": "slow_zoom_in"}])
        plan = plan_simple_zoom(HOLDS)
        f = build_zoompan_filter(plan["motions"][0], frames=60)
        self.assertNotIn("zoompan", f)
        for blocked in BLOCKED_MOTIONS:
            self.assertNotIn(blocked, f)

    def test_M8_static_output_is_1920x1080(self) -> None:
        plan = plan_simple_zoom(HOLDS)
        for entry in plan["motions"]:
            f = build_zoompan_filter(entry, frames=max(1, int(entry["duration"] * 30)))
            self.assertIn(f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}", f)
            self.assertNotIn("zoompan", f)

    def test_M9_captions_stay_in_safe_zone(self) -> None:
        zone = caption_safe_zone()
        self.assertEqual(zone["width"], 1680)
        self.assertEqual(zone["height"], 900)
        self.assertGreaterEqual(zone["x"], 60)
        self.assertGreaterEqual(zone["y"], 60)

    def test_M10_hold_membership_unchanged(self) -> None:
        # The motion plan references the exact hold_ids/timing of the hold plan
        # and never re-orders or drops a hold.
        plan = plan_simple_zoom(HOLDS)
        hold_ids = [entry["hold_id"] for entry in plan["motions"]]
        self.assertEqual(hold_ids, [h["hold_id"] for h in HOLDS])
        for entry, hold in zip(plan["motions"], HOLDS):
            self.assertEqual(entry["start"], hold["start"])
            self.assertEqual(entry["end"], hold["end"])

    def test_validation_rejects_bad_motion_and_size(self) -> None:
        plan = plan_simple_zoom(HOLDS)
        broken = dict(plan)
        broken["output_size"] = {"width": 1280, "height": 720}
        with self.assertRaises(SimpleZoomError):
            validate_simple_zoom_plan(broken)
        broken = dict(plan)
        bad = dict(broken["motions"][0])
        bad["motion"] = "pan-left"
        broken["motions"] = [bad] + broken["motions"][1:]
        with self.assertRaises(SimpleZoomError):
            validate_simple_zoom_plan(broken)

    def test_validation_rejects_nonzero_zoom(self) -> None:
        plan = plan_simple_zoom(HOLDS)
        broken = dict(plan)
        bad = dict(broken["motions"][0])
        bad["zoom"] = 0.01
        broken["motions"] = [bad] + broken["motions"][1:]
        with self.assertRaises(SimpleZoomError):
            validate_simple_zoom_plan(broken)


if __name__ == "__main__":
    unittest.main()
