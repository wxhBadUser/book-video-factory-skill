from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.longform_contracts import (  # noqa: E402
    LongformContractError,
    validate_longform_manifest,
)


def locked_timeline(timing_source: str) -> dict:
    return {
        "schema_version": "1.0",
        "schema_id": "timeline.v1",
        "release_id": "v1-r1",
        "book": {"title": "老人与海", "author": "海明威"},
        "status": "locked",
        "caption_timing_source": timing_source,
        "shots": [{"shot_id": "S001"}],
        "captions": [{"caption_id": "C001", "start": 0.0, "end": 1.0, "text": "老人出海"}],
    }


class EdgeVttTimelineContractTests(unittest.TestCase):
    def test_locked_timeline_accepts_edge_vtt_as_master_timing(self) -> None:
        result = validate_longform_manifest(locked_timeline("edge_vtt"))
        self.assertEqual(result["caption_timing_source"], "edge_vtt")

    def test_removed_video_asset_contract_is_rejected(self) -> None:
        payload = {
            "schema_version": "1.0",
            "schema_id": "hero-video-assets.v1",
            "release_id": "v1-r1",
            "book": {"title": "老人与海", "author": "海明威"},
            "status": "draft",
            "assets": [],
        }
        with self.assertRaisesRegex(LongformContractError, "unsupported schema_id"):
            validate_longform_manifest(payload)

    def test_removed_voice_performance_contract_is_rejected(self) -> None:
        payload = {
            "schema_version": "1.0",
            "schema_id": "voice-performance.v1",
            "release_id": "v1-r1",
            "book": {"title": "老人与海", "author": "海明威"},
            "status": "draft",
            "chunks": [],
        }
        with self.assertRaisesRegex(LongformContractError, "unsupported schema_id"):
            validate_longform_manifest(payload)

    def test_locked_timeline_rejects_non_default_timing_source(self) -> None:
        with self.assertRaisesRegex(LongformContractError, "edge_vtt"):
            validate_longform_manifest(locked_timeline("estimated"))


if __name__ == "__main__":
    unittest.main()
