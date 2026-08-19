from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO = _repository_root()
SCANNER = REPO / "scripts/verify_phase0_architecture.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("phase0_scanner", SCANNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load phase0 scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseZeroArchitectureScannerTests(unittest.TestCase):
    def test_current_repository_has_no_critical_architecture_findings(self) -> None:
        scanner = load_scanner()
        report = scanner.scan_repository(REPO)
        critical = [item for item in report["findings"] if item["severity"] == "critical"]
        self.assertEqual(critical, [], critical)

    def test_active_text_scanner_detects_rejected_route_term(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "README.md"
            active.write_text("default uses multi-speaker VoxCPM2 production\n", encoding="utf-8")
            findings = scanner.scan_active_text_files(root, [active])
            self.assertTrue(any(item["check_id"] == "forbidden_active_route" for item in findings))

    def test_active_text_scanner_detects_removed_bilingual_filename(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "README.md"
            active.write_text("output *-v4-bilingual-3x4.mp4\n", encoding="utf-8")
            findings = scanner.scan_active_text_files(root, [active])
            self.assertTrue(any(item["check_id"] == "forbidden_active_route" for item in findings))

    def test_absolute_windows_path_is_rejected_in_active_code(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "script.py"
            active.write_text("MODEL = r'I:/图书号/models/example'\n", encoding="utf-8")
            findings = scanner.scan_absolute_paths(root, [active])
            self.assertTrue(any(item["check_id"] == "absolute_windows_path" for item in findings))


if __name__ == "__main__":
    unittest.main()
