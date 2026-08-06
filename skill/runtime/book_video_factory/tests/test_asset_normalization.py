from __future__ import annotations

import tempfile
import json
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image

from book_video_factory.production_visuals.staging import (
    AssetNormalizationError,
    normalize_scene_asset,
)


def staged(project: Path, name: str, size: tuple[int, int]) -> Path:
    path = project / "06_visual_production/staging" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (45, 95, 145)).save(path)
    return path


class AssetNormalizationTests(unittest.TestCase):
    def test_normalization_cli_resolves_flattened_staging_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            staged(project, "provider/SCENE_S09.png", (1536, 1024))
            script = Path(__file__).resolve().parents[1] / "scripts/normalize_scene_asset.py"
            completed = subprocess.run([
                sys.executable, str(script), "--project", str(project),
                "--expected-output-target", "nested/SCENE_S09.png",
                "--output-target", "assets/generated/scenes/SCENE_S09.png",
            ], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["final_size"], [1920, 1080])

    def test_letterbox_normalizes_provider_size_and_binds_source_and_final_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            source = staged(project, "scene.png", (1536, 1024))
            result = normalize_scene_asset(
                project, source, "assets/generated/scenes/scene.png",
                orientation="landscape", strategy="letterbox", asset_tier="standard",
            )
            self.assertEqual(result.delivered_size, (1536, 1024))
            self.assertEqual(result.final_size, (1920, 1080))
            self.assertEqual(len(result.source_sha256), 64)
            self.assertEqual(len(result.final_sha256), 64)
            with Image.open(result.output_path) as image:
                self.assertEqual(image.size, (1920, 1080))

    def test_safe_crop_is_allowed_for_standard_asset_but_not_hero_or_identity(self) -> None:
        for tier in ("hero", "identity"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as temp:
                project = Path(temp) / "project"
                source = staged(project, "scene.png", (1600, 1000))
                with self.assertRaisesRegex(AssetNormalizationError, "crop|Hero|identity|tier"):
                    normalize_scene_asset(
                        project, source, "assets/generated/scenes/scene.png",
                        orientation="landscape", strategy="crop", asset_tier=tier,
                    )
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            source = staged(project, "scene.png", (1600, 1000))
            result = normalize_scene_asset(
                project, source, "assets/generated/scenes/scene.png",
                orientation="landscape", strategy="crop", asset_tier="standard",
            )
            self.assertEqual(result.final_size, (1920, 1080))

    def test_excessive_crop_path_escape_and_overwrite_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            source = staged(project, "square.png", (1024, 1024))
            with self.assertRaisesRegex(AssetNormalizationError, "crop|loss"):
                normalize_scene_asset(project, source, "assets/generated/scenes/square.png", orientation="landscape", strategy="crop", asset_tier="standard")
            with self.assertRaises(AssetNormalizationError):
                normalize_scene_asset(project, source, "../escape.png", orientation="landscape", strategy="letterbox", asset_tier="standard")
            output = project / "assets/generated/scenes/existing.png"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"existing")
            with self.assertRaisesRegex(AssetNormalizationError, "overwrite|exists"):
                normalize_scene_asset(project, source, "assets/generated/scenes/existing.png", orientation="landscape", strategy="letterbox", asset_tier="standard")


if __name__ == "__main__":
    unittest.main()
