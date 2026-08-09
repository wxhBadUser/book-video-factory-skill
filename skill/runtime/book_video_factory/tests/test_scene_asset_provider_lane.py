"""Chain 5 (re-review #24): a registered scene asset's provider must equal the
production task's ``generation_lane``.

The re-review charge: provenance was launderable because ``register_scene_asset``
only checked that the provider was *some* known provider, not the specific lane
the task was planned for. Now the asset provider must match ``generation_lane``
exactly, so a gemini-web asset can never be logged under a host-imagegen lane.
"""

from __future__ import annotations

import unittest

from book_video_factory.production_visuals.registry import (
    SceneAssetError,
    _validate_asset_provider,
)


class SceneAssetProviderLaneTests(unittest.TestCase):
    def test_matching_provider_passes(self) -> None:
        # Must not raise.
        _validate_asset_provider("host-imagegen", "host-imagegen")

    def test_mismatched_provider_fails(self) -> None:
        with self.assertRaises(SceneAssetError):
            _validate_asset_provider("gemini-web", "host-imagegen")

    def test_unknown_provider_fails(self) -> None:
        with self.assertRaises(SceneAssetError):
            _validate_asset_provider("vibes-provider", "host-imagegen")

    def test_missing_generation_lane_fails(self) -> None:
        with self.assertRaises(SceneAssetError):
            _validate_asset_provider("host-imagegen", None)

    def test_lane_override_cannot_spoof_provider(self) -> None:
        # A task planned for flow-web cannot accept a host-imagegen asset.
        with self.assertRaises(SceneAssetError):
            _validate_asset_provider("host-imagegen", "flow-web")


if __name__ == "__main__":
    unittest.main()
