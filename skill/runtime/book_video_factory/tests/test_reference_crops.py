from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.reference_crops import prepare_reference_crops  # noqa: E402


class ReferenceCropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.png"
        Image.new("RGB", (320, 180), (120, 160, 210)).save(self.source)
        self.valid_plan = {
            "references": [
                {
                    "reference_id": "REF01",
                    "source": str(self.source),
                    "crop_box": [20, 30, 180, 120],
                    "role": "palette only",
                    "output_name": "ref01.jpg",
                }
            ]
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_reference_crop_plan_rejects_out_of_bounds_box(self) -> None:
        plan = {
            "references": [
                {
                    "reference_id": "REF01",
                    "source": str(self.source),
                    "crop_box": [0, 0, 500, 500],
                    "role": "palette only",
                    "output_name": "ref01.jpg",
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "crop_box"):
            prepare_reference_crops(plan, self.root / "out")

    def test_reference_crop_plan_rejects_non_integer_box(self) -> None:
        plan = {
            "references": [
                {
                    "reference_id": "REF01",
                    "source": str(self.source),
                    "crop_box": [0, 0, 160.5, 90],
                    "role": "palette only",
                    "output_name": "ref01.jpg",
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "crop_box"):
            prepare_reference_crops(plan, self.root / "out")

    def test_reference_crop_plan_rejects_duplicate_output_name(self) -> None:
        item = self.valid_plan["references"][0]
        plan = {"references": [item, {**item, "reference_id": "REF02"}]}

        with self.assertRaisesRegex(ValueError, "output_name"):
            prepare_reference_crops(plan, self.root / "out")

    def test_reference_crop_manifest_records_real_hash_and_dimensions(self) -> None:
        result = prepare_reference_crops(self.valid_plan, self.root / "out")

        asset = result["assets"][0]
        self.assertEqual(asset["width"], 160)
        self.assertEqual(asset["height"], 90)
        self.assertEqual(len(asset["sha256"]), 64)
        self.assertTrue(Path(asset["path"]).is_file())
        self.assertEqual(asset["role"], "palette only")

    def test_reference_crop_output_is_deterministic(self) -> None:
        first = prepare_reference_crops(self.valid_plan, self.root / "first")
        second = prepare_reference_crops(self.valid_plan, self.root / "second")

        self.assertEqual(
            first["assets"][0]["sha256"],
            second["assets"][0]["sha256"],
        )


if __name__ == "__main__":
    unittest.main()
