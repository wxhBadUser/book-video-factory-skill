from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from book_video_factory.production_visuals.staging import (
    StagingResolutionError,
    resolve_staged_asset,
)


class StagingResolverTests(unittest.TestCase):
    def test_resolves_flattened_provider_output_by_verified_basename(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            delivered = project / "06_visual_production/staging/provider-flat/SCENE_S01.png"
            delivered.parent.mkdir(parents=True)
            Image.new("RGB", (1536, 1024), (30, 80, 120)).save(delivered)
            result = resolve_staged_asset(project, "assets/generated/scenes/SCENE_S01.png")
            self.assertEqual(result.path, delivered.resolve())
            self.assertEqual((result.width, result.height), (1536, 1024))
            self.assertEqual(len(result.sha256), 64)

    def test_duplicate_basename_and_missing_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            staging = project / "06_visual_production/staging"
            for folder in ("one", "two"):
                target = staging / folder / "scene.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1920, 1080), (20, 30, 40)).save(target)
            with self.assertRaisesRegex(StagingResolutionError, "collision|duplicate|multiple"):
                resolve_staged_asset(project, "nested/scene.png")
            with self.assertRaisesRegex(StagingResolutionError, "missing|not found"):
                resolve_staged_asset(project, "nested/absent.png")


if __name__ == "__main__":
    unittest.main()

