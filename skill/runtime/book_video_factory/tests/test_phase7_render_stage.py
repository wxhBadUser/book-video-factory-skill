from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from PIL import Image

from book_video_factory.manifests import sha256_file
from book_video_factory.semantic_alignment.caption_contract import build_caption_visual_contract_from_project
from book_video_factory.semantic_alignment.caption_grouping import build_caption_grouping_from_project
from book_video_factory.semantic_alignment.contract_bindings import aggregate_contract_bindings_sha256
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import compute_prompt_binding
from book_video_factory.semantic_alignment.vision_review import (
    BaseVisionProvider,
    CurrentReviewResult,
    register_provider_key,
    review_current_shot,
)
from book_video_factory.render_stage.compiler import (
    _default_qa_runner,
    _hbg_bash_command,
    execute_render_stage,
    generate_opening_preview,
    prepare_render_stage,
)
from book_video_factory.delivery_stage import FinalMasterApprovalError, approve_final_master, verify_final_master_approval
from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.render_stage.compiler import RenderStageError
from book_video_factory.render_stage.mix_calibration import approve_opening_mix, calibrate_opening_mix
from book_video_factory.render_stage.preflight import preflight_render
from book_video_factory.render_stage.encoded_visual_qa import build_encoded_frame_plan, review_encoded_master
from book_video_factory.render_stage.qa import FinalVideoQaError, evaluate_final_video


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class _Phase7CurrentVisionProvider(BaseVisionProvider):
    name = "phase7-current-vision-stub"
    signing_secret = "phase7-current-vision-test-key"

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str):
        raise AssertionError("Phase7 current fixture must not invoke the v1 reviewer")

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
            semantic_review_status="pass",
            semantic_review_reasoning="画面覆盖当前图片组的全部字幕语义。",
            reality_review_status="pass",
            reality_review_reasoning="画面动作、物件和空间关系符合现实。",
            identity_review_status="not_applicable",
            identity_review_reasoning="当前图片组没有可见持续人物，因此身份审查不适用。",
            vision_call_id="phase7-current-call-001",
        )


register_provider_key(_Phase7CurrentVisionProvider.name, _Phase7CurrentVisionProvider.signing_secret)


def _signed_scene_evidence(project: Path, asset_rel: str, task_id: str) -> dict[str, Any]:
    from book_video_factory.production_visuals.registry import _tasks

    task = _tasks(project).get(task_id)
    if not isinstance(task, dict):
        raise AssertionError(f"fixture task queue missing current task {task_id}")
    proposition = VisualProposition.from_mapping(task["visual_proposition"])
    evidence = review_current_shot(
        shot_id=str(task["shot_id"]),
        image_path=project / asset_rel,
        caption_group_text=str(task["caption_text"]),
        caption_group_sha256=str(task["prompt_binding"]["caption_group_sha256"]),
        proposition_text=json.dumps(task["visual_proposition"], ensure_ascii=False, sort_keys=True),
        proposition_sha256=proposition.content_sha256(),
        prompt_text=str(task["prompt"]),
        prompt_sha256=hashlib.sha256(str(task["prompt"]).encode("utf-8")).hexdigest(),
        visible_persistent_character_ids=(),
        identity_reference_paths=(),
        generation_provider="host-imagegen",
        provider=_Phase7CurrentVisionProvider(),
    )
    return evidence.to_dict()


def _signed_encoded_evidence(sample_id: str) -> dict[str, Any]:
    """Mint real, signed vision evidence for an encoded frame (no on-disk frame)."""

    import hashlib
    import tempfile
    from book_video_factory.semantic_alignment.vision_review import (
        LocalVisionProvider, ParityResult, review_shot,
    )

    class _KeyedStubProvider(LocalVisionProvider):
        name = "local-vision-stub"

        def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
            call_id = hashlib.sha256(image_bytes + str(caption_text).encode("utf-8")).hexdigest()[:24]
            return ParityResult(verdict="match", reasoning="The encoded frame matches the reviewed scene.", call_id=call_id)

    with tempfile.TemporaryDirectory() as raw:
        img = Path(raw) / "frame.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake-pixels")
        evidence = review_shot(
            shot_id=sample_id, image_path=img, caption_text=sample_id, prompt_text=sample_id,
            provider=_KeyedStubProvider(),
        )
    return evidence.to_dict()


