"""V2 autonomy E2E: Host doubles feed Runtime-owned production artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image

from book_video_factory.audio_stage.autonomy import run_narration_from_locked_script
from book_video_factory.audio_stage.providers.base import NarrationChunkRequest, NarrationChunkResult
from book_video_factory.director_stage.contracts import EDIT_TIMELINE_REL, resolve_edit_timeline
from book_video_factory.host_orchestration import read_ledgers, register_host_event
from book_video_factory.literary_director import complete_literary_director_from_host_event
from book_video_factory.manifests import sha256_file
from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.production_orchestration import (
    apply_verdicts,
    ensure_generate_actions,
    ensure_judge_actions,
    incorporate_generated_assets,
    production_status,
)
from book_video_factory.visual_covenant import (
    complete_visual_covenant_from_host_event,
    promote_covenant_assets,
    record_visual_covenant_approval,
)
from book_video_factory.v2_render import DELIVERY_MANIFEST_REL, render_delivery_status, verify_delivery_manifest


def _sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _make_png(project: Path, relative: str, color: tuple[int, int, int]) -> str:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), color).save(target, format="PNG")
    return sha256_file(target)


def _locked_project(tmp_path: Path, *, name: str = "pilot") -> Path:
    project = tmp_path / "warehouse" / "projects" / name
    _write_json(project / "01_research_资料搜集/SOURCE_MANIFEST.json", {
        "schema_version": "1.0",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "source_dir": "source",
        "files": [],
    })
    _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
        "schema_version": "source-sanitization-report.v1",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "detected_source_declarations": [],
    })
    script_text = "第一段旁白。\n\n第二段旁白，仍然属于同一个文学世界。"
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": sha256_file(script),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    _write_json(project / "config/PROVIDER_DOUBLES.json", {
        "narration_provider": "offline-e2e",
        "image_provider": "host-imagegen-double",
        "judge_provider": "host-multimodal-double",
    })
    return project


def _ledgers(project: Path) -> tuple[list[dict], list[dict]]:
    state = read_ledgers(project)
    return state["actions"], state["events"]


def _active_action(project: Path, action_type: str, marker: str) -> dict:
    actions, events = _ledgers(project)
    terminal = {str(item["idempotency_key"]) for item in events}
    candidates = [
        item for item in actions
        if item.get("action_type") == action_type
        and marker in str(item.get("idempotency_key", ""))
        and str(item.get("idempotency_key")) not in terminal
    ]
    assert candidates, f"no active {action_type} action containing {marker}"
    return sorted(candidates, key=lambda item: item["attempt"])[-1]


def _host_covenant_double(project: Path, action: dict) -> dict:
    char_path = "03_images_生成图片/covenant/CHAR.png"
    loc_path = "03_images_生成图片/covenant/LOCATION.png"
    char_sha = _make_png(project, char_path, (120, 140, 160))
    loc_sha = _make_png(project, loc_path, (140, 120, 100))
    world_profile = {
        "period": "fictional literary present",
        "geography": "an unnamed inland town",
        "visual_world": "restrained literary realism",
        "materials": ["wood", "paper", "wool"],
        "palette": ["slate", "umber", "paper white"],
        "lighting": ["soft overcast daylight", "quiet practical light"],
        "composition": ["negative space", "stable tableau", "subtitle-safe lower frame"],
        "negative_constraints": ["no modern logos", "no watermark", "no embedded text"],
    }
    world_profile_sha = _sha(json.dumps(world_profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    result_path = project / action["expected_output"]["result_path"]
    _write_json(result_path, {
        "schema_version": "visual-covenant-plan.v2",
        "release_id": action["release_id"],
        "project_id": project.name,
        "locked_script_sha256": action["inputs"][0]["sha256"],
        "world_profile": world_profile,
        "world_profile_sha256": world_profile_sha,
        "assets": [
            {
                "asset_id": "COV_CHAR",
                "asset_family": "character:subject",
                "visual_function": "character_portrait",
                "source": "visual_covenant",
                "production_eligible": True,
                "technical_status": "verified",
                "path": char_path,
                "file_sha256": char_sha,
                "provenance": {"provider": "host-imagegen-double", "tool_call_id": "cov-char-double"},
            },
            {
                "asset_id": "COV_LOC",
                "asset_family": "location:town",
                "visual_function": "location_establishing",
                "source": "visual_covenant",
                "production_eligible": True,
                "technical_status": "verified",
                "path": loc_path,
                "file_sha256": loc_sha,
                "provenance": {"provider": "host-imagegen-double", "tool_call_id": "cov-location-double"},
            },
        ],
    })
    return {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": action["action_type"],
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen-double",
        "tool_call_id": "cov-plan-double",
        "output_path": result_path.relative_to(project).as_posix(),
        "output_sha256": sha256_file(result_path),
    }


class _NarrationDouble:
    provider_id = "offline-e2e"
    policy = "minimax_required"
    model = "offline-model"
    voice_id = "offline-voice"

    def synthesize(self, request: NarrationChunkRequest, *, evidence_dir: Path) -> NarrationChunkResult:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        audio_path = evidence_dir / f"{request.chunk_id}.wav"
        with wave.open(str(audio_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x00" * 800)
        return NarrationChunkResult(
            chunk_id=request.chunk_id,
            provider=self.provider_id,
            model=self.model,
            voice_id=self.voice_id,
            audio_path=audio_path.name,
            audio_sha256=sha256_file(audio_path),
            duration=0.1,
            subtitle_timestamps=({"start": 0.0, "end": 0.1, "text": request.text},),
            subtitle_granularity="sentence",
            trace_id=f"trace-{request.chunk_id}",
            request_digest=request.digest(),
        )


def _host_director_double(project: Path, action: dict) -> dict:
    result_path = project / action["expected_output"]["result_path"]
    audio_path = project / "04_audio/AUDIO_TIMELINE.v2.json"
    captions_path = project / "04_audio/CAPTION_TIMELINE.json"
    catalog_path = project / "manifests/asset_catalog/ASSET_CATALOG.v2.json"
    duration = json.loads(audio_path.read_text(encoding="utf-8"))["narration_duration_seconds"]
    script_input = next(item for item in action["inputs"] if item["path"].endswith("SCRIPT_RELEASE.md"))
    approval_input = next(item for item in action["inputs"] if item["path"].endswith("VISUAL_COVENANT_APPROVAL.v2.json"))
    policy_input = next(item for item in action["inputs"] if item["kind"] == "director_policy")
    approval_sha = json.loads((project / approval_input["path"]).read_text(encoding="utf-8"))["visual_covenant_approval_sha256"]
    _write_json(result_path, {
        "schema_version": "literary-director-result.v2",
        "release_id": action["release_id"],
        "project_id": project.name,
        "locked_script_sha256": script_input["sha256"],
        "audio_timeline_sha256": sha256_file(audio_path),
        "caption_timeline_sha256": sha256_file(captions_path),
        "visual_covenant_approval_sha256": approval_sha,
        "asset_catalog_sha256": sha256_file(catalog_path),
        "director_policy_sha256": policy_input["sha256"],
        "paragraphs": [{
            "paragraph_id": "VP_001",
            "start": 0.0,
            "end": duration,
            "source_caption_ids": ["C1", "C2"],
            "narrative_focus": "quiet reflection",
            "visual_function": "character_narration",
            "visual_center": "the subject",
            "mood": "steadfast",
            "rationale": "one visual paragraph for the locked narration",
            "decision": "generate_new",
            "asset_family": "character:subject",
            "minimal_generation_intent": "a restrained portrait in the approved literary world",
            "recommended_reference_asset_ids": ["COV_CHAR"],
        }],
    })
    return {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": action["action_type"],
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-literary-director-double",
        "tool_call_id": "director-plan-double",
        "output_path": result_path.relative_to(project).as_posix(),
        "output_sha256": sha256_file(result_path),
    }


def _host_generate_double(project: Path, asset_id: str) -> None:
    action = _active_action(project, "generate_image", f"GEN_{asset_id}:")
    relative = f"03_images_生成图片/production/{asset_id}.png"
    image_sha = _make_png(project, relative, (160, 130, 110))
    register_host_event(project, {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "generate_image",
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen-double",
        "tool_call_id": "production-image-double",
        "output_path": relative,
        "output_sha256": image_sha,
    })
    incorporate_generated_assets(project)


def _host_judge_double(project: Path, asset_id: str) -> None:
    ensure_judge_actions(project)
    action = _active_action(project, "judge_visual_asset", f"JUDGE_{asset_id}:")
    expected = action["expected_output"]
    result_path = project / f"03_images_生成图片/judgments/{asset_id}.json"
    _write_json(result_path, {
        "schema_version": "multimodal-visual-judge.v2",
        "action_id": action["action_id"],
        "asset_id": asset_id,
        "asset_sha256": expected["asset_sha256"],
        "covenant_approval_sha256": expected["covenant_approval_sha256"],
        "visual_paragraph_sha256": expected["visual_paragraph_sha256"],
        "judge_policy_sha256": expected["judge_policy_sha256"],
        "verdict": "pass",
        "dimensions": {},
        "hard_failures": [],
        "warnings": [],
        "reason": "offline production judge double",
        "judge_model": "offline-judge",
        "judge_call_id": "judge-double",
    })
    register_host_event(project, {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "judge_visual_asset",
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-multimodal-double",
        "tool_call_id": "judge-double",
        "output_path": result_path.relative_to(project).as_posix(),
        "output_sha256": sha256_file(result_path),
    })
    apply_verdicts(project)


def test_autonomy_end_to_end_uses_runtime_wiring_and_one_covenant_approval(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)

    planned = pipeline_status(project)
    assert planned["status"] == "host_action_pending"
    assert planned["host_action"]["action_type"] == "plan_visual_covenant"
    action = planned["host_action"]
    complete_visual_covenant_from_host_event(project, _host_covenant_double(project, action))
    approval = record_visual_covenant_approval(
        project,
        reviewer="fixture-reviewer",
        approved_at="2026-08-22T00:00:00+08:00",
    )
    promote_covenant_assets(project)
    approval_events = list((project / "04_visual_covenant_视觉契约/approval_events").glob("*.json"))
    assert len(approval_events) == 1

    narration = run_narration_from_locked_script(project, _NarrationDouble())
    assert narration["status"] == "audio_timeline_ready"
    assert (project / "04_audio/AUDIO_TIMELINE.v2.json").is_file()

    director_status = pipeline_status(project)
    assert director_status["host_action"]["action_type"] == "plan_literary_director"
    complete_literary_director_from_host_event(project, _host_director_double(project, director_status["host_action"]))
    assert (project / "04_director/VISUAL_PARAGRAPHS.json").is_file()
    assert (project / "04_director/EDIT_DECISIONS.v2.json").is_file()
    assert (project / "04_director/PRODUCTION_IMAGE_PLAN.v2.json").is_file()

    ensure_generate_actions(project)
    generation_action = _active_action(project, "generate_image", "GEN_PROD_VP_001:")
    covenant = json.loads((project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json").read_text(encoding="utf-8"))
    world_input = next(item for item in generation_action["inputs"] if item["kind"] == "world_profile")
    assert world_input["sha256"] == covenant["world_profile_sha256"]
    assert generation_action["expected_output"]["world_profile"] == covenant["world_profile"]
    _host_generate_double(project, "PROD_VP_001")
    _host_judge_double(project, "PROD_VP_001")
    production_entry = next(
        item for item in json.loads(
            (project / "manifests/asset_catalog/ASSET_CATALOG.v2.json").read_text(encoding="utf-8")
        )["assets"] if item["asset_id"] == "PROD_VP_001"
    )
    assert production_entry["world_profile_sha256"] == covenant["world_profile_sha256"]
    assert production_status(project)["status"] == "asset_catalog_ready"
    assert resolve_edit_timeline(project)["status"] == "edit_timeline_resolved"
    assert json.loads((project / EDIT_TIMELINE_REL).read_text(encoding="utf-8"))["events"]

    awaiting = pipeline_status(project)
    assert awaiting["status"] == "awaiting_static_render"
    assert "execute" in awaiting["command"]
    assert not (project / "08_render_合成/final/V2_AUTONOMY.mp4").exists()

    runner = Path(__file__).resolve().parents[1] / "scripts/run_v2_render.py"
    executed = subprocess.run(
        [sys.executable, str(runner), "execute", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    manifest = json.loads((project / DELIVERY_MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["auto_delivered"] is True
    assert json.loads((project / "08_render_合成/final/ENCODED_QA.v2.json").read_text(encoding="utf-8"))["status"] == "pass"
    assert render_delivery_status(project)["status"] == "delivered"
    assert verify_delivery_manifest(project)["status"] == "delivery_verified"
    assert approval["visual_covenant_approval_sha256"]
