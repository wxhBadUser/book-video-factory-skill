from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_video_factory.render_stage.preflight import (
    RenderPreflightError,
    preflight_render,
    verify_render_preflight,
)
import test_phase7_render_stage as phase7


def _runner(calls: list[list[str]], *, disk_pass: bool = True):
    def run(command: list[str], cwd: Path, env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if any("validate_style_system.mjs" in item for item in command):
            return subprocess.CompletedProcess(command, 0, json.dumps({"status": "validated", "orientation": "landscape"}), "")
        required = next((item for item in reversed(command) if item.isdigit()), "1")
        code = 0 if disk_pass else 1
        stdout = f"project={cwd}\nfree_gib={100 if disk_pass else 0}\nrequired_gib={required}\n"
        if disk_pass:
            stdout += "preflight=pass\n"
        return subprocess.CompletedProcess(command, code, stdout, "" if disk_pass else "insufficient free space for long render")
    return run


class RenderPreflightTests(unittest.TestCase):
    def prepare(self, base: Path):
        return phase7.RenderStageTests().prepare_project(base)

    def test_directly_invokes_hbg_style_and_disk_preflight_with_bound_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            calls: list[list[str]] = []
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                result = preflight_render(project, input_path, command_runner=_runner(calls), process_lister=lambda: [])
            self.assertEqual(result.next_stage_status, "blocked_by_visual_semantic_alignment")
            self.assertEqual(len(calls), 2)
            self.assertTrue(any("validate_style_system.mjs" in item for item in calls[0]))
            self.assertTrue(any("preflight_long_render.sh" in item for item in calls[1]))
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["chosen_renderer"], "streaming_ffmpeg")
            self.assertGreater(report["free_disk_bytes"], report["required_disk_bytes"])
            self.assertEqual(len(report["render_job_id"]), 20)
            self.assertEqual(report["existing_work_dirs"], [])
            self.assertTrue(report["caption_visual_contract_blockers"])
            with self.assertRaisesRegex(RenderPreflightError, "blocked|pass"):
                verify_render_preflight(project)

    def test_blocks_active_and_current_stale_work_dirs_with_exact_safe_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                prepared = phase7.prepare_render_stage(project, input_path)
                renders = prepared.workspace / "renders"
                inactive = renders / "ffmpeg-work-book-v1"
                active = renders / "work-active-job"
                inactive.mkdir(parents=True)
                active.mkdir()
                (inactive / "segment.mp4").write_bytes(b"partial")
                (active / "state.json").write_text("{}", encoding="utf-8")
                result = preflight_render(
                    project,
                    input_path,
                    command_runner=_runner([]),
                    process_lister=lambda: [{"pid": 4242, "command_line": f"node render {active}"}],
                )
            self.assertEqual(result.next_stage_status, "blocked_by_visual_semantic_alignment")
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            by_name = {Path(item["path"]).name: item for item in report["existing_work_dirs"]}
            self.assertEqual(by_name["work-active-job"]["activity"], "active")
            self.assertIsNone(by_name["work-active-job"]["remove_command"])
            self.assertEqual(by_name["ffmpeg-work-book-v1"]["activity"], "inactive")
            command = by_name["ffmpeg-work-book-v1"]["remove_command"]
            self.assertIn("Remove-Item -LiteralPath", command)
            self.assertIn(str(inactive.resolve()), command)
            self.assertNotIn("renders\\*", command)
            with self.assertRaisesRegex(RenderPreflightError, "blocked|pass"):
                verify_render_preflight(project)
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                with self.assertRaisesRegex(phase7.RenderStageError, "preflight"):
                    phase7.execute_render_stage(project, input_path, render_runner=lambda *_: None, qa_runner=lambda *_: None)

    def test_insufficient_disk_is_recorded_as_blocked_not_promoted_to_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                result = preflight_render(project, input_path, command_runner=_runner([], disk_pass=False), process_lister=lambda: [])
            self.assertEqual(result.next_stage_status, "blocked_by_visual_semantic_alignment")
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["hbg_disk_preflight"]["exit_code"], 1)
            self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
