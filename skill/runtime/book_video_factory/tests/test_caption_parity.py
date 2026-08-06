from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_video_factory.render_stage.encoded_visual_qa import (
    build_encoded_frame_plan,
    evaluate_caption_parity_contract,
)
import test_phase7_render_stage as phase7


class CaptionParityTests(unittest.TestCase):
    def prepare(self, base: Path, *, long_caption: bool = False):
        project, input_path, tasks, scene_manifest, director = phase7.RenderStageTests().prepare_project(base)
        if long_caption:
            audio_path = project / "audio_meta.json"
            audio = json.loads(audio_path.read_text(encoding="utf-8"))
            audio["captions"][0]["text"] = "这是一条故意超出最终字幕最大宽度而HTML预览仍可能看起来正常的极长字幕攻击文本"
            phase7.write_json(audio_path, audio)
        approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
        with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
             mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
             mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
            phase7.prepare_render_stage(project, input_path)
            phase7.preflight_render(project, input_path, command_runner=phase7.passing_preflight_runner, process_lister=lambda: [])
            plan_path = build_encoded_frame_plan(project).plan_path
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        decisions = [{
            "sample_id": item["sample_id"], "semantic_status": "pass", "visual_reality_status": "pass",
            "identity_status": "pass", "caption_status": "pass" if {"caption_bright", "caption_dark"} & set(item["categories"]) else "not_applicable",
            "note": "Reviewed.", "caption_note": "ASS box, text, safe area, and subject clearance are visible." if {"caption_bright", "caption_dark"} & set(item["categories"]) else "",
        } for item in plan["samples"]]
        frames = [{"sample_id": item["sample_id"], "path": f"frame-{index}.png", "sha256": f"{index:064x}", "categories": item["categories"]} for index, item in enumerate(plan["samples"], start=1)]
        video = project / "video.mp4"; video.write_bytes(b"video")
        return project, plan, decisions, frames, video

    def test_checks_html_and_ass_contract_plus_bright_dark_encoded_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan, decisions, frames, video = self.prepare(Path(temp))
            report = evaluate_caption_parity_contract(project, plan=plan, decisions=decisions, frames=frames, video=video)
            self.assertEqual(report["status"], "pass")
            checks = {item["id"]: item["result"] for item in report["checks"]}
            for identifier in (
                "font_family_parity", "font_size_parity", "background_box_parity", "padding_outline_parity",
                "bottom_safe_area", "maximum_width", "maximum_two_lines", "bright_dark_encoded_frames",
                "encoded_caption_human_review", "subject_not_occluded",
            ):
                self.assertEqual(checks[identifier], "pass")
            caption_samples = [item for item in plan["samples"] if {"caption_bright", "caption_dark"} & set(item["categories"])]
            self.assertGreaterEqual(len(caption_samples), 2)

    def test_rejects_html_passes_but_ass_text_can_overflow_attack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, plan, decisions, frames, video = self.prepare(Path(temp), long_caption=True)
            report = evaluate_caption_parity_contract(project, plan=plan, decisions=decisions, frames=frames, video=video)
            self.assertEqual(report["status"], "fail")
            checks = {item["id"]: item for item in report["checks"]}
            self.assertEqual(checks["maximum_width"]["result"], "fail")
            self.assertGreater(checks["maximum_width"]["observed"]["estimated_max_text_width"], checks["maximum_width"]["observed"]["allowed_width"])


if __name__ == "__main__":
    unittest.main()
