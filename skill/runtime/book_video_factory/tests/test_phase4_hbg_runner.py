from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase4_fixture_factory import build_approved_phase3_project, write_phase4_inputs
from book_video_factory.hbg_bridge.runner import HbgRunnerError, run_hbg_node
from book_video_factory.audio_stage import hbg_adapter
from book_video_factory.audio_stage.media_probe import parse_vtt, validate_vtt

try:
    from book_video_factory.audio_stage.hbg_adapter import (
        prepare_audio_staging_project,
        run_hbg_caption_audit,
        run_hbg_density_audit,
        run_hbg_narration,
    )
except ModuleNotFoundError:
    pass


class Phase4HbgRunnerTests(unittest.TestCase):
    def test_edge_srt_payload_is_normalized_to_real_webvtt_after_hbg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subtitle = root / "assets/audio/narration-full.vtt"
            subtitle.parent.mkdir(parents=True)
            subtitle.write_text(
                "1\n00:00:00,100 --> 00:00:01,050\n真实字幕\n\n"
                "2\n00:00:01,000 --> 00:00:03,287\n格式测试。\n",
                encoding="utf-8",
            )
            normalizer = getattr(hbg_adapter, "normalize_hbg_subtitles_to_webvtt", None)
            self.assertIsNotNone(normalizer)

            normalizer(root)

            content = subtitle.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("WEBVTT\n\n"), content)
            self.assertIn("00:00:00.100 --> 00:00:01.050", content)
            self.assertIn("00:00:01.000 --> 00:00:03.287", content)
            cues = parse_vtt(subtitle)
            self.assertEqual((cues[0].start, cues[0].end), (0.1, 1.05))
            report = validate_vtt(cues, duration=3.287, expected_text="真实字幕格式测试。")
            self.assertEqual(report["cue_count"], 2)

    def test_hbg_rerun_restores_preexisting_webvtt_bytes_after_temporary_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subtitle = root / "assets/audio/narration-full.vtt"
            subtitle.parent.mkdir(parents=True)
            original = b"WEBVTT\n\n00:00.100 --> 00:03.287\nreal timeline\n"
            subtitle.write_bytes(original)

            def inspect_hbg_input(*args, **kwargs):
                self.assertIn(
                    "00:00:00.100 --> 00:00:03.287",
                    subtitle.read_text(encoding="utf-8"),
                )
                return mock.Mock(returncode=0)

            with mock.patch(
                "book_video_factory.audio_stage.hbg_adapter.run_hbg_node",
                side_effect=inspect_hbg_input,
            ):
                run_hbg_narration(root)

            self.assertEqual(subtitle.read_bytes(), original)

    def test_capability_whitelist_blocks_cross_stage_and_arbitrary_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with self.assertRaisesRegex(HbgRunnerError, "permitted|capability|Phase"):
                run_hbg_node("build_narration.mjs", project)
            with self.assertRaises(HbgRunnerError):
                run_hbg_node("init_project_style.mjs", project, capability="phase4")
            with self.assertRaises(HbgRunnerError):
                run_hbg_node("../render_streaming_ffmpeg.mjs", project, capability="phase4")

    def test_phase4_can_invoke_only_three_pristine_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "book_video_factory.hbg_bridge.runner._verify_vendor", return_value="a" * 40
        ), mock.patch("book_video_factory.hbg_bridge.runner.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="{}", stderr="")
            for script in (
                "build_narration.mjs",
                "audit_caption_semantics.mjs",
                "audit_storyboard_density.mjs",
            ):
                run_hbg_node(script, Path(temp), capability="phase4")
            called = [Path(call.args[0][1]).name for call in run.call_args_list]
            self.assertEqual(called, [
                "build_narration.mjs",
                "audit_caption_semantics.mjs",
                "audit_storyboard_density.mjs",
            ])

    def test_staging_uses_spoken_script_and_does_not_modify_project_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_approved_phase3_project(base / "source")
            input_path, _ = write_phase4_inputs(project)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            originals = {name: (project / name).read_bytes() for name in (
                "SCRIPT.md", "PROJECT_SPEC.json", "HBG_STYLE.json", "STORYBOARD_BASE.json"
            )}
            staging = base / "staging"
            prepare_audio_staging_project(
                project,
                staging,
                spoken_script="# HBG 旁白执行稿\n\n## 第一章｜测试\n\n圣地亚哥奥出海。\n",
                phase4_input=payload,
                storyboard_base=json.loads((project / "STORYBOARD_BASE.json").read_text(encoding="utf-8")),
            )
            self.assertIn("圣地亚哥奥", (staging / "SCRIPT.md").read_text(encoding="utf-8"))
            spec = json.loads((staging / "PROJECT_SPEC.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["narration"]["voice"], payload["voice"])
            self.assertEqual(spec["narration"]["bodyRate"], payload["body_rate"])
            self.assertEqual(spec["narration"]["leadText"], payload["lead_text"])
            for name, expected in originals.items():
                self.assertEqual((project / name).read_bytes(), expected)

    def test_adapter_wrappers_use_phase4_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "book_video_factory.audio_stage.hbg_adapter.run_hbg_node"
        ) as run:
            root = Path(temp)
            run_hbg_narration(root)
            run_hbg_caption_audit(root)
            storyboard = root / "STORYBOARD.json"; storyboard.write_text("[]")
            run_hbg_density_audit(root)
            self.assertEqual(
                [(c.args[0], c.kwargs["capability"]) for c in run.call_args_list],
                [
                    ("build_narration.mjs", "phase4"),
                    ("audit_caption_semantics.mjs", "phase4"),
                    ("audit_storyboard_density.mjs", "phase4"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
