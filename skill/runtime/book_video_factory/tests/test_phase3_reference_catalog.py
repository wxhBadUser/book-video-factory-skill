from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.reference_visuals.catalog import (  # noqa: E402
    ReferenceCatalogError,
    load_reference_catalog,
    verify_reference_catalog,
)


class Phase3ReferenceCatalogTests(unittest.TestCase):
    def test_catalog_locks_exactly_six_gold_cases(self) -> None:
        catalog = load_reference_catalog()
        self.assertEqual(
            {item.work_title for item in catalog.entries},
            {"漂亮朋友", "窄门", "简爱", "小王子", "包法利夫人", "月亮与六便士"},
        )
        self.assertEqual(len(catalog.entries), 6)
        for item in catalog.entries:
            self.assertTrue(item.style_only)
            self.assertFalse(item.identity_reference_allowed)
            self.assertFalse(item.production_asset_allowed)
            self.assertFalse(item.exact_composition_copy_allowed)
            self.assertEqual(len(item.sha256), 64)
            self.assertGreater(item.shot_count, 150)
            self.assertGreater(item.width, 0)
            self.assertGreater(item.height, 0)

    def test_repository_catalog_verifies_files_hashes_and_dimensions(self) -> None:
        report = verify_reference_catalog()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["verified_entries"], 6)
        self.assertEqual(report["duplicate_hashes"], [])

    def test_tampered_contact_sheet_fails_hash_validation(self) -> None:
        catalog = load_reference_catalog()
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = Path(temp)
            source = catalog.entries[0].absolute_path
            copied = root / source.name
            shutil.copy2(source, copied)
            copied.write_bytes(copied.read_bytes() + b"tamper")
            payload = json.loads(catalog.path.read_text(encoding="utf-8"))
            payload["entries"][0]["path"] = copied.as_posix()
            path = root / "catalog.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "sha256"):
                load_reference_catalog(path, repository_root=ROOT.parent)

    def test_catalog_rejects_reference_path_escape(self) -> None:
        catalog = load_reference_catalog()
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = Path(temp)
            payload = json.loads(catalog.path.read_text(encoding="utf-8"))
            payload["entries"][0]["path"] = "../outside.jpg"
            path = root / "catalog.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "escapes"):
                load_reference_catalog(path, repository_root=ROOT.parent)


    def test_catalog_file_itself_must_be_inside_repository_root(self) -> None:
        catalog = load_reference_catalog()
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as root_dir:
            copied = Path(outside) / "catalog.json"
            shutil.copy2(catalog.path, copied)
            with self.assertRaisesRegex(ReferenceCatalogError, "catalog path|escapes"):
                load_reference_catalog(copied, repository_root=Path(root_dir))

    def test_profile_semantic_tamper_is_rejected(self) -> None:
        catalog = load_reference_catalog()
        entry = catalog.entries[0]
        profile_path = ROOT.parent / entry.profile_path
        original = profile_path.read_bytes()
        payload = json.loads(original.decode("utf-8"))
        payload["visual_language"] = "tampered generic style"
        try:
            profile_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "profile.*sha256|profile.*mismatch"):
                load_reference_catalog()
        finally:
            profile_path.write_bytes(original)
        self.assertEqual(profile_path.read_bytes(), original)

    def test_kernel_hash_or_contract_tamper_is_rejected(self) -> None:
        catalog = load_reference_catalog()
        kernel = ROOT / "config/visuals/literary-cinematic-realism-v1.json"
        original = kernel.read_bytes()
        payload = json.loads(original.decode("utf-8"))
        payload["machine_diagnostics_are_aesthetic_approval"] = True
        try:
            kernel.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "kernel.*hash|kernel.*contract"):
                load_reference_catalog()
        finally:
            kernel.write_bytes(original)
        self.assertEqual(kernel.read_bytes(), original)

    def test_catalog_rejects_unknown_top_level_fields(self) -> None:
        catalog = load_reference_catalog()
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = Path(temp)
            payload = json.loads(catalog.path.read_text(encoding="utf-8"))
            payload["untrusted_override"] = True
            path = root / "catalog.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "fields"):
                load_reference_catalog(path, repository_root=ROOT.parent)

    def test_catalog_rejects_reference_allowed_as_identity_or_production(self) -> None:
        catalog = load_reference_catalog()
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = Path(temp)
            payload = json.loads(catalog.path.read_text(encoding="utf-8"))
            payload["entries"][0]["roles"]["identity_reference_allowed"] = True
            path = root / "catalog.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ReferenceCatalogError, "style-only"):
                load_reference_catalog(path, repository_root=ROOT.parent)


if __name__ == "__main__":
    unittest.main()
