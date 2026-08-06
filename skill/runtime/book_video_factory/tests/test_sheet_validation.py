from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from book_video_factory.production_visuals.sheet_validation import (
    SheetValidationError,
    validate_scene_sheet,
)


def make_sheet(path: Path, *, orientation: str = "landscape") -> None:
    panel_width, panel_height = ((1920, 1080) if orientation == "landscape" else (1080, 1920))
    outer = gutter = 8
    width = panel_width * 2 + outer * 2 + gutter
    height = panel_height * 2 + outer * 2 + gutter
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    colors = ((160, 30, 30), (30, 160, 30), (30, 30, 160), (160, 120, 30))
    boxes = (
        (outer, outer, outer + panel_width, outer + panel_height),
        (outer + panel_width + gutter, outer, width - outer, outer + panel_height),
        (outer, outer + panel_height + gutter, outer + panel_width, height - outer),
        (outer + panel_width + gutter, outer + panel_height + gutter, width - outer, height - outer),
    )
    for box, color in zip(boxes, colors, strict=True):
        draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=color)
    image.save(path)


class SheetValidationTests(unittest.TestCase):
    def test_valid_sheet_is_split_by_pristine_hbg_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            mapping = project / "05_director/SHEET_MAP.json"
            mapping.parent.mkdir(parents=True)
            targets = [f"assets/generated/scenes/S{index}.png" for index in range(1, 5)]
            mapping.write_text(json.dumps({"sheet_groups": [{
                "sheet_id": "SHEET_0001",
                "task_ids": ["S1", "S2", "S3", "S4"],
                "split_targets": targets,
            }]}), encoding="utf-8")
            (project / "HBG_STYLE.json").write_text(
                json.dumps(
                    {
                        "orientation": "landscape",
                        "canvas": {"width": 1920, "height": 1080},
                    }
                ),
                encoding="utf-8",
            )
            source = base / "valid.png"
            make_sheet(source)
            script = Path(__file__).resolve().parents[1] / "scripts/split_scene_sheet.py"
            completed = subprocess.run(
                [sys.executable, str(script), "--project", str(project), "--sheet-id", "SHEET_0001", "--source", str(source)],
                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            for relative in targets:
                with Image.open(project / relative) as panel:
                    self.assertEqual(panel.size, (1920, 1080))

    def test_valid_landscape_sheet_has_four_equal_ordered_native_panels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sheet.png"
            make_sheet(source)
            order = ["S1", "S2", "S3", "S4"]
            result = validate_scene_sheet(source, orientation="landscape", panel_order=order, expected_panel_order=order)
            self.assertEqual((result.width, result.height), (3864, 2184))
            self.assertEqual(result.panel_order, order)
            self.assertEqual(len(result.panel_boxes), 4)
            self.assertEqual({(box[2] - box[0], box[3] - box[1]) for box in result.panel_boxes}, {(1920, 1080)})

    def test_valid_portrait_sheet_uses_native_portrait_panels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sheet.png"
            make_sheet(source, orientation="portrait")
            order = ["S1", "S2", "S3", "S4"]
            result = validate_scene_sheet(source, orientation="portrait", panel_order=order)
            self.assertEqual((result.width, result.height), (2184, 3864))
            self.assertTrue(all(box[3] - box[1] > box[2] - box[0] for box in result.panel_boxes))

    def test_wrong_canvas_and_panel_order_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            wrong = base / "wrong.png"
            Image.new("RGB", (3000, 2000), (20, 20, 20)).save(wrong)
            with self.assertRaisesRegex(SheetValidationError, "canvas|four|2x2"):
                validate_scene_sheet(wrong, orientation="landscape", panel_order=["S1", "S2", "S3", "S4"])
            valid = base / "valid.png"
            make_sheet(valid)
            with self.assertRaisesRegex(SheetValidationError, "order"):
                validate_scene_sheet(valid, orientation="landscape", panel_order=["S2", "S1", "S3", "S4"], expected_panel_order=["S1", "S2", "S3", "S4"])

    def test_nonblack_or_discontinuous_gutter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sheet.png"
            make_sheet(source)
            with Image.open(source) as opened:
                image = opened.convert("RGB")
            ImageDraw.Draw(image).rectangle((1930, 400, 1933, 700), fill=(220, 40, 40))
            image.save(source)
            with self.assertRaisesRegex(SheetValidationError, "gutter|pollution|black"):
                validate_scene_sheet(source, orientation="landscape", panel_order=["S1", "S2", "S3", "S4"])

    def test_split_wrapper_never_calls_hbg_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            mapping = project / "05_director/SHEET_MAP.json"
            mapping.parent.mkdir(parents=True)
            mapping.write_text(json.dumps({"sheet_groups": [{
                "sheet_id": "SHEET_0001",
                "task_ids": ["S1", "S2", "S3", "S4"],
                "split_targets": [f"assets/generated/scenes/S{index}.png" for index in range(1, 5)],
            }]}), encoding="utf-8")
            invalid = base / "invalid.png"
            Image.new("RGB", (100, 100), (255, 0, 0)).save(invalid)
            script = Path(__file__).resolve().parents[1] / "scripts/split_scene_sheet.py"
            spec = importlib.util.spec_from_file_location("split_scene_sheet_for_test", script)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            with mock.patch.object(sys, "path", [str(script.parent), *sys.path]):
                spec.loader.exec_module(module)
            argv = [str(script), "--project", str(project), "--sheet-id", "SHEET_0001", "--source", str(invalid)]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(module.subprocess, "run") as runner:
                with self.assertRaises(SystemExit):
                    module.main()
                runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
