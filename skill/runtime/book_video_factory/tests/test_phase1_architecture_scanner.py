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
SCANNER = REPO / "scripts/verify_phase1_content_brain.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("phase1_scanner", SCANNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load phase1 scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseOneArchitectureScannerTests(unittest.TestCase):
    def test_current_repository_has_no_critical_findings(self) -> None:
        scanner = load_scanner()
        report = scanner.scan_repository(REPO)
        critical = [item for item in report["findings"] if item["severity"] == "critical"]
        self.assertEqual(critical, [], critical)

    def test_placeholder_producing_research_fallback_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/book_research.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                'while len(candidates) < 20:\n    candidates.append({"summary": f"derived candidate {len(candidates)}"})\n',
                encoding="utf-8",
            )
            findings = scanner.scan_placeholder_fallbacks(root)
            self.assertTrue(any(item["check_id"] == "placeholder_fallback" for item in findings))

    def test_automatic_route_scoring_fallback_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/narrative_routing.py"
            path.parent.mkdir(parents=True)
            path.write_text('if not route.scores:\n    route.scores = {"originality": 3}\n', encoding="utf-8")
            findings = scanner.scan_placeholder_fallbacks(root)
            self.assertTrue(any(item["check_id"] == "automatic_editorial_fallback" for item in findings))

    def test_formal_pipeline_without_level_a_guard_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/research_validation.py"
            path.parent.mkdir(parents=True)
            path.write_text("def validate_formal_research(research):\n    return {'status': 'pass'}\n", encoding="utf-8")
            findings = scanner.scan_formal_source_guard(root)
            self.assertTrue(any(item["check_id"] == "formal_source_guard" for item in findings))

    def test_second_progress_authority_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/phase1_progress.py"
            path.parent.mkdir(parents=True)
            path.write_text('PROGRESS_FILE = "phase1-progress.json"\n', encoding="utf-8")
            findings = scanner.scan_second_state_authority(root)
            self.assertTrue(any(item["check_id"] == "second_state_authority" for item in findings))

    def test_copied_media_implementation_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/content_package.py"
            path.parent.mkdir(parents=True)
            path.write_text('subprocess.run(["ffmpeg", "-i", "audio.wav"])\n', encoding="utf-8")
            findings = scanner.scan_phase1_media_implementation(root)
            self.assertTrue(any(item["check_id"] == "phase1_media_implementation" for item in findings))

    def test_documentation_cannot_claim_phase_one_media_completion(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = root / "README.md"
            readme.write_text("Phase 1 已完成音频、图片和视频生成。\n", encoding="utf-8")
            findings = scanner.scan_phase1_scope_claims(root, [readme])
            self.assertTrue(any(item["check_id"] == "phase1_scope_claim" for item in findings))

    def test_vendor_hash_drift_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "vendor/hbg-life-simulation"
            shutil.copytree(REPO / "vendor/hbg-life-simulation", destination)
            target = destination / "scripts/build_narration.mjs"
            target.write_text(target.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
            findings = scanner.scan_vendor_integrity(root)
            self.assertTrue(any(item["check_id"] == "vendor_hash" for item in findings))

    def test_report_is_json_serializable(self) -> None:
        scanner = load_scanner()
        payload = json.dumps(scanner.scan_repository(REPO), ensure_ascii=False)
        self.assertIn("phase1-content-brain-report.v1", payload)


if __name__ == "__main__":
    unittest.main()