def passing_preflight_runner(command: list[str], cwd: Path, env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
    if any("validate_style_system.mjs" in item for item in command):
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": "validated"}), "")
    return subprocess.CompletedProcess(command, 0, "free_gib=100\nrequired_gib=1\npreflight=pass\n", "")


def render_pipeline_status(project: Path) -> dict[str, Any]:
    """Exercise project Render routing independently of the intentional WIP mirror diff."""

    with mock.patch("book_video_factory.pipeline_runtime.repository_integrity_block", return_value=None):
        return pipeline_status(project)


class RenderStageTests(unittest.TestCase):
    def test_render_workspace_stages_on_project_volume_for_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch(
                "book_video_factory.render_stage.compiler.compile_director_stage",
                return_value=director,
            ), mock.patch(
                "book_video_factory.render_stage.compiler._tasks",
                return_value=tasks,
            ), mock.patch(
                "book_video_factory.render_stage.compiler._scene_approval",
                return_value=(approval, scene_manifest),
            ), mock.patch(
                "book_video_factory.render_stage.compiler.tempfile.TemporaryDirectory",
                wraps=tempfile.TemporaryDirectory,
            ) as temporary:
                prepare_render_stage(project, input_path)

            calls = [
                call for call in temporary.call_args_list
                if call.kwargs.get("prefix") == "book-video-render-"
            ]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].kwargs.get("dir"), project / "07_render")

    @unittest.skipUnless(os.name == "nt", "Git Bash path bridge is Windows-specific")
    def test_hbg_bash_command_uses_windows_pwd_and_posix_paths(self) -> None:
        command = _hbg_bash_command(
            Path("I:/repo/vendor/hbg/scripts/render_long_video.sh"),
            Path("I:/repo/workspace/project"),
            Path("I:/repo/output.mp4"),
        )

        self.assertTrue(str(command[0]).lower().endswith("bash.exe"))
        self.assertEqual(command[1], "-c")
        self.assertIn("builtin pwd -W", command[2])
        self.assertEqual(command[3], "hbg-script")
        self.assertNotIn("\\", command[4])
        self.assertNotIn("\\", command[5])
        self.assertNotIn("\\", command[6])

    def test_hbg_qa_runner_decodes_utf8_and_uses_bash_bridge(self) -> None:
        with mock.patch(
            "book_video_factory.render_stage.compiler.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as runner:
            _default_qa_runner(Path("video.mp4"), Path("qa"), [(1.0, "sample")])

        command = runner.call_args.args[0]
        self.assertEqual(runner.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(runner.call_args.kwargs["errors"], "replace")
        if os.name == "nt":
            self.assertEqual(command[1], "-c")
            self.assertIn("builtin pwd -W", command[2])

    def test_render_input_schema_is_valid_closed_json(self) -> None:
        package_root = Path(__file__).resolve().parents[1]
        schema = json.loads((package_root / "schemas/render_stage_input.v1.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["opening"]["additionalProperties"])
        self.assertEqual(schema["properties"]["output_name"]["pattern"], r"^[^/\\]+\.mp4$")

    def prepare_project(self, base: Path, *, approve_mix: bool = True) -> tuple[Path, Path, dict, dict, SimpleNamespace]:
        project = base / "projects/book"; project.mkdir(parents=True)
        for relative, text in {
            "SCRIPT_SOURCE.md": "source\n", "SCRIPT.md": "script\n", "CHARACTERS.md": "characters\n",
        }.items():
            (project / relative).write_text(text, encoding="utf-8")
        write_json(project / "PROJECT_SPEC.json", {
            "version": 2, "projectType": "classic-book-narration", "title": "Test", "titleLines": ["Test"],
            "book": {"title": "Test", "author": "Author", "sourceManifest": "source.json", "sourceLevel": "A", "factLedger": "facts.json"},
            "source": {"corrections": [], "chapters": [{"id": "CH01", "title": "One", "cue": "one"}]},
            "narration": {"provider": "edge-tts", "voice": "zh-CN-YunjianNeural", "bodyRate": "+0%", "leadRate": "+0%", "revealRate": "+0%", "pitch": "+0Hz", "captionMaxChars": 18, "captionMinChars": 6, "captionMinDuration": 0.55},
            "opening": {"mode": "classic-book-flash", "leadText": "lead", "leadDisplayText": "lead", "revealText": "reveal", "flashDuration": 1.667, "flashMedia": "assets/opening/flash.mp4", "finalImage": "", "flashLives": []},
            "visual": {"profile": "profile.json", "characters": "CHARACTERS.md", "anchorApproval": "approval.json"},
            "workflow": {"releaseId": "r1", "scriptContract": "script.narrator-essay.v1", "scriptLock": "lock.json", "stateAuthority": "workflow-gates-manifests"},
            "audio": {"narrationOutput": "assets/audio/narration.m4a", "bgmSource": "assets/audio/bgm/source.mp3", "bgmLooped": "assets/audio/bgm/looped.m4a"},
        })
        write_json(project / "HBG_STYLE.json", {
            "orientation": "landscape", "canvas": {"width": 1920, "height": 1080, "fps": 30},
            "captions": {
                "fontFamily": "PingFang SC", "fontSize": 48, "fontWeight": 750, "lineHeight": 1.35,
                "letterSpacingEm": 0.035, "textColor": "#FFFFFF", "backgroundRgba": "rgba(16,13,12,0.78)",
                "assBoxColor": "&H380C0D10", "boxPadding": 12, "htmlPadding": "13px 28px 16px",
                "borderRadius": 12, "bottom": 58, "maxWidth": 1540,
            },
            "audio": {"bgmVolume": 0.22},
        })
        write_json(project / "STORYBOARD.json", [
            {"id": "s1", "chapter": 1, "start": 2.0, "end": 5.0, "duration": 3.0, "motion": "zoom-in", "asset": "old.png"},
            {"id": "s2", "chapter": 1, "start": 5.0, "end": 8.0, "duration": 3.0, "motion": "pan-left", "asset": "old2.png"},
        ])
        write_json(project / "audio_meta.json", {
            "totalDuration": 8.0, "narrationDuration": 6.0,
            "opening": {
                "bodyStart": 2.0,
                "lead": {"path": "assets/audio/opening/lead.mp3"},
                "reveal": {"path": "assets/audio/opening/reveal.mp3"},
                "flash": {"audio": "assets/opening/flash.mp4"},
            },
            "body": {
                "path": "assets/audio/narration.m4a",
                "vtt": "assets/audio/narration-full.vtt",
                "duration": 6.0,
            },
            "captions": [
                {"id": "caption-0001", "start": 0.0, "end": 3.0, "duration": 3.0, "text": "海上仍有微光", "allowShort": False},
                {"id": "caption-0002", "start": 3.0, "end": 6.0, "duration": 3.0, "text": "老人没有放弃", "allowShort": False},
            ], "chapters": [{"chapter": 1, "id": "ch01", "title": "One", "start": 2.0, "end": 8.0, "duration": 6.0}],
        })
        section_defs = [
            ("S01", "theory", "海上仍有微光", "caption-0001", 0.0, 3.0),
            ("S02", "author_background", "老人没有放弃", "caption-0002", 3.0, 6.0),
        ]
        write_json(project / "02_story_script_故事脚本/SCRIPT_PACKAGE.json", {
            "performance_version": {
                "sections": [
                    {"section_id": sid, "narrative_function": function, "text": text}
                    for sid, function, text, _caption_id, _start, _end in section_defs
                ]
            }
        })
        write_json(project / "04_audio/CAPTION_BINDINGS.json", {
            "release_id": "r1",
            "captions": {
                caption_id: {
                    "caption_id": caption_id,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "start": start,
                    "end": end,
                }
                for sid, function, text, caption_id, start, end in section_defs
            },
        })
        write_json(project / "STORYBOARD_BASE.json", [
            {
                "beatId": f"B{index:03d}", "sectionId": sid, "cue": text,
                "description": text, "requiredEntities": [], "forbiddenEntities": [],
                "narrative_function": function,
            }
            for index, (sid, function, text, _caption_id, _start, _end) in enumerate(section_defs, start=1)
        ])
        write_json(project / "03_images_生成图片/BOOK_VISUAL_PROFILE.json", {
            "character_anchors": [], "object_anchors": [], "scene_anchors": []
        })
        build_caption_visual_contract_from_project(project, release_id="r1")
        build_caption_grouping_from_project(project)
        grouping = json.loads(
            (project / "04_audio/CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8")
        )
        for relative in (
            "assets/audio/opening/lead.mp3", "assets/audio/opening/reveal.mp3",
            "assets/audio/narration.m4a", "assets/audio/narration-full.vtt",
            "assets/audio/bgm/source.mp3",
            "assets/opening/flash.mp4", "assets/opening/preview.mp4",
        ):
            path = project / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((relative + "\n").encode())
        tasks: dict[str, dict[str, Any]] = {}
        for index, group in enumerate(grouping["groups"], start=1):
            task_id = f"SCENE_G{index:03d}"
            scene_id = f"caption-group-{str(group['group_id']).lower()}"
            shot_id = f"SHOT_G{index:03d}"
            caption_ids = [str(value) for value in group["caption_ids"]]
            caption_text = " / ".join(
                next(text for _sid, _function, text, caption_id, _start, _end in section_defs if caption_id == value)
                for value in caption_ids
            )
            proposition = VisualProposition(
                mode="Abstract", subject="克制的时代气氛", action="", environment="",
                mood="克制", lighting="soft", palette="earth",
                rationale_text="权威 Section 指定为非剧情抽象叙事，不引入隐藏人物。",
            )
            prompt = f"[1/9 CAPTION]\n{caption_text}\n[2/9 PROPOSITION]\n克制的时代气氛"
            aggregate = aggregate_contract_bindings_sha256(
                group["contract_bindings"], expected_caption_ids=caption_ids
            )
            beat_ids = [f"B{index:03d}"]
            binding = compute_prompt_binding(
                caption_ids=caption_ids,
                caption_text=caption_text,
                proposition=proposition,
                prompt=prompt,
                scene_id=scene_id,
                shot_id=shot_id,
                beat_ids=beat_ids,
                caption_visual_contract_sha256=aggregate,
                group_id=group["group_id"],
                caption_contract_bindings=group["contract_bindings"],
                caption_group_sha256=group["caption_group_sha256"],
            )
            tasks[task_id] = {
                "schema_version": "production-image-task.v1",
                "task_id": task_id,
                "scene_id": scene_id,
                "shot_id": shot_id,
                "caption_ids": caption_ids,
                "caption_text": caption_text,
                "caption_visual_contract_sha256": aggregate,
                "narrative_function": group["narrative_function"],
                "source_beat_ids": beat_ids,
                "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "visual_proposition": proposition.to_dict(),
                "prompt_binding": binding,
                "anchor_refs": [],
                "identity_reference_task_ids": [],
            }
        assets = []
        for index, task in enumerate(tasks.values(), start=1):
            relative = f"assets/generated/scenes/{task['scene_id']}.png"
            path = project / relative; path.parent.mkdir(parents=True, exist_ok=True)
            level = 20 if index == 1 else 220
            Image.new("RGB", (1920, 1080), (level, level, level)).save(path)
            assets.append({
                "task_id": task["task_id"], "scene_id": task["scene_id"], "path": relative,
                "sha256": sha256_file(path), "provider": "host-imagegen",
                "identity_reference_evidence": [],
            })
        scene_manifest = {"release_id": "r1", "assets": assets}
        approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
        write_json(project / "06_visual_production/SCENE_ASSET_MANIFEST.json", scene_manifest)
        write_json(project / "06_visual_production/SCENE_ASSET_APPROVAL.json", approval)
        write_json(project / "04_audio/AUDIO_STAGE_MANIFEST.json", {"release_id": "r1"})
        task_queue_path = project / "05_director/IMAGE_TASKS.jsonl"
        task_queue_path.parent.mkdir(parents=True, exist_ok=True)
        task_lines = [json.dumps(task, ensure_ascii=False) for task in tasks.values()]
        task_queue_path.write_text("\n".join(task_lines) + "\n", encoding="utf-8")
        timeline_path = project / "05_director/DIRECTOR_TIMELINE.json"
        write_json(timeline_path, {
            "schema_version": "director-timeline.v1", "release_id": "r1", "body_start": 2.0,
            "body_duration": 6.0, "scene_count": 2,
            "scenes": [
                {
                    "scene_id": task["scene_id"], "source_beat_ids": task["source_beat_ids"],
                    "chapter": 1, "start": 2.0 + float(group["start"]),
                    "end": 2.0 + float(group["end"]), "duration": float(group["duration"]),
                    "caption_ids": task["caption_ids"], "caption_text": task["caption_text"],
                    "narrative_cue": task["caption_text"], "visual_description": "当前抽象画面",
                    "semantic_rationale": "当前 Caption Group 直接约束画面",
                    "risk_flags": ["hero_shot"] if index == 1 else ["death_climax"],
                    "required_entities": [], "forbidden_entities": [], "anchor_refs": [],
                    "participants": {"count": 0, "allowed": [], "forbidden": []},
                    "motion": "zoom-in" if index == 1 else "pan-left", "generation_mode": "single",
                }
                for index, (task, group) in enumerate(zip(tasks.values(), grouping["groups"]), start=1)
            ],
        })
        director_path = project / "05_director/DIRECTOR_STAGE_MANIFEST.json"
        write_json(director_path, {"release_id": "r1", "output_hashes": {"05_director/DIRECTOR_TIMELINE.json": sha256_file(timeline_path)}})
        # Faithful scene-review decision with authoritative vision evidence for
        # every produced task. The render preflight now fails closed without it,
        # so the happy-path fixtures must supply it (previously relied on the
        # legacy_pass backdoor).
        write_json(project / "06_visual_production/SCENE_REVIEW_DECISION.json", {
            "schema_version": "scene-review-decision.v1",
            "release_id": "r1",
            "director_stage_manifest_sha256": sha256_file(director_path),
            "scene_asset_manifest_sha256": sha256_file(project / "06_visual_production/SCENE_ASSET_MANIFEST.json"),
            "reviewer": "Test Reviewer",
            "decisions": [
                {
                    "task_id": task["task_id"],
                    "semantic_review_status": "pass",
                    "reality_review_status": "pass",
                    "identity_review_status": "not_applicable",
                    "note": "Scene reviewed with authoritative current v2 evidence.",
                    "vision_evidence": _signed_scene_evidence(project, asset["path"], task["task_id"]),
                }
                for task, asset in zip(tasks.values(), assets)
            ],
        })
        director = SimpleNamespace(manifest_path=director_path)
        render_input = project / "07_render/RENDER_INPUT.json"
        write_json(render_input, {
            "schema_version": "render-stage-input.v1", "release_id": "r1", "renderer": "streaming_ffmpeg",
            "output_name": "book-v1.mp4", "bgm_source": "assets/audio/bgm/source.mp3",
            "opening": {"preview_video": "assets/opening/preview.mp4", "final_image_task_id": "SCENE_G001", "flash_task_ids": ["SCENE_G001", "SCENE_G002"]},
            "quality": "high", "minimum_free_gib": 1, "hyperframes_version": "1.2.3",
        })
        from book_video_factory.hbg_bridge.runner import repository_root

        vendor_lock = repository_root() / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json"
        write_json(project / "07_render/OPENING_PREVIEW_MANIFEST.json", {
            "schema_version": "opening-preview-manifest.v1",
            "release_id": "r1",
            "render_input_sha256": sha256_file(render_input),
            "scene_approval_sha256": sha256_file(project / "06_visual_production/SCENE_ASSET_APPROVAL.json"),
            "audio_stage_sha256": sha256_file(project / "04_audio/AUDIO_STAGE_MANIFEST.json"),
            "hbg_style_sha256": sha256_file(project / "HBG_STYLE.json"),
            "project_spec_sha256": sha256_file(project / "PROJECT_SPEC.json"),
            "preview_path": "assets/opening/preview.mp4",
            "preview_sha256": sha256_file(project / "assets/opening/preview.mp4"),
            "preview_bytes": (project / "assets/opening/preview.mp4").stat().st_size,
            "renderer": "hbg-hyperframes-opening-preview",
            "hbg_vendor_lock_sha256": sha256_file(vendor_lock),
            "next_stage_status": "awaiting_opening_mix_calibration",
        })
        if approve_mix:
            def probe(path: Path, seconds: float | None) -> dict[str, float]:
                if "preview" in path.name:
                    return {"duration_seconds": 18.0, "integrated_lufs": -14.0, "true_peak_dbtp": -3.5}
                return {"duration_seconds": 120.0 if seconds is None else seconds, "integrated_lufs": -15.3, "true_peak_dbtp": -4.2}
            calibration = calibrate_opening_mix(project, render_input, probe_runner=probe)
            approve_opening_mix(project, calibration.calibration_path, reviewer="Test Human", note="Test opening mix approved.")
        return project, render_input, tasks, scene_manifest, director

    def test_pipeline_status_requests_opening_preview_before_streaming_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _, _ = self.prepare_project(Path(temp))
            (project / "assets/opening/preview.mp4").unlink()
            (project / "07_render/OPENING_PREVIEW_MANIFEST.json").unlink()
            status = render_pipeline_status(project)
            self.assertEqual(status["status"], "awaiting_opening_preview")
            self.assertIn("run_render_stage.py preview", status["command"])

    def test_formal_render_is_blocked_until_exact_opening_mix_is_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp), approve_mix=False)
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            self.assertEqual(render_pipeline_status(project)["status"], "awaiting_opening_mix_calibration")
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                with self.assertRaisesRegex(RenderStageError, "mix|approval"):
                    prepare_render_stage(project, input_path)

                def probe(path: Path, seconds: float | None) -> dict[str, float]:
                    if "preview" in path.name:
                        return {"duration_seconds": 18.0, "integrated_lufs": -14.0, "true_peak_dbtp": -3.5}
                    return {"duration_seconds": 120.0 if seconds is None else seconds, "integrated_lufs": -15.3, "true_peak_dbtp": -4.2}

                calibration = calibrate_opening_mix(project, input_path, probe_runner=probe)
                self.assertEqual(render_pipeline_status(project)["status"], "awaiting_opening_mix_approval")
                approve_opening_mix(project, calibration.calibration_path, reviewer="Test Human", note="Exact gain and preview approved.")
                self.assertEqual(prepare_render_stage(project, input_path).next_stage_status, "awaiting_render_preflight")
                self.assertEqual(
                    preflight_render(project, input_path, command_runner=passing_preflight_runner, process_lister=lambda: []).next_stage_status,
                    "ready_for_hbg_render",
                )

    def test_prepare_rejects_stale_preview_after_valid_render_input_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            value = json.loads(input_path.read_text(encoding="utf-8"))
            value["quality"] = "standard"
            write_json(input_path, value)
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                with self.assertRaises(RenderStageError):
                    prepare_render_stage(project, input_path)

    def test_generates_hbg_opening_preview_before_streaming_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            preview = project / "assets/opening/preview.mp4"
            preview.unlink()
            (project / "07_render/OPENING_PREVIEW_MANIFEST.json").unlink()
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}

            calls: list[Path] = []
            def preview_runner(workspace: Path, output: Path, manifest: dict) -> None:
                calls.append(workspace)
                self.assertFalse((workspace / "assets/opening/preview.mp4").exists())
                self.assertEqual(output.suffix, ".mp4")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"hbg-opening-preview")

            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                result = generate_opening_preview(project, input_path, preview_runner=preview_runner)
                self.assertEqual(result.status, "created")
                self.assertEqual(result.next_stage_status, "awaiting_opening_mix_calibration")
                self.assertEqual(preview.read_bytes(), b"hbg-opening-preview")
                self.assertEqual(len(calls), 1)
                repeated = generate_opening_preview(project, input_path, preview_runner=preview_runner)
                self.assertEqual(repeated.status, "unchanged")
                self.assertEqual(len(calls), 1)
                def probe(path: Path, seconds: float | None) -> dict[str, float]:
                    if "preview" in path.name:
                        return {"duration_seconds": 18.0, "integrated_lufs": -14.0, "true_peak_dbtp": -3.5}
                    return {"duration_seconds": 120.0 if seconds is None else seconds, "integrated_lufs": -15.3, "true_peak_dbtp": -4.2}
                calibration = calibrate_opening_mix(project, input_path, probe_runner=probe)
                approve_opening_mix(project, calibration.calibration_path, reviewer="Test Human", note="Regenerated opening mix approved.")
                prepared = prepare_render_stage(project, input_path)
                self.assertEqual(prepared.next_stage_status, "awaiting_render_preflight")
                preflight_render(project, input_path, command_runner=passing_preflight_runner, process_lister=lambda: [])
                build_encoded_frame_plan(project)
                self.assertEqual(render_pipeline_status(project)["status"], "ready_for_hbg_render")

    def test_prepares_workspace_and_executes_hbg_runner_with_qa(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                prepared = prepare_render_stage(project, input_path)
                self.assertEqual(prepared.next_stage_status, "awaiting_render_preflight")
                rendered_storyboard = json.loads((prepared.workspace / "STORYBOARD.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    rendered_storyboard[0]["asset"],
                    "assets/generated/scenes/caption-group-g001.png",
                )
                preflight_render(project, input_path, command_runner=passing_preflight_runner, process_lister=lambda: [])
                build_encoded_frame_plan(project)

                def render_runner(workspace: Path, output: Path, manifest: dict) -> None:
                    self.assertEqual(output.suffix, ".mp4")
                    output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"not-real-but-nonempty-test-mp4")

                def qa_runner(video: Path, qa_dir: Path, frames: list[tuple[float, str]]) -> None:
                    qa_dir.mkdir(parents=True, exist_ok=True)
                    write_json(qa_dir / "ffprobe.json", {
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                        ],
                        "format": {"duration": "8.0"},
                    })
                    (qa_dir / "blackdetect.txt").write_text("", encoding="utf-8")
                    (qa_dir / "silencedetect.txt").write_text("", encoding="utf-8")
                    (qa_dir / "ebur128.txt").write_text("Peak: -3.5 dBFS\n", encoding="utf-8")
                    for index, (_seconds, label) in enumerate(frames, start=1):
                        Image.new("RGB", (1920, 1080), (20 * index, 30, 40)).save(qa_dir / f"{index:02d}-{label}.png")
                    Image.new("RGB", (1200, 600), (40, 40, 40)).save(qa_dir / "contact-sheet.jpg")

                result = execute_render_stage(project, input_path, render_runner=render_runner, qa_runner=qa_runner)
                self.assertEqual(result.next_stage_status, "awaiting_encoded_visual_review")
                self.assertTrue(result.output_path.is_file())
                final = json.loads((project / "08_render_合成/final/FINAL_RENDER_MANIFEST.json").read_text(encoding="utf-8"))
                self.assertEqual(final["video_sha256"], sha256_file(result.output_path))
                self.assertEqual(final["next_stage_status"], "awaiting_encoded_visual_review")
                self.assertEqual(render_pipeline_status(project)["status"], "awaiting_encoded_visual_review")

                plan_path = project / "09_qc/ENCODED_FRAME_PLAN.json"
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                decision_path = project / "09_qc/ENCODED_REVIEW_DECISION.json"
                write_json(decision_path, {
                    "schema_version": "encoded-visual-review-decision.v1", "release_id": "r1",
                    "video_sha256": sha256_file(result.output_path), "frame_plan_sha256": sha256_file(plan_path),
                    "reviewer": "Human Reviewer",
                    "decisions": [{
                        "sample_id": item["sample_id"], "semantic_status": "pass",
                        "visual_reality_status": "pass", "identity_status": "pass",
                        "caption_status": "pass" if {"caption_bright", "caption_dark"} & set(item["categories"]) else "not_applicable",
                        "note": "Reviewed.",
                        "caption_note": "ASS caption box and subject clearance reviewed." if {"caption_bright", "caption_dark"} & set(item["categories"]) else "",
                        # Every encoded sample must carry authoritative, signed vision evidence.
                        "vision_evidence": _signed_encoded_evidence(item["sample_id"]),
                    } for item in plan["samples"]],
                })
                reviewed = review_encoded_master(project, decision_path)
                self.assertEqual(reviewed.next_stage_status, "awaiting_final_master_approval")
                self.assertEqual(review_encoded_master(project, decision_path).status, "unchanged")
                self.assertEqual(render_pipeline_status(project)["status"], "awaiting_final_master_approval")

                approval_result = approve_final_master(
                    project, reviewer="Human Reviewer", note="Encoded master and QA evidence reviewed."
                )
                self.assertEqual(approval_result.next_stage_status, "complete")
                verified = verify_final_master_approval(project)
                self.assertTrue(verified["human_approved"])
                self.assertEqual(render_pipeline_status(project)["status"], "blocked_by_release_rights")
                repeated = approve_final_master(project, reviewer="Human Reviewer", note="Encoded master and QA evidence reviewed.")
                self.assertEqual(repeated.status, "unchanged")
                result.output_path.write_bytes(result.output_path.read_bytes() + b"tamper")
                with self.assertRaises(FinalMasterApprovalError):
                    verify_final_master_approval(project)

    def test_prepare_uses_group_director_timeline_when_legacy_storyboard_boundaries_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, _tasks, scene_manifest, director = self.prepare_project(Path(temp))
            timeline_path = project / "05_director/DIRECTOR_TIMELINE.json"
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            group_scene_ids = ["caption-group-g001", "caption-group-g002"]
            for scene, scene_id, caption_id in zip(
                timeline["scenes"], group_scene_ids, ("caption-0001", "caption-0002")
            ):
                scene["scene_id"] = scene_id
                scene["caption_ids"] = [caption_id]
                scene["source_beat_ids"] = ["B001"]
                scene["motion"] = "hold"
            write_json(timeline_path, timeline)

            group_tasks = {
                f"SCENE_G{index:03d}": {
                    "task_id": f"SCENE_G{index:03d}",
                    "scene_id": scene_id,
                }
                for index, scene_id in enumerate(group_scene_ids, start=1)
            }
            group_assets = []
            for task, asset in zip(group_tasks.values(), scene_manifest["assets"]):
                group_assets.append({**asset, "task_id": task["task_id"], "scene_id": task["scene_id"]})
            group_manifest = {**scene_manifest, "assets": group_assets}
            render_input = json.loads(input_path.read_text(encoding="utf-8"))
            render_input["opening"]["final_image_task_id"] = "SCENE_G001"
            render_input["opening"]["flash_task_ids"] = ["SCENE_G001", "SCENE_G002"]
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}

            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=group_tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, group_manifest)), \
                 mock.patch("book_video_factory.render_stage.compiler._render_input", return_value=render_input):
                prepared = prepare_render_stage(project, input_path)

            rendered = json.loads((prepared.workspace / "STORYBOARD.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in rendered], group_scene_ids)
            self.assertEqual([item["captionIds"] for item in rendered], [["caption-0001"], ["caption-0002"]])
            self.assertEqual([(item["start"], item["end"]) for item in rendered], [(2.0, 5.0), (5.0, 8.0)])
            self.assertEqual([item["asset"] for item in rendered], [asset["path"] for asset in group_assets])
            self.assertEqual(
                (prepared.workspace / "assets/audio/narration-full.vtt").read_bytes(),
                (project / "assets/audio/narration-full.vtt").read_bytes(),
            )
            self.assertEqual(
                (prepared.workspace / "05_director/DIRECTOR_TIMELINE.json").read_bytes(),
                timeline_path.read_bytes(),
            )


    def test_execute_rolls_back_published_files_when_final_manifest_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                prepare_render_stage(project, input_path)
                preflight_render(project, input_path, command_runner=passing_preflight_runner, process_lister=lambda: [])
                build_encoded_frame_plan(project)

                def render_runner(workspace: Path, output: Path, manifest: dict) -> None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered-test-video")

                def qa_runner(video: Path, qa_dir: Path, frames: list[tuple[float, str]]) -> None:
                    qa_dir.mkdir(parents=True, exist_ok=True)
                    write_json(qa_dir / "ffprobe.json", {
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                        ],
                        "format": {"duration": "8.0"},
                    })
                    (qa_dir / "blackdetect.txt").write_text("", encoding="utf-8")
                    (qa_dir / "silencedetect.txt").write_text("", encoding="utf-8")
                    (qa_dir / "ebur128.txt").write_text("Peak: -3.5 dBFS\n", encoding="utf-8")
                    for index, (_seconds, label) in enumerate(frames, start=1):
                        Image.new("RGB", (1920, 1080), (20 * index, 30, 40)).save(qa_dir / f"{index:02d}-{label}.png")
                    Image.new("RGB", (1200, 600), (40, 40, 40)).save(qa_dir / "contact-sheet.jpg")

                original_write_bytes = Path.write_bytes
                def fail_final_manifest(path: Path, data: bytes) -> int:
                    if path.name == "FINAL_RENDER_MANIFEST.json":
                        raise OSError("simulated final manifest failure")
                    return original_write_bytes(path, data)

                with mock.patch.object(Path, "write_bytes", new=fail_final_manifest):
                    with self.assertRaises(OSError):
                        execute_render_stage(project, input_path, render_runner=render_runner, qa_runner=qa_runner)

                self.assertFalse((project / "08_render_合成/final/book-v1.mp4").exists())
                self.assertFalse((project / "08_render_合成/final/FINAL_RENDER_MANIFEST.json").exists())
                self.assertFalse((project / "09_qc/final-video").exists())
                self.assertFalse((project / "09_qc/FINAL_QA_REPORT.json").exists())

    def test_prepare_rejects_workspace_tampering_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, input_path, tasks, scene_manifest, director = self.prepare_project(Path(temp))
            approval = {"release_id": "r1", "human_approved": True, "next_stage_status": "ready_for_render"}
            with mock.patch("book_video_factory.render_stage.compiler.compile_director_stage", return_value=director), \
                 mock.patch("book_video_factory.render_stage.compiler._tasks", return_value=tasks), \
                 mock.patch("book_video_factory.render_stage.compiler._scene_approval", return_value=(approval, scene_manifest)):
                prepared = prepare_render_stage(project, input_path)
                (prepared.workspace / "SCRIPT.md").write_text("tampered", encoding="utf-8")
                with self.assertRaises(RenderStageError):
                    prepare_render_stage(project, input_path)

    def test_final_qa_rejects_wrong_codec(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); video = base / "x.mp4"; video.write_bytes(b"x")
            qa = base / "qa"; qa.mkdir()
            write_json(qa / "ffprobe.json", {
                "streams": [
                    {"codec_type": "video", "codec_name": "vp9", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
                ], "format": {"duration": "8.0"},
            })
            Image.new("RGB", (1200, 600)).save(qa / "contact-sheet.jpg")
            with self.assertRaises(FinalVideoQaError):
                evaluate_final_video(video, qa, expected_duration=8.0)

    def test_final_qa_rejects_missing_planned_encoded_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); video = base / "x.mp4"; video.write_bytes(b"x")
            qa = base / "qa"; qa.mkdir()
            write_json(qa / "ffprobe.json", {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
                ], "format": {"duration": "8.0"},
            })
            Image.new("RGB", (1200, 600)).save(qa / "contact-sheet.jpg")
            with self.assertRaisesRegex(FinalVideoQaError, "encoded_frame_samples"):
                evaluate_final_video(
                    video,
                    qa,
                    expected_duration=8.0,
                    expected_frame_labels=["encoded-001"],
                )


if __name__ == "__main__":
    unittest.main()
