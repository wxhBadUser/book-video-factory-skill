from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO = _repository_root()
SCANNER = REPO / "scripts/verify_phase2_hbg_bridge.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("phase2_scanner", SCANNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load phase2 scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseTwoArchitectureScannerTests(unittest.TestCase):
    def test_current_repository_has_no_critical_findings(self) -> None:
        scanner = load_scanner()
        report = scanner.scan_repository(REPO)
        critical = [item for item in report["findings"] if item["severity"] == "critical"]
        self.assertEqual(critical, [], critical)

    def test_missing_required_bridge_module_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            findings = scanner.scan_required_files(root)
            self.assertTrue(any(item["check_id"] == "phase2_required_file" for item in findings))

    def test_parallel_progress_authority_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/progress.py"
            path.parent.mkdir(parents=True)
            path.write_text('STATE = "progress.json"\n', encoding="utf-8")
            findings = scanner.scan_second_state_authority(root)
            self.assertTrue(any(item["check_id"] == "second_state_authority" for item in findings))

    def test_copied_hbg_narration_implementation_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/build_narration.py"
            path.parent.mkdir(parents=True)
            path.write_text('subprocess.run(["edge-tts", "--write-media", "out.mp3"])\n', encoding="utf-8")
            findings = scanner.scan_media_or_copied_hbg(root)
            self.assertTrue(any(item["check_id"] == "phase2_media_implementation" for item in findings))

    def test_runner_may_only_call_approved_hbg_scripts(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/runner.py"
            path.parent.mkdir(parents=True)
            path.write_text('run_hbg_node("build_narration.mjs", project)\n', encoding="utf-8")
            findings = scanner.scan_runner_allowlist(root)
            self.assertTrue(any(item["check_id"] == "hbg_runner_allowlist" for item in findings))

    def test_missing_approval_guard_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/provenance.py"
            path.parent.mkdir(parents=True)
            path.write_text("def verify_phase1_handoff(project, release_id):\n    return True\n", encoding="utf-8")
            findings = scanner.scan_approval_guard(root)
            self.assertTrue(any(item["check_id"] == "approval_guard" for item in findings))

    def test_absolute_windows_path_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/project_spec.py"
            path.parent.mkdir(parents=True)
            path.write_text('MODEL = "I:\\\\图书号\\\\model"\n', encoding="utf-8")
            findings = scanner.scan_absolute_paths(root)
            self.assertTrue(any(item["check_id"] == "absolute_path" for item in findings))

    def test_unsafe_direct_overwrite_without_transaction_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/hbg_bridge/compiler.py"
            path.parent.mkdir(parents=True)
            path.write_text('target.write_text(content, encoding="utf-8")\n', encoding="utf-8")
            findings = scanner.scan_atomic_publication(root)
            self.assertTrue(any(item["check_id"] == "atomic_publication" for item in findings))

    def test_phase_two_documentation_cannot_claim_media_completion(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = root / "README.md"
            readme.write_text("Phase 2 已生成完整 Edge TTS、图片和最终视频。\n", encoding="utf-8")
            findings = scanner.scan_scope_claims(root, [readme])
            self.assertTrue(any(item["check_id"] == "phase2_scope_claim" for item in findings))

    def test_vendor_hash_drift_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "vendor/hbg-life-simulation"
            shutil.copytree(REPO / "vendor/hbg-life-simulation", destination)
            target = destination / "scripts/build_storyboard_base.mjs"
            target.write_text(target.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
            findings = scanner.scan_vendor_integrity(root)
            self.assertTrue(any(item["check_id"] == "vendor_hash" for item in findings))

    def test_report_is_json_serializable(self) -> None:
        scanner = load_scanner()
        payload = json.dumps(scanner.scan_repository(REPO), ensure_ascii=False)
        self.assertIn("phase2-hbg-bridge-report.v1", payload)


if __name__ == "__main__":
    unittest.main()
