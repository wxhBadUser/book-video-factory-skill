"""Part 8: Voice Performance Plan planner + contract tests (design §13.1)."""

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_voice_audition_approval,
    validate_voice_performance_plan,
)
from book_video_factory.audio_stage.voice_performance import (
    build_caption_ssml,
    plan_caption,
    plan_voice_performance,
)

_SHA = "a" * 64


class VoicePerformancePlannerTests(unittest.TestCase):
    def test_death_caption_triggers_grief(self) -> None:
        entry = plan_caption("c1", "有庆死了。")["c1"]
        self.assertEqual(entry["rate"], "-15%")
        self.assertEqual(entry["emotion_hint"], "grief-still")
        self.assertEqual(entry["pause_ms_after"], 800)

    def test_cry_caption_emphasizes_following_word(self) -> None:
        entry = plan_caption("c2", "凤霞哭着点了点头。")["c2"]
        self.assertEqual(entry["pitch"], "-1Hz")
        self.assertTrue(entry["emphasis_words"], "should emphasize a word after 哭")
        self.assertIn(entry["emphasis_words"][0], "凤霞哭着点了点头。")

    def test_abstract_mode_slows_and_pauses(self) -> None:
        entry = plan_caption("c3", "人为什么还要活着？", mode="Abstract")["c3"]
        self.assertEqual(entry["rate"], "-5%")
        self.assertEqual(entry["pause_ms_after"], 600)

    def test_paragraph_start_pauses_before(self) -> None:
        entry = plan_caption("c4", "日子总要继续过下去。", is_paragraph_start=True)["c4"]
        self.assertEqual(entry["pause_ms_before"], 400)

    def test_scene_boundary_pauses_before(self) -> None:
        entry = plan_caption("c5", "第二年春天。", is_scene_boundary=True)["c5"]
        self.assertEqual(entry["pause_ms_before"], 600)

    def test_neutral_caption_stays_default(self) -> None:
        entry = plan_caption("c6", "太阳升起来了。")["c6"]
        self.assertEqual(entry["rate"], "+0%")
        self.assertEqual(entry["pitch"], "+0Hz")
        self.assertEqual(entry["pause_ms_before"], 0)
        self.assertEqual(entry["pause_ms_after"], 0)

    def test_plan_voice_performance_assembles_plan(self) -> None:
        plan = plan_voice_performance(
            [
                {"caption_id": "c1", "text": "有庆死了。"},
                {"caption_id": "c2", "text": "人为什么还要活着？", "is_paragraph_start": True},
            ],
            audio_meta_sha256=_SHA,
            release_id="huozhe-r1",
            modes={"c2": "Abstract"},
        )
        self.assertEqual(plan["schema_version"], "voice-performance-plan.v1")
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(len(plan["captions"]), 2)
        self.assertEqual(plan["captions"]["c1"]["rate"], "-15%")
        self.assertEqual(plan["captions"]["c2"]["rate"], "-5%")


class VoicePerformanceSsmlTests(unittest.TestCase):
    def test_ssml_wraps_prosody_and_breaks(self) -> None:
        ssml = build_caption_ssml("有庆死了。", rate="-15%", pause_ms_after=800)
        self.assertIn("<speak", ssml)
        self.assertIn('rate="-15%"', ssml)
        self.assertIn('<break time="800ms"/>', ssml)
        self.assertIn("有庆死了。", ssml)

    def test_ssml_escapes_markup(self) -> None:
        ssml = build_caption_ssml("他说 <b>你好</b> & 再见")
        self.assertNotIn("<b>", ssml)
        self.assertIn("&lt;b&gt;", ssml)
        self.assertIn("&amp;", ssml)

    def test_ssml_emphasis_word(self) -> None:
        ssml = build_caption_ssml("凤霞哭了。", emphasis_words=["凤霞"])
        self.assertIn("<emphasis", ssml)
        self.assertIn("凤霞", ssml)


class VoicePerformancePlanContractTests(unittest.TestCase):
    def _plan(self) -> dict:
        return plan_voice_performance(
            [{"caption_id": "c1", "text": "有庆死了。"}],
            audio_meta_sha256=_SHA,
            release_id="huozhe-r1",
        )

    def test_valid_plan_accepted(self) -> None:
        self.assertEqual(validate_voice_performance_plan(self._plan())["status"], "draft")

    def test_bad_rate_rejected(self) -> None:
        plan = self._plan()
        plan["captions"]["c1"]["rate"] = "fast"
        with self.assertRaises(AudioStageContractError):
            validate_voice_performance_plan(plan)

    def test_bad_audio_meta_sha_rejected(self) -> None:
        plan = self._plan()
        plan["audio_meta_sha256"] = "not-a-hash"
        with self.assertRaises(AudioStageContractError):
            validate_voice_performance_plan(plan)

    def test_wrong_status_rejected(self) -> None:
        plan = self._plan()
        plan["status"] = "sent"
        with self.assertRaises(AudioStageContractError):
            validate_voice_performance_plan(plan)


class VoiceAuditionApprovalTests(unittest.TestCase):
    def _approval(self, status: str = "approved") -> dict:
        return {
            "schema_version": "voice-audition-approval.v1",
            "release_id": "huozhe-r1",
            "voice_performance_plan_sha256": _SHA,
            "reviewer": "Director",
            "status": status,
            "note": "Auditioned, prosody approved.",
        }

    def test_approved_passes(self) -> None:
        self.assertEqual(validate_voice_audition_approval(self._approval())["status"], "approved")

    def test_draft_blocks_full_tts(self) -> None:
        with self.assertRaises(AudioStageContractError):
            validate_voice_audition_approval(self._approval(status="draft"))

    def test_missing_approval_blocks_full_tts(self) -> None:
        with self.assertRaises(AudioStageContractError):
            validate_voice_audition_approval({
                "schema_version": "voice-audition-approval.v1",
                "release_id": "huozhe-r1",
                "voice_performance_plan_sha256": _SHA,
                "reviewer": "Director",
                "status": "approved",
            })


class VoicePerformanceCompilerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="vpp-int-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_load_ssml_returns_overrides(self) -> None:
        from book_video_factory.audio_stage.compiler import load_voice_performance_ssml

        plan = plan_voice_performance(
            [{"caption_id": "c1", "text": "有庆死了。"}],
            audio_meta_sha256=_SHA,
            release_id="huozhe-r1",
        )
        plan_path = self.root / "VPP.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        ssml = load_voice_performance_ssml(plan_path)
        self.assertIn("c1", ssml)
        self.assertIn("有庆死了。", ssml["c1"])
        self.assertIn('rate="-15%"', ssml["c1"])

    def test_require_audition_approved_passes(self) -> None:
        from book_video_factory.audio_stage.compiler import require_voice_audition_approved

        approval = {
            "schema_version": "voice-audition-approval.v1",
            "release_id": "huozhe-r1",
            "voice_performance_plan_sha256": _SHA,
            "reviewer": "Director",
            "status": "approved",
            "note": "ok",
        }
        path = self.root / "VPA.json"
        path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(require_voice_audition_approved(path)["status"], "approved")

    def test_require_audition_draft_blocks(self) -> None:
        from book_video_factory.audio_stage.compiler import require_voice_audition_approved

        approval = {
            "schema_version": "voice-audition-approval.v1",
            "release_id": "huozhe-r1",
            "voice_performance_plan_sha256": _SHA,
            "reviewer": "Director",
            "status": "draft",
            "note": "not yet",
        }
        path = self.root / "VPA.json"
        path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(AudioStageContractError):
            require_voice_audition_approved(path)


if __name__ == "__main__":
    unittest.main()
