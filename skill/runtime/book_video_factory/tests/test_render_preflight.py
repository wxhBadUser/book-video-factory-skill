from __future__ import annotations

import json
import copy
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_video_factory.render_stage.preflight import (
    RenderPreflightError,
    _scene_review_vision_blockers,
    preflight_render,
    verify_render_preflight,
)
from book_video_factory.render_stage.compiler import VisualSemanticAlignmentError
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.vision_review import (
    BaseVisionProvider,
    CurrentReviewResult,
    register_provider_key,
    review_current_shot,
)
import test_phase7_render_stage as phase7
from test_av_semantic_alignment_attacks import (
    _tmp_project,
    _write_current_contract_group_and_task,
    _write_tasks,
)


class _RenderCurrentVisionProvider(BaseVisionProvider):
    name = "render-current-vision-stub"
    signing_secret = "render-current-vision-test-key"

    def __init__(self, *, semantic: str = "pass", reality: str = "pass", identity: str = "pass") -> None:
        self.semantic = semantic
        self.reality = reality
        self.identity = identity

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str):
        raise AssertionError("current Render preflight must not invoke the v1 reviewer")

    def _review_current(
        self,
        *,
        image_bytes: bytes,
        caption_group_text: str,
        proposition_text: str,
        prompt_text: str,
        identity_reference_bytes: tuple[bytes, ...],
    ) -> CurrentReviewResult:
        return CurrentReviewResult(
            semantic_review_status=self.semantic,
            semantic_review_reasoning="画面覆盖当前图片组的全部字幕语义。",
            reality_review_status=self.reality,
            reality_review_reasoning="人物动作物件关系符合现实约束。",
            identity_review_status=self.identity,
            identity_review_reasoning="人物身份与有序参考锚点完全一致。",
            vision_call_id="render-current-call-001",
        )


