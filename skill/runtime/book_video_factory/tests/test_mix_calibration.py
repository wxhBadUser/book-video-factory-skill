from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from book_video_factory.manifests import sha256_file
from book_video_factory.render_stage.mix_calibration import (
    MixCalibrationError,
    approve_opening_mix,
    calibrate_opening_mix,
    verify_opening_mix_approval,
)
import test_phase7_render_stage as phase7


def _set_gain(project: Path, gain: float) -> None:
    style_path = project / "HBG_STYLE.json"
    style = json.loads(style_path.read_text(encoding="utf-8"))
    style["audio"] = {"bgmVolume": gain}
    phase7.write_json(style_path, style)
    preview_manifest = project / "07_render/OPENING_PREVIEW_MANIFEST.json"
    manifest = json.loads(preview_manifest.read_text(encoding="utf-8"))
    manifest["hbg_style_sha256"] = sha256_file(style_path)
    phase7.write_json(preview_manifest, manifest)


def _probe(path: Path, seconds: float | None) -> dict[str, float]:
    if "preview" in path.name:
        return {"duration_seconds": 18.0, "integrated_lufs": -14.0, "true_peak_dbtp": -3.5}
    return {
        "duration_seconds": 120.0 if seconds is None else min(120.0, seconds),
        "integrated_lufs": -15.3,
        "true_peak_dbtp": -4.2,
    }


class MixCalibrationTests(unittest.TestCase):
    def prepare(self, base: Path, gain: float = 0.1) -> tuple[Path, Path]:
        project, render_input, *_ = phase7.RenderStageTests().prepare_project(base, approve_mix=False)
        _set_gain(project, gain)
        return project, render_input

    def test_schema_is_closed_and_requires_fixed_mix_evidence(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas/mix_calibration.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("ducking", schema["required"])
        self.assertIn("encoded_true_peak_dbtp", schema["required"])

    def test_calibration_probes_source_first_twenty_and_hbg_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, render_input = self.prepare(Path(temp))
            calls: list[tuple[str, float | None]] = []

            def probe(path: Path, seconds: float | None) -> dict[str, float]:
                calls.append((path.relative_to(project).as_posix(), seconds))
                return _probe(path, seconds)

            result = calibrate_opening_mix(project, render_input, probe_runner=probe)
            candidate = json.loads(result.calibration_path.read_text(encoding="utf-8"))
            self.assertEqual(calls, [
                ("assets/audio/bgm/source.mp3", None),
                ("assets/audio/bgm/source.mp3", 20.0),
                ("assets/opening/preview.mp4", None),
            ])
            self.assertEqual(candidate["mix_mode"], "fixed")
            self.assertFalse(candidate["ducking"])
            self.assertAlmostEqual(candidate["gain_db"], 20.0 * math.log10(0.1), places=5)
            self.assertEqual(candidate["preview_duration_seconds"], 18.0)
            self.assertEqual(candidate["encoded_true_peak_dbtp"], -3.5)
            self.assertEqual(candidate["next_stage_status"], "ready_for_render_preflight")

    def test_rejects_short_preview_hot_peak_and_tiny_gain_adjustment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, render_input = self.prepare(Path(temp))

            def short(path: Path, seconds: float | None) -> dict[str, float]:
                value = _probe(path, seconds)
                if "preview" in path.name:
                    value["duration_seconds"] = 14.9
                return value

            with self.assertRaisesRegex(MixCalibrationError, "15|duration"):
                calibrate_opening_mix(project, render_input, probe_runner=short)

            def hot(path: Path, seconds: float | None) -> dict[str, float]:
                value = _probe(path, seconds)
                if "preview" in path.name:
                    value["true_peak_dbtp"] = -2.9
                return value

            with self.assertRaisesRegex(MixCalibrationError, "peak|3"):
                calibrate_opening_mix(project, render_input, probe_runner=hot)

            calibrate_opening_mix(project, render_input, probe_runner=_probe)
            _set_gain(project, 0.11)
            preview = project / "assets/opening/preview.mp4"
            preview.write_bytes(preview.read_bytes() + b"rerender")
            manifest_path = project / "07_render/OPENING_PREVIEW_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["preview_sha256"] = sha256_file(preview)
            manifest["preview_bytes"] = preview.stat().st_size
            phase7.write_json(manifest_path, manifest)
            with self.assertRaisesRegex(MixCalibrationError, "3|4|step"):
                calibrate_opening_mix(project, render_input, probe_runner=_probe)

    def test_rejects_preview_when_bound_audio_evidence_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, render_input = self.prepare(Path(temp))
            audio_manifest = project / "04_audio/AUDIO_STAGE_MANIFEST.json"
            audio_manifest.write_bytes(audio_manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(MixCalibrationError, "audio|binding|stale"):
                calibrate_opening_mix(project, render_input, probe_runner=_probe)

    def test_approval_binds_exact_gain_preview_hash_and_current_style(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, render_input = self.prepare(Path(temp), gain=0.14)
            result = calibrate_opening_mix(project, render_input, probe_runner=_probe)
            approved = approve_opening_mix(
                project,
                result.calibration_path,
                reviewer="Human Reviewer",
                note="Opening narration and fixed BGM balance reviewed.",
            )
            evidence = verify_opening_mix_approval(project, render_input)
            self.assertEqual(evidence["preview_sha256"], sha256_file(project / "assets/opening/preview.mp4"))
            self.assertEqual(evidence["gain_linear"], 0.14)
            self.assertEqual(approved.next_stage_status, "ready_for_render_preflight")
            preview = project / "assets/opening/preview.mp4"
            preview.write_bytes(preview.read_bytes() + b"tamper")
            with self.assertRaisesRegex(MixCalibrationError, "preview|stale|hash"):
                verify_opening_mix_approval(project, render_input)


if __name__ == "__main__":
    unittest.main()
