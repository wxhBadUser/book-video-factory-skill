from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VENDOR = REPO / "vendor/hbg-life-simulation"
LOCK = VENDOR / "UPSTREAM_LOCK.json"
EXPECTED_COMMIT = "63aa262d88f18c6058b205c2dd582cf909b219a4"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HbgVendorIntegrityTests(unittest.TestCase):
    def test_vendor_lock_and_mit_license_exist(self) -> None:
        self.assertTrue(LOCK.is_file(), "UPSTREAM_LOCK.json is missing")
        license_path = VENDOR / "LICENSE"
        self.assertTrue(license_path.is_file(), "HBG MIT LICENSE is missing")
        self.assertIn("MIT License", license_path.read_text(encoding="utf-8"))

    def test_vendor_lock_records_expected_upstream_and_all_file_hashes(self) -> None:
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "hbg-upstream-lock.v1")
        self.assertEqual(payload["repository"], "Mr-funny/hbg-life-simulation")
        self.assertEqual(payload["commit"], EXPECTED_COMMIT)
        self.assertEqual(payload["vendor_policy"], "pristine-vendor-plus-external-wrappers")
        self.assertEqual(payload["local_patches"], [])
        files = payload["files"]
        self.assertGreaterEqual(len(files), 25)
        actual_paths = {
            path.relative_to(VENDOR).as_posix()
            for path in VENDOR.rglob("*")
            if path.is_file() and path.name != "UPSTREAM_LOCK.json"
        }
        self.assertEqual(set(files), actual_paths)
        mismatches = {
            relative: {"expected": expected, "actual": sha256(VENDOR / relative)}
            for relative, expected in files.items()
            if (VENDOR / relative).is_file() and sha256(VENDOR / relative) != expected
        }
        self.assertEqual(mismatches, {})

    def test_direct_reuse_allowlist_points_to_real_vendor_files(self) -> None:
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        missing = [relative for relative in payload["direct_reuse"] if not (VENDOR / relative).is_file()]
        self.assertEqual(missing, [])
        self.assertIn("scripts/build_narration.mjs", payload["direct_reuse"])
        self.assertIn("scripts/render_streaming_ffmpeg.mjs", payload["direct_reuse"])
        self.assertIn("scripts/verify_final_video.sh", payload["direct_reuse"])


if __name__ == "__main__":
    unittest.main()
