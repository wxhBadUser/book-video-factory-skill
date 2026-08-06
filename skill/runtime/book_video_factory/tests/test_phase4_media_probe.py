from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from book_video_factory.audio_stage.media_probe import (
        MediaValidationError,
        parse_vtt,
        probe_audio,
        validate_vtt,
    )
except ModuleNotFoundError:
    MediaValidationError = RuntimeError  # type: ignore


def make_audio(path: Path, *, sample_rate: int = 48000, channels: int = 1, codec: str = "pcm_s16le") -> None:
    layout = "mono" if channels == 1 else "stereo"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration=1:sample_rate={sample_rate}",
        "-ac", str(channels), "-channel_layout", layout,
        "-c:a", codec, str(path),
    ], check=True)


def write_vtt(path: Path, blocks: list[tuple[float, float, str]]) -> None:
    def ts(value: float) -> str:
        h = int(value // 3600); value -= h * 3600
        m = int(value // 60); value -= m * 60
        s = int(value); ms = int(round((value - s) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    text = ["WEBVTT", ""]
    for start, end, caption in blocks:
        text.extend([f"{ts(start)} --> {ts(end)}", caption, ""])
    path.write_text("\n".join(text), encoding="utf-8")


class Phase4MediaProbeTests(unittest.TestCase):
    def test_valid_pcm_wav_and_aac_m4a_are_probed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wav = root / "body.wav"; make_audio(wav)
            m4a = root / "body.m4a"; make_audio(m4a, codec="aac")
            wav_probe = probe_audio(wav)
            m4a_probe = probe_audio(m4a)
            self.assertEqual((wav_probe.codec, wav_probe.sample_rate, wav_probe.channels), ("pcm_s16le", 48000, 1))
            self.assertEqual((m4a_probe.codec, m4a_probe.sample_rate, m4a_probe.channels), ("aac", 48000, 1))
            self.assertGreater(wav_probe.duration, 0)

    def test_corrupt_wrong_rate_channel_or_codec_fail_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corrupt = root / "bad.wav"; corrupt.write_bytes(b"not-media")
            with self.assertRaises(MediaValidationError): probe_audio(corrupt)
            wrong = root / "wrong.wav"; make_audio(wrong, sample_rate=44100, channels=2)
            result = probe_audio(wrong)
            self.assertNotEqual((result.sample_rate, result.channels), (48000, 1))

    def test_vtt_parser_and_validator_require_monotonic_full_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "body.vtt"
            write_vtt(path, [(0.0, 0.5, "老人出海"), (0.5, 1.0, "看见大海")])
            cues = parse_vtt(path)
            report = validate_vtt(cues, duration=1.0, expected_text="老人出海看见大海")
            self.assertEqual(report["cue_count"], 2)
            self.assertEqual(report["normalized_text"], "老人出海看见大海")

    def test_vtt_coverage_ignores_edge_dropped_chinese_book_title_bracket(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reveal.vtt"
            write_vtt(path, [(0.1, 2.7, "狂人日记》，鲁迅。")])
            report = validate_vtt(
                parse_vtt(path),
                duration=2.7,
                expected_text="《狂人日记》，鲁迅。",
            )
            self.assertEqual(report["normalized_text"], "狂人日记鲁迅")

    def test_vtt_rejects_malformed_overlap_out_of_range_empty_and_missing_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            malformed = root / "malformed.vtt"; malformed.write_text("WEBVTT\n\nnot-time\ntext\n")
            with self.assertRaises(MediaValidationError): parse_vtt(malformed)
            cases = [
                [(0.0, 0.7, "老人"), (0.6, 1.0, "出海")],
                [(0.0, 1.2, "老人出海")],
                [(0.0, 0.5, "")],
            ]
            for index, blocks in enumerate(cases):
                path = root / f"bad-{index}.vtt"; write_vtt(path, blocks)
                with self.assertRaises(MediaValidationError):
                    validate_vtt(parse_vtt(path), duration=1.0, expected_text="老人出海")
            missing = root / "missing.vtt"; write_vtt(missing, [(0.0, 1.0, "老人")])
            with self.assertRaises(MediaValidationError):
                validate_vtt(parse_vtt(missing), duration=1.0, expected_text="老人出海")


if __name__ == "__main__":
    unittest.main()
