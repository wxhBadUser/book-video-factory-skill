from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from PIL import Image

from book_video_factory.manifests import sha256_file
from book_video_factory.render_stage.encoded_visual_qa import (
    EncodedVisualQaError,
    build_encoded_frame_plan,
    review_encoded_master,
    verify_encoded_frame_plan,
)
import test_phase7_render_stage as phase7

from book_video_factory.semantic_alignment.vision_review import (
    LocalVisionProvider,
    MissingVisionEvidenceError,
    ParityResult,
    review_shot,
)


def _signed_encoded_evidence(verdict: str = "match") -> dict[str, Any]:
    """A real, cryptographically-signed vision-evidence record for an encoded frame.

    Minted through ``review_shot`` with the keyed local provider, so the encoded
    gate (which verifies the signature) accepts it. The old hand-authored dict
    naming claude-sonnet-4.5 would now be rejected by the gate.
    """

    import hashlib
    import tempfile
    from pathlib import Path

    class _KeyedStubProvider(LocalVisionProvider):
        name = "local-vision-stub"

        def __init__(self, v: str = "match") -> None:
            self.verdict = v

        def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
            call_id = hashlib.sha256(image_bytes + str(caption_text).encode("utf-8")).hexdigest()[:24]
            return ParityResult(verdict=self.verdict, reasoning="The encoded frame matches the reviewed scene.", call_id=call_id)

    with tempfile.TemporaryDirectory() as raw:
        img = Path(raw) / "frame.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake-pixels")
        evidence = review_shot(
            shot_id="encoded", image_path=img, caption_text=verdict, prompt_text=verdict,
            provider=_KeyedStubProvider(verdict),
        )
    return evidence.to_dict()


class EncodedVisualQaTests(unittest.TestCase):
    def test_frame_plan_covers_semantic_categories_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = phase7.RenderStageTests().prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                phase7.prepare_render_stage(project, input_path)
                phase7.preflight_render(project, input_path, command_runner=phase7.passing_preflight_runner, process_lister=lambda: [])
                first = build_encoded_frame_plan(project)
                second = build_encoded_frame_plan(project)
            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "unchanged")
            plan = json.loads(first.plan_path.read_text(encoding="utf-8"))
            categories = {category for sample in plan["samples"] for category in sample["categories"]}
            self.assertTrue({"opening", "chapter", "high_risk", "hero", "climax", "ending", "random"}.issubset(categories))
            self.assertGreaterEqual(plan["random_sample_count"], 1)
            ending = next(sample for sample in plan["samples"] if "ending" in sample["categories"])
            self.assertLessEqual(ending["seconds"], plan["total_duration"] - 0.5)
            self.assertEqual(verify_encoded_frame_plan(project), plan)

    def test_plan_tampering_is_rejected_before_hbg_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = phase7.RenderStageTests().prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                phase7.prepare_render_stage(project, input_path)
                phase7.preflight_render(project, input_path, command_runner=phase7.passing_preflight_runner, process_lister=lambda: [])
                result = build_encoded_frame_plan(project)
            plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
            plan["samples"][0]["seconds"] += 0.25
            phase7.write_json(result.plan_path, plan)
            with self.assertRaisesRegex(EncodedVisualQaError, "modified|stale|plan"):
                verify_encoded_frame_plan(project)

    def test_hbg_verifier_receives_plan_timestamps_and_rejection_blocks_final_qa(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = phase7.RenderStageTests().prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            observed_frames: list[tuple[float, str]] = []

            def render_runner(workspace: Path, output: Path, manifest: dict) -> None:
                output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"encoded-master")

            def qa_runner(video: Path, qa_dir: Path, frames: list[tuple[float, str]]) -> None:
                observed_frames.extend(frames)
                qa_dir.mkdir(parents=True, exist_ok=True)
                phase7.write_json(qa_dir / "ffprobe.json", {
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                    ], "format": {"duration": "8.0"},
                })
                (qa_dir / "blackdetect.txt").write_text("", encoding="utf-8")
                (qa_dir / "silencedetect.txt").write_text("", encoding="utf-8")
                (qa_dir / "ebur128.txt").write_text("Peak: -3.5 dBFS\n", encoding="utf-8")
                for index, (_seconds, label) in enumerate(frames, start=1):
                    Image.new("RGB", (1920, 1080), (index * 20, 30, 40)).save(qa_dir / f"{index:02d}-{label}.png")
                Image.new("RGB", (1200, 600), (40, 40, 40)).save(qa_dir / "contact-sheet.jpg")

            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                phase7.prepare_render_stage(project, input_path)
                phase7.preflight_render(project, input_path, command_runner=phase7.passing_preflight_runner, process_lister=lambda: [])
                built = build_encoded_frame_plan(project)
                rendered = phase7.execute_render_stage(project, input_path, render_runner=render_runner, qa_runner=qa_runner)
            plan = json.loads(built.plan_path.read_text(encoding="utf-8"))
            self.assertEqual(observed_frames, [(item["seconds"], item["sample_id"]) for item in plan["samples"]])
            decision_path = project / "09_qc/ENCODED_REVIEW_DECISION.json"
            decisions = [{
                "sample_id": item["sample_id"], "semantic_status": "pass",
                "visual_reality_status": "pass", "identity_status": "pass",
                "caption_status": "pass" if {"caption_bright", "caption_dark"} & set(item["categories"]) else "not_applicable",
                "note": "Reviewed.",
                "caption_note": "ASS caption box and subject clearance reviewed." if {"caption_bright", "caption_dark"} & set(item["categories"]) else "",
                # Every encoded sample must carry authoritative, signed vision evidence.
                "vision_evidence": _signed_encoded_evidence("match"),
            } for item in plan["samples"]]
            decisions[0]["semantic_status"] = "fail"; decisions[0]["note"] = "Opening title does not match the approved story."
            phase7.write_json(decision_path, {
                "schema_version": "encoded-visual-review-decision.v1", "release_id": "r1",
                "video_sha256": sha256_file(rendered.output_path), "frame_plan_sha256": sha256_file(built.plan_path),
                "reviewer": "Human Reviewer", "decisions": decisions,
            })
            reviewed = review_encoded_master(project, decision_path)
            self.assertEqual(reviewed.next_stage_status, "blocked_by_encoded_visual_qa")
            final = json.loads((project / "08_render_合成/final/FINAL_RENDER_MANIFEST.json").read_text(encoding="utf-8"))
            qa = json.loads((project / "09_qc/FINAL_QA_REPORT.json").read_text(encoding="utf-8"))
            self.assertEqual(final["next_stage_status"], "blocked_by_encoded_visual_qa")
            self.assertEqual(qa["status"], "awaiting_encoded_qa")


if __name__ == "__main__":
    unittest.main()
