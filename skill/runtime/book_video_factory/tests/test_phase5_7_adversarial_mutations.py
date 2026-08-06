from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from book_video_factory.delivery_stage import (
    FinalMasterApprovalError,
    approve_final_master,
    verify_final_master_approval,
)
from book_video_factory.manifests import record_approval, sha256_file
from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.render_stage.compiler import RenderStageError, prepare_render_stage
from book_video_factory.render_stage.qa import FinalVideoQaError, evaluate_final_video
from test_phase7_render_stage import RenderStageTests, write_json

ROOT = Path(__file__).resolve().parents[2]


def load_scanner(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PHASE5 = load_scanner("verify_phase5_director_stage.py")
PHASE6 = load_scanner("verify_phase6_scene_visuals.py")
PHASE7 = load_scanner("verify_phase7_render_stage.py")
VFINAL = load_scanner("verify_vfinal_architecture.py")


class PhaseFiveToSevenAdversarialTests(unittest.TestCase):
    def test_architecture_mutations_are_blocked(self) -> None:
        cases = [
            ("private_image_client", 'import requests\nrequests.post("https://x")\n'),
            ("duplicate_media_engine", 'import subprocess\nsubprocess.run(["ffmpeg","-i","x"])\n'),
            ("duplicate_tts_engine", 'import edge_tts\nedge_tts.Communicate("x","y")\n'),
            ("fake_media_path", 'Path("x.mp4").write_bytes(b"fake")\n'),
            ("second_state_authority", 'STATE="pipeline_progress.json"\n'),
            ("embedded_secret", 'TOKEN="sk-123456789012345678901234567890"\n'),
            ("local_path", 'ROOT="/Users/private/project"\n'),
        ]
        for expected, body in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "book_video_factory/src/book_video_factory/render_stage/bad.py"
                target.parent.mkdir(parents=True)
                target.write_text(body, encoding="utf-8")
                ids = {item["check_id"] for item in VFINAL.scan_repository(root)["findings"]}
                self.assertIn(expected, ids)

    def test_phase_specific_missing_contracts_are_blocked(self) -> None:
        scanners = [
            (PHASE5, "phase5_required_file"),
            (PHASE6, "phase6_required_file"),
            (PHASE7, "phase7_required_file"),
        ]
        for scanner, expected in scanners:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                ids = {item["check_id"] for item in scanner.scan_repository(Path(temp))["findings"]}
                self.assertIn(expected, ids)

    def _qa_fixture(self, base: Path) -> tuple[Path, Path, dict]:
        video = base / "video.mp4"; video.write_bytes(b"encoded")
        qa = base / "qa"; qa.mkdir()
        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
            ],
            "format": {"duration": "8.0"},
        }
        write_json(qa / "ffprobe.json", probe)
        (qa / "blackdetect.txt").write_text("", encoding="utf-8")
        (qa / "silencedetect.txt").write_text("", encoding="utf-8")
        (qa / "ebur128.txt").write_text("Peak: -3.5 dBFS\n", encoding="utf-8")
        Image.new("RGB", (1200, 600)).save(qa / "contact-sheet.jpg")
        return video, qa, probe

    def test_final_video_qa_mutations_are_blocked(self) -> None:
        mutations = {
            "wrong_codec": lambda p, q, x: x["streams"][0].update(codec_name="vp9"),
            "wrong_width": lambda p, q, x: x["streams"][0].update(width=1280),
            "wrong_height": lambda p, q, x: x["streams"][0].update(height=720),
            "wrong_pixel_format": lambda p, q, x: x["streams"][0].update(pix_fmt="yuv444p"),
            "wrong_fps": lambda p, q, x: x["streams"][0].update(r_frame_rate="24/1"),
            "wrong_audio_codec": lambda p, q, x: x["streams"][1].update(codec_name="mp3"),
            "wrong_sample_rate": lambda p, q, x: x["streams"][1].update(sample_rate="44100"),
            "wrong_duration": lambda p, q, x: x["format"].update(duration="20.0"),
            "missing_audio": lambda p, q, x: x.update(streams=x["streams"][:1]),
            "extra_video": lambda p, q, x: x["streams"].append(dict(x["streams"][0])),
            "black_interval": lambda p, q, x: (q / "blackdetect.txt").write_text("black_start:1", encoding="utf-8"),
            "long_silence": lambda p, q, x: (q / "silencedetect.txt").write_text("silence_start:1", encoding="utf-8"),
            "hot_peak": lambda p, q, x: (q / "ebur128.txt").write_text("Peak: -1.0 dBFS", encoding="utf-8"),
            "missing_contact": lambda p, q, x: (q / "contact-sheet.jpg").unlink(),
            "empty_video": lambda p, q, x: p.write_bytes(b""),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                video, qa, probe = self._qa_fixture(Path(temp))
                mutation(video, qa, probe)
                write_json(qa / "ffprobe.json", probe)
                with self.assertRaises(FinalVideoQaError):
                    evaluate_final_video(video, qa, expected_duration=8.0)

    def _approved_master(self, base: Path) -> Path:
        project = base / "project"; project.mkdir()
        video = project / "08_render_合成/final/book.mp4"; video.parent.mkdir(parents=True); video.write_bytes(b"video")
        plan = project / "09_qc/ENCODED_FRAME_PLAN.json"; write_json(plan, {"fixture": True})
        decision = project / "09_qc/ENCODED_REVIEW_DECISION.json"; write_json(decision, {"fixture": True})
        hbg_contact = project / "09_qc/final-video/contact-sheet.jpg"; hbg_contact.parent.mkdir(parents=True); hbg_contact.write_bytes(b"hbg-contact")
        event = record_approval(
            project, release_id="r1", gate="encoded_visual_qa", decision="approved", reviewer="Human",
            subjects=[plan, decision, video, hbg_contact], note="Encoded visual review fixture.",
        )
        encoded = project / "09_qc/ENCODED_VISUAL_QA.json"
        write_json(encoded, {
            "schema_version": "encoded-visual-qa.v1", "status": "pass", "human_review_passed": True,
            "reviewer": "Human", "video_path": video.relative_to(project).as_posix(), "video_sha256": sha256_file(video),
            "frame_plan_path": plan.relative_to(project).as_posix(), "frame_plan_sha256": sha256_file(plan),
            "decision_path": decision.relative_to(project).as_posix(), "decision_sha256": sha256_file(decision),
            "hbg_contact_sheet_sha256": sha256_file(hbg_contact), "frames": [],
            "approval_event_path": event.relative_to(project).as_posix(), "approval_event_sha256": sha256_file(event),
        })
        contact = project / "09_qc/FINAL_CONTACT_SHEET.jpg"; contact.write_bytes(b"contact-sheet")
        caption = project / "09_qc/CAPTION_PARITY_REPORT.json"; write_json(caption, {"schema_version": "caption-parity-report.v1", "status": "pass"})
        qa = project / "09_qc/FINAL_QA_REPORT.json"; write_json(qa, {
            "schema_version": "final-video-qa-report.v1", "status": "pass",
            "encoded_visual_qa_sha256": sha256_file(encoded), "final_contact_sheet_sha256": sha256_file(contact),
            "caption_parity_sha256": sha256_file(caption),
        })
        render = project / "07_render/RENDER_MANIFEST.json"; write_json(render, {"schema_version": "render-manifest.v1"})
        write_json(project / "08_render_合成/final/FINAL_RENDER_MANIFEST.json", {
            "schema_version": "final-render-manifest.v1", "release_id": "r1",
            "render_manifest_sha256": sha256_file(render),
            "video_path": video.relative_to(project).as_posix(), "video_sha256": sha256_file(video), "video_bytes": video.stat().st_size,
            "qa_report_path": qa.relative_to(project).as_posix(), "qa_report_sha256": sha256_file(qa),
            "encoded_visual_qa_path": encoded.relative_to(project).as_posix(), "encoded_visual_qa_sha256": sha256_file(encoded),
            "final_contact_sheet_path": contact.relative_to(project).as_posix(), "final_contact_sheet_sha256": sha256_file(contact),
            "caption_parity_path": caption.relative_to(project).as_posix(), "caption_parity_sha256": sha256_file(caption),
            "renderer": "streaming_ffmpeg", "next_stage_status": "awaiting_final_master_approval",
        })
        approve_final_master(project, reviewer="Human", note="Reviewed encoded master.")
        return project

    def test_final_master_approval_mutations_are_blocked(self) -> None:
        mutations = {
            "video_tamper": lambda root, app: (root / "08_render_合成/final/book.mp4").write_bytes(b"changed"),
            "qa_tamper": lambda root, app: (root / "09_qc/FINAL_QA_REPORT.json").write_text("{}", encoding="utf-8"),
            "render_manifest_tamper": lambda root, app: (root / "07_render/RENDER_MANIFEST.json").write_text("{}", encoding="utf-8"),
            "encoded_visual_qa_tamper": lambda root, app: (root / "09_qc/ENCODED_VISUAL_QA.json").write_text("{}", encoding="utf-8"),
            "final_contact_sheet_tamper": lambda root, app: (root / "09_qc/FINAL_CONTACT_SHEET.jpg").write_bytes(b"changed"),
            "caption_parity_tamper": lambda root, app: (root / "09_qc/CAPTION_PARITY_REPORT.json").write_text("{}", encoding="utf-8"),
            "approval_subject_tamper": lambda root, app: app["subjects"].clear(),
            "approval_release_tamper": lambda root, app: app.update(release_id="r2"),
            "approval_status_tamper": lambda root, app: app.update(next_stage_status="complete-ish"),
            "approval_reviewer_tamper": lambda root, app: app.update(reviewer="Other"),
            "approval_event_hash_tamper": lambda root, app: app.update(approval_event_sha256="0" * 64),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = self._approved_master(Path(temp))
                approval_path = root / "10_delivery_交付/FINAL_MASTER_APPROVAL.json"
                approval = json.loads(approval_path.read_text(encoding="utf-8"))
                mutation(root, approval)
                approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                with self.assertRaises(FinalMasterApprovalError):
                    verify_final_master_approval(root)

    def test_render_input_mutations_fail_closed(self) -> None:
        factory = RenderStageTests()
        mutation_cases = {
            "unknown_renderer": lambda x: x.update(renderer="custom-ffmpeg"),
            "unsafe_output": lambda x: x.update(output_name="../book.mp4"),
            "unversioned_hyperframes": lambda x: x.update(hyperframes_version="latest"),
            "zero_disk": lambda x: x.update(minimum_free_gib=0),
            "unknown_final_task": lambda x: x["opening"].update(final_image_task_id="UNKNOWN"),
            "empty_flash_tasks": lambda x: x["opening"].update(flash_task_ids=[]),
            "wrong_release": lambda x: x.update(release_id="r2"),
            "unknown_quality": lambda x: x.update(quality="ultra"),
        }
        for name, mutation in mutation_cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                project, input_path, tasks, scene_manifest, director = factory.prepare_project(Path(temp))
                value = json.loads(input_path.read_text(encoding="utf-8")); mutation(value); write_json(input_path, value)
                approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
                with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                     mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                     mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                    with self.assertRaises(RenderStageError):
                        prepare_render_stage(project, input_path)

    def test_new_project_status_fails_closed_without_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status = pipeline_status(root)
            self.assertEqual(status["status"], "awaiting_content_package")
            self.assertNotEqual(status["status"], "complete")


if __name__ == "__main__":
    unittest.main()