register_provider_key(_RenderCurrentVisionProvider.name, _RenderCurrentVisionProvider.signing_secret)


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

    def _current_review_fixture(
        self,
        base: Path,
        *,
        semantic: str = "pass",
        reality: str = "pass",
        identity: str = "pass",
    ) -> tuple[Path, Path, dict, dict]:
        root = _tmp_project(base)
        _contract_path, task = _write_current_contract_group_and_task(root)
        task.update({
            "generation_lane": "host-imagegen",
            "generation_mode": "single",
            "narrative_function": "plot",
            "prompt_sha256": hashlib.sha256(task["prompt"].encode("utf-8")).hexdigest(),
            "output_target": "assets/generated/scenes/S1.png",
        })
        task["anchor_refs"] = ["C002", "C003"]
        task["identity_reference_task_ids"] = ["ANCHOR_C002", "ANCHOR_C003"]
        _write_tasks(root, [task])
        image = root / "06_visual_production/scenes/current.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"current-scene-pixels")
        anchors = []
        identity_evidence = []
        for character_id in task["anchor_refs"]:
            anchor = root / f"03_images_生成图片/anchors/{character_id}.png"
            anchor.parent.mkdir(parents=True, exist_ok=True)
            anchor.write_bytes(f"anchor-{character_id}".encode())
            anchors.append(anchor)
            identity_evidence.append({
                "task_id": f"ANCHOR_{character_id}",
                "path": anchor.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
                "role": "identity_reference",
            })
        proposition = VisualProposition.from_mapping(task["visual_proposition"])
        grouping = json.loads(
            (root / "04_audio/CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8")
        )
        group = next(
            value for value in grouping["groups"]
            if value["group_id"] == task["prompt_binding"]["group_id"]
        )
        bindings = json.loads(
            (root / "04_audio/CAPTION_BINDINGS.json").read_text(encoding="utf-8")
        )["captions"]
        evidence = review_current_shot(
            shot_id=task["shot_id"],
            image_path=image,
            caption_group=group,
            caption_texts={caption_id: bindings[caption_id]["text"] for caption_id in task["caption_ids"]},
            proposition=proposition,
            prompt_text=task["prompt"],
            visible_persistent_character_ids=tuple(task["anchor_refs"]),
            identity_reference_paths=tuple(anchors),
            generation_provider="host-imagegen",
            provider=_RenderCurrentVisionProvider(
                semantic=semantic, reality=reality, identity=identity
            ),
        )
        asset = {
            "task_id": task["task_id"],
            "scene_id": task["scene_id"],
            "path": image.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "provider": "host-imagegen",
            "identity_reference_evidence": identity_evidence,
        }
        decision = {
            "schema_version": "scene-review-decision.v1",
            "release_id": "r1",
            "decisions": [{
                "task_id": task["task_id"],
                "semantic_review_status": "pass",
                "reality_review_status": "pass",
                "identity_review_status": "pass",
                "note": "Current v2 pixels reviewed.",
                "vision_evidence": evidence.to_dict(),
            }],
        }
        decision_path = root / "06_visual_production/SCENE_REVIEW_DECISION.json"
        decision_path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
        return root, decision_path, task, asset

    def test_current_v2_review_binds_group_proposition_prompt_image_and_ordered_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root, decision_path, _task, asset = self._current_review_fixture(Path(raw))
            self.assertEqual(
                _scene_review_vision_blockers(
                    decision_path, {asset["task_id"]: asset}, root, require_current=True
                ),
                [],
            )

            attacks = ("group", "proposition", "prompt", "image", "anchor_order", "decision_status")
            for attack in attacks:
                with self.subTest(attack=attack), tempfile.TemporaryDirectory() as attack_raw:
                    attack_root, attack_decision, task, current_asset = self._current_review_fixture(Path(attack_raw))
                    if attack == "group":
                        task["prompt_binding"]["caption_group_sha256"] = "d" * 64
                        _write_tasks(attack_root, [task])
                    elif attack == "proposition":
                        task["visual_proposition"]["mood"] = "篡改后的情绪"
                        _write_tasks(attack_root, [task])
                    elif attack == "prompt":
                        task["prompt"] += " tampered"
                        _write_tasks(attack_root, [task])
                    elif attack == "image":
                        (attack_root / current_asset["path"]).write_bytes(b"swapped-scene-pixels")
                    else:
                        if attack == "anchor_order":
                            current_asset = copy.deepcopy(current_asset)
                            current_asset["identity_reference_evidence"].reverse()
                        else:
                            decision = json.loads(attack_decision.read_text(encoding="utf-8"))
                            decision["decisions"][0]["identity_review_status"] = "not_applicable"
                            attack_decision.write_text(
                                json.dumps(decision, ensure_ascii=False), encoding="utf-8"
                            )
                    if attack in {"proposition", "prompt"}:
                        with self.assertRaises(RenderPreflightError):
                            _scene_review_vision_blockers(
                                attack_decision,
                                {current_asset["task_id"]: current_asset},
                                attack_root,
                                require_current=True,
                            )
                    else:
                        self.assertEqual(
                            _scene_review_vision_blockers(
                                attack_decision,
                                {current_asset["task_id"]: current_asset},
                                attack_root,
                                require_current=True,
                            ),
                            [current_asset["task_id"]],
                        )

    def test_current_release_requires_all_three_v2_statuses_and_rejects_v1(self) -> None:
        for axis in ("semantic", "reality", "identity"):
            with self.subTest(axis=axis), tempfile.TemporaryDirectory() as raw:
                statuses = {"semantic": "pass", "reality": "pass", "identity": "pass"}
                statuses[axis] = "fail"
                root, decision_path, _task, asset = self._current_review_fixture(
                    Path(raw), **statuses
                )
                self.assertEqual(
                    _scene_review_vision_blockers(
                        decision_path, {asset["task_id"]: asset}, root, require_current=True
                    ),
                    [asset["task_id"]],
                )

        with tempfile.TemporaryDirectory() as raw:
            root, decision_path, _task, asset = self._current_review_fixture(Path(raw))
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            from book_video_factory.semantic_alignment.vision_review import (
                LocalVisionProvider,
                review_shot,
            )
            task = _task
            decision["decisions"][0]["vision_evidence"] = review_shot(
                shot_id=asset["task_id"],
                image_path=root / asset["path"],
                caption_text=task["caption_text"],
                prompt_text=task["prompt"],
                provider=LocalVisionProvider(),
            ).to_dict()
            decision_path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(
                _scene_review_vision_blockers(
                    decision_path, {asset["task_id"]: asset}, root, require_current=True
                ),
                [asset["task_id"]],
            )

    def test_full_preflight_requires_decisions_to_exactly_cover_current_tasks_and_assets(self) -> None:
        attacks = ("empty", "missing", "duplicate", "extra", "non_string")
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                project, input_path, tasks, scene_manifest, director = self.prepare(Path(temp))
                decision_path = project / "06_visual_production/SCENE_REVIEW_DECISION.json"
                decision = json.loads(decision_path.read_text(encoding="utf-8"))
                if attack == "empty":
                    decision["decisions"] = []
                elif attack == "missing":
                    decision["decisions"] = decision["decisions"][:-1]
                elif attack == "duplicate":
                    decision["decisions"][-1] = copy.deepcopy(decision["decisions"][0])
                elif attack == "extra":
                    extra = copy.deepcopy(decision["decisions"][0])
                    extra["task_id"] = "SCENE_EXTRA"
                    decision["decisions"].append(extra)
                else:
                    decision["decisions"][0]["task_id"] = 7
                decision_path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
                approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}

                with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                     mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                     mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                    result = preflight_render(
                        project,
                        input_path,
                        command_runner=_runner([]),
                        process_lister=lambda: [],
                    )

                self.assertEqual(result.next_stage_status, "blocked_by_visual_semantic_alignment")
                report = json.loads(result.report_path.read_text(encoding="utf-8"))
                self.assertTrue(report["vision_review_blockers"])

    def test_prepare_time_visual_semantic_error_is_persisted_by_formal_preflight_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            input_path = project / "07_render/RENDER_INPUT.json"
            input_path.parent.mkdir(parents=True)
            input_path.write_text("{}", encoding="utf-8")

            with mock.patch(
                "book_video_factory.render_stage.compiler.prepare_render_stage",
                side_effect=VisualSemanticAlignmentError(
                    "registered scene asset or identity anchor pixels changed"
                ),
            ):
                result = preflight_render(project, input_path)

            self.assertEqual(result.next_stage_status, "blocked_by_visual_semantic_alignment")
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["next_stage_status"], "blocked_by_visual_semantic_alignment")
            self.assertIn("identity anchor pixels changed", report["vision_review_blockers"][0])

    def test_directly_invokes_hbg_style_and_disk_preflight_with_bound_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            calls: list[list[str]] = []
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                result = preflight_render(project, input_path, command_runner=_runner(calls), process_lister=lambda: [])
            self.assertEqual(result.next_stage_status, "ready_for_hbg_render")
            self.assertEqual(len(calls), 2)
            self.assertTrue(any("validate_style_system.mjs" in item for item in calls[0]))
            self.assertTrue(any("preflight_long_render.sh" in item for item in calls[1]))
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["chosen_renderer"], "streaming_ffmpeg")
            self.assertGreater(report["free_disk_bytes"], report["required_disk_bytes"])
            self.assertEqual(len(report["render_job_id"]), 20)
            self.assertEqual(report["existing_work_dirs"], [])
            self.assertEqual(report["caption_visual_contract_blockers"], [])
            self.assertEqual(report["vision_review_blockers"], [])
            self.assertEqual(verify_render_preflight(project)["status"], "pass")

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
            self.assertEqual(result.next_stage_status, "blocked_by_render_preflight")
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
            self.assertEqual(result.next_stage_status, "blocked_by_render_preflight")
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["hbg_disk_preflight"]["exit_code"], 1)
            self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
