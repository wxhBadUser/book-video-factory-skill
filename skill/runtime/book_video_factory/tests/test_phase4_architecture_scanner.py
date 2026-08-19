from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO = _repository_root()
SCANNER = REPO / "scripts/verify_phase4_audio_stage.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("phase4_scanner", SCANNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Phase 4 scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseFourArchitectureScannerTests(unittest.TestCase):
    def test_current_repository_has_no_critical_findings(self) -> None:
        scanner = load_scanner()
        report = scanner.scan_repository(REPO)
        critical = [item for item in report["findings"] if item["severity"] == "critical"]
        self.assertEqual(critical, [], critical)

    def test_missing_required_audio_runtime_file_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            findings = scanner.scan_required_files(Path(temporary))
        self.assertTrue(any(item["check_id"] == "phase4_required_file" for item in findings))

    def test_local_edge_tts_client_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/tts_client.py"
            path.parent.mkdir(parents=True)
            path.write_text("import edge_tts\nedge_tts.Communicate('text', 'voice')\n", encoding="utf-8")
            findings = scanner.scan_local_tts_implementations(root)
        self.assertTrue(any(item["check_id"] == "local_tts_implementation" for item in findings))

    def test_fake_audio_writer_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/fake_audio.py"
            path.parent.mkdir(parents=True)
            path.write_text("Path('narration.m4a').write_bytes(b'fake audio')\n", encoding="utf-8")
            findings = scanner.scan_fake_media_writers(root)
        self.assertTrue(any(item["check_id"] == "fake_audio_writer" for item in findings))

    def test_arbitrary_hbg_script_invocation_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/bad.py"
            path.parent.mkdir(parents=True)
            path.write_text("run_hbg_node(user_script, project, capability='phase4')\n", encoding="utf-8")
            findings = scanner.scan_hbg_capability_boundary(root)
        self.assertTrue(any(item["check_id"] == "arbitrary_hbg_invocation" for item in findings))

    def test_second_audio_state_authority_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/progress.py"
            path.parent.mkdir(parents=True)
            path.write_text("STATE_FILE = 'audio_progress.json'\n", encoding="utf-8")
            findings = scanner.scan_second_state_authority(root)
        self.assertTrue(any(item["check_id"] == "second_audio_state_authority" for item in findings))

    def test_estimated_timing_fallback_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/timing.py"
            path.parent.mkdir(parents=True)
            path.write_text("duration = characters / 232\ntiming_source = 'estimated'\n", encoding="utf-8")
            findings = scanner.scan_estimated_timing(root)
        self.assertTrue(any(item["check_id"] == "estimated_audio_timing" for item in findings))

    def test_duplicate_caption_or_vtt_engine_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/vtt_builder.py"
            path.parent.mkdir(parents=True)
            path.write_text("def build_vtt(cues): return 'WEBVTT'\n", encoding="utf-8")
            findings = scanner.scan_duplicate_media_engines(root)
        self.assertTrue(any(item["check_id"] == "duplicate_vtt_engine" for item in findings))

    def test_direct_image_or_render_invocation_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "book_video_factory/src/book_video_factory/audio_stage/image.py"
            render = root / "book_video_factory/src/book_video_factory/audio_stage/render.py"
            image.parent.mkdir(parents=True)
            image.write_text("image_gen.text2im()\n", encoding="utf-8")
            render.write_text("run_hbg_node('render_streaming_ffmpeg.mjs', project)\n", encoding="utf-8")
            findings = scanner.scan_later_stage_invocations(root)
        ids = {item["check_id"] for item in findings}
        self.assertIn("phase4_image_generation", ids)
        self.assertIn("phase4_final_render", ids)

    def test_embedded_secret_or_local_path_is_rejected(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "book_video_factory/src/book_video_factory/audio_stage/leak.py"
            path.parent.mkdir(parents=True)
            path.write_text("TOKEN='sk-abcdefghijklmnopqrstuvwxyz123456'\nROOT='/Users/alice/private/audio'\n", encoding="utf-8")
            findings = scanner.scan_secrets_and_paths(root)
        ids = {item["check_id"] for item in findings}
        self.assertIn("phase4_embedded_secret", ids)
        self.assertIn("phase4_local_path", ids)

    def test_phase_four_docs_cannot_claim_images_or_final_video_complete(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = root / "README.md"
            readme.write_text("Phase 4 已完成全量图片、HyperFrames 和最终 MP4。\n", encoding="utf-8")
            findings = scanner.scan_scope_claims(root, [readme])
        self.assertTrue(any(item["check_id"] == "phase4_scope_claim" for item in findings))

    def test_runtime_drift_is_inherited_from_phase_zero(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "book_video_factory/a.py"
            bundled = root / "skill/runtime/book_video_factory/a.py"
            main.parent.mkdir(parents=True)
            bundled.parent.mkdir(parents=True)
            main.write_text("x=1\n", encoding="utf-8")
            bundled.write_text("x=2\n", encoding="utf-8")
            findings = scanner.scan_runtime_divergence(root)
        self.assertTrue(any(item["check_id"] == "runtime_divergence" for item in findings))

    def test_report_is_json_serializable(self) -> None:
        scanner = load_scanner()
        payload = json.dumps(scanner.scan_repository(REPO), ensure_ascii=False)
        self.assertIn("phase4-audio-stage-report.v1", payload)


if __name__ == "__main__":
    unittest.main()
