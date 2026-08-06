from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "scripts/verify_phase3_visual_stage.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("phase3_scanner", SCANNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Phase 3 scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseThreeArchitectureScannerTests(unittest.TestCase):
    def test_current_repository_has_no_critical_findings(self) -> None:
        scanner = load_scanner()
        report = scanner.scan_repository(REPO)
        critical = [item for item in report["findings"] if item["severity"] == "critical"]
        self.assertEqual(critical, [], critical)

    def test_missing_required_visual_runtime_file_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            findings = scanner.scan_required_files(Path(temporary))
        self.assertTrue(any(item["check_id"] == "phase3_required_file" for item in findings))

    def test_private_image_api_client_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/visual_stage/image_client.py"
            path.parent.mkdir(parents=True)
            path.write_text("import requests\nrequests.post('https://api.openai.com/v1/images/generations')\n", encoding="utf-8")
            findings = scanner.scan_forbidden_image_clients(root)
        self.assertTrue(any(item["check_id"] == "forbidden_image_api_client" for item in findings))

    def test_copied_hbg_contact_sheet_or_renderer_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/visual_stage/contact_sheet.py"
            path.parent.mkdir(parents=True)
            path.write_text("def make_contact_sheet(images): return images\n", encoding="utf-8")
            findings = scanner.scan_copied_hbg_media(root)
        self.assertTrue(any(item["check_id"] == "copied_hbg_media" for item in findings))

    def test_parallel_visual_progress_authority_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/visual_stage/progress.py"
            path.parent.mkdir(parents=True)
            path.write_text('STATE = "visual_progress.json"\n', encoding="utf-8")
            findings = scanner.scan_second_state_authority(root)
        self.assertTrue(any(item["check_id"] == "second_state_authority" for item in findings))

    def test_fake_approval_writer_outside_approval_module_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/visual_stage/fake.py"
            path.parent.mkdir(parents=True)
            path.write_text('Path("ANCHOR_APPROVAL.json").write_text("{}")\n', encoding="utf-8")
            findings = scanner.scan_fake_approval_writers(root)
        self.assertTrue(any(item["check_id"] == "fake_visual_approval" for item in findings))

    def test_global_only_visual_envelope_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/visual_stage/compiler.py"
            path.parent.mkdir(parents=True)
            path.write_text("from book_video_factory.image_qc import REFERENCE_ENVELOPE\nPROFILE = REFERENCE_ENVELOPE\n", encoding="utf-8")
            findings = scanner.scan_global_only_envelope(root)
        self.assertTrue(any(item["check_id"] == "global_only_visual_envelope" for item in findings))

    def test_generated_media_committed_as_test_fixture_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/tests/fixtures/fake-output.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not a source fixture")
            findings = scanner.scan_committed_generated_media(root)
        self.assertTrue(any(item["check_id"] == "committed_generated_media" for item in findings))

    def test_phase_three_docs_cannot_claim_audio_or_video_completion(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = root / "README.md"
            readme.write_text("Phase 3 已完成 Edge TTS、VTT 和最终 MP4。\n", encoding="utf-8")
            findings = scanner.scan_scope_claims(root, [readme])
        self.assertTrue(any(item["check_id"] == "phase3_scope_claim" for item in findings))

    def test_report_is_json_serializable(self) -> None:
        scanner = load_scanner()
        payload = json.dumps(scanner.scan_repository(REPO), ensure_ascii=False)
        self.assertIn("phase3-visual-stage-report.v1", payload)


if __name__ == "__main__":
    unittest.main()
