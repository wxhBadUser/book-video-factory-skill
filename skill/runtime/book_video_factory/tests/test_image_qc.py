from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.image_qc import (  # noqa: E402
    analyze_image,
    inspect_image_batch,
    validate_reference_envelope,
)


class ImageQCTests(unittest.TestCase):
    def test_custom_high_key_envelope_can_pass_bright_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bright = Path(temp) / "bright.png"
            Image.new("RGB", (320, 180), (180, 190, 210)).save(bright)

            report = inspect_image_batch(
                [{"asset_id": "BRIGHT", "path": bright}],
                reference_envelope={
                    "median_luma": [100, 220],
                    "dark_pixel_share_percent": [0, 20],
                    "mean_saturation": [0, 255],
                    "warm_pixel_share_percent": [0, 100],
                },
            )

            self.assertEqual(report["machine_diagnostic_status"], "pass")
            self.assertEqual(
                report["reference_envelope"]["median_luma"],
                (100.0, 220.0),
            )

    def test_custom_envelope_rejects_reversed_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "median_luma"):
            validate_reference_envelope(
                {
                    "median_luma": [145, 100],
                    "dark_pixel_share_percent": [0, 20],
                    "mean_saturation": [45, 115],
                    "warm_pixel_share_percent": [20, 80],
                }
            )

    def test_custom_envelope_rejects_missing_metric(self) -> None:
        with self.assertRaisesRegex(ValueError, "keys"):
            validate_reference_envelope(
                {
                    "median_luma": [100, 145],
                    "dark_pixel_share_percent": [0, 20],
                    "mean_saturation": [45, 115],
                }
            )

    def test_reports_light_shadow_color_and_batch_imbalance_without_faking_human_qa(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dark = root / "dark.png"
            warm = root / "warm.png"
            Image.new("RGB", (320, 180), (8, 12, 20)).save(dark)
            Image.new("RGB", (320, 180), (210, 145, 70)).save(warm)

            dark_metrics = analyze_image(dark)
            report = inspect_image_batch(
                [
                    {"asset_id": "DARK", "path": dark},
                    {"asset_id": "WARM", "path": warm},
                ]
            )

            self.assertGreater(dark_metrics["dark_pixel_share_percent"], 95)
            self.assertEqual(dark_metrics["aspect_ratio"], "16:9")
            self.assertEqual(report["human_review_status"], "pending")
            self.assertEqual(report["machine_diagnostic_status"], "warn")
            self.assertIn("median_luma", report["batch_metrics"])


if __name__ == "__main__":
    unittest.main()
