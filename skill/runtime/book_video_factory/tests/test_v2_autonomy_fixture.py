"""End-to-end V2 autonomy acceptance fixture (Stages 1-6, offline).

Drives the full autonomous loop: locked script, approved covenant with two
eligible assets, a generate_new paragraph that is retried once and then passes,
hold_current resolution, non-contiguous covenant asset reuse, then the static
render + encoded QA + auto delivery with BGM none. No human approval gate is
ever required, and every tamper fails closed.
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from PIL import Image

from book_video_factory.director_stage.contracts import EDIT_TIMELINE_REL, resolve_edit_timeline
from book_video_factory.host_orchestration import read_ledgers, register_host_event
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
    covenant_canonical_sha,
    promote_covenant_assets,
    record_visual_covenant_approval,
)
from book_video_factory.v2_render import (
    DEFAULT_VIDEO_REL,
    DELIVERY_MANIFEST_REL,
    V2RenderError,
    render_static,
    render_delivery_status,
    verify_delivery_manifest,
)


def _sha(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _make_png(project: Path, relative: str) -> str:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (120, 140, 160)).save(target, format="PNG")
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
    script_text = "autonomous V2 narration for the autonomy acceptance fixture"
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script_text),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    assets = [
        {
            "asset_id": "COV_CHAR",
            "asset_family": "character:jane",
            "visual_function": "character_narration",
            "source": "visual_covenant",
            "production_eligible": True,
            "technical_status": "verified",
            "path": "03_images_生成图片/covenant/COV_CHAR.png",
            "file_sha256": _make_png(project, "03_images_生成图片/covenant/COV_CHAR.png"),
            "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-char"},
        },
        {
            "asset_id": "COV_LOC",
            "asset_family": "location:thrushcross",
            "visual_function": "location_establishing",
            "source": "visual_covenant",
            "production_eligible": True,
            "technical_status": "verified",
            "path": "03_images_生成图片/covenant/COV_LOC.png",
            "file_sha256": _make_png(project, "03_images_生成图片/covenant/COV_LOC.png"),
            "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-loc"},
        },
    ]
    covenant_payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": name,
        "locked_script_sha256": _sha(script_text),
        "assets": assets,
    }
    covenant_payload["visual_covenant_sha256"] = covenant_canonical_sha(covenant_payload)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant_payload)
    record_visual_covenant_approval(project, reviewer="fixture-reviewer", approved_at="2026-08-22T00:00:00+08:00")
    promote_covenant_assets(project)
    return project


def _vp(paragraph_id: str, start: float, end: float) -> dict:
    return {
        "paragraph_id": paragraph_id,
        "start": start,
        "end": end,
        "source_caption_ids": ["C1"],
        "visual_intent": "stable hold of the established imagery",
        "mood": "steadfast",
    }


def _plan_asset(asset_id: str, *, family: str, func: str, bound_paragraph_id: str) -> dict:
    return {
        "asset_id": asset_id,
        "asset_family": family,
        "visual_function": func,
        "bound_paragraph_id": bound_paragraph_id,
    }


def _audio_timeline(project: Path, *, duration: float) -> str:
    master_path = project / "04_audio/master.wav"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(master_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(duration * 8000))
    provider_vtt = project / "04_audio/provider.vtt"
    provider_vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nautonomous narration\n", encoding="utf-8")
    captions = project / "04_audio/CAPTION_TIMELINE.json"
    _write_json(captions, {"captions": [{"start": 0.0, "end": duration, "text": "autonomous narration"}]})
    audio = {
        "schema_version": "audio-timeline.v2",
        "release_id": "release-1",
        "project_id": project.name,
        "narration_master_path": "04_audio/master.wav",
        "narration_master_sha256": sha256_file(master_path),
        "provider_vtt_path": "04_audio/provider.vtt",
        "provider_vtt_sha256": sha256_file(provider_vtt),
        "caption_timeline_path": "04_audio/CAPTION_TIMELINE.json",
        "caption_timeline_sha256": sha256_file(captions),
        "timing_authority": "provider",
        "narration_duration_seconds": duration,
        "bgm": {"bgm_mode": "none"},
    }
    _write_json(project / "04_audio/AUDIO_TIMELINE.v2.json", audio)
    return sha256_file(project / "04_audio/AUDIO_TIMELINE.v2.json")


def _director_and_plan(project: Path, *, paragraphs: list, decisions: list, plan_assets: list) -> None:
    duration = float(paragraphs[-1]["end"])
    audio_sha = _audio_timeline(project, duration=duration)
    _write_json(project / "04_director/VISUAL_PARAGRAPHS.json", {
        "schema_version": "visual-paragraphs.v1",
        "release_id": "release-1",
        "project_id": project.name,
        "audio_timeline_sha256": audio_sha,
        "narration_duration_seconds": duration,
        "paragraphs": paragraphs,
    })
    vp_sha = sha256_file(project / "04_director/VISUAL_PARAGRAPHS.json")
    _write_json(project / "04_director/EDIT_DECISIONS.v2.json", {
        "schema_version": "edit-decisions.v2",
        "release_id": "release-1",
        "project_id": project.name,
        "visual_paragraphs_sha256": vp_sha,
        "decisions": decisions,
    })
    _write_json(project / "04_director/PRODUCTION_IMAGE_PLAN.v2.json", {
        "schema_version": "production-image-plan.v2",
        "release_id": "release-1",
        "project_id": project.name,
        "assets": plan_assets,
    })


def _decision(paragraph_id: str, decision: str, asset_id: str | None = None) -> dict:
    item = {"paragraph_id": paragraph_id, "decision": decision}
    if asset_id is not None:
        item["asset_id"] = asset_id
    return item


def _ledgers(project: Path) -> tuple:
    state = read_ledgers(project)
    return state["actions"], state["events"]


def _active_action(project: Path, action_type: str, asset_id: str) -> dict:
    actions, events = _ledgers(project)
    terminal = {str(item["idempotency_key"]) for item in events}
    prefix = {"generate_image": "GEN_", "judge_visual_asset": "JUDGE_"}[action_type]
    candidates = [
        item
        for item in actions
        if item.get("action_type") == action_type
        and f"{prefix}{asset_id}:" in str(item.get("idempotency_key", ""))
        and str(item.get("idempotency_key")) not in terminal
    ]
    assert candidates, f"no active {action_type} action for {asset_id}"
    return sorted(candidates, key=lambda item: item["attempt"])[-1]


def _simulate_generate(project: Path, asset_id: str) -> int:
    action = _active_action(project, "generate_image", asset_id)
    image_relative = f"03_images_生成图片/production/{asset_id}_a{action['attempt']}.png"
    image_sha = _make_png(project, image_relative)
    event = {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "generate_image",
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen",
        "tool_call_id": f"call-gen-{asset_id}-{action['attempt']}",
        "output_path": image_relative,
        "output_sha256": image_sha,
    }
    register_host_event(project, event)
    incorporate_generated_assets(project)
    return action["attempt"]


def _simulate_judge(project: Path, asset_id: str, verdict: dict) -> None:
    ensure_judge_actions(project)
    action = _active_action(project, "judge_visual_asset", asset_id)
    expected = action["expected_output"]
    payload = {
        "schema_version": "multimodal-visual-judge.v2",
        "action_id": action["action_id"],
        "asset_id": asset_id,
        "asset_sha256": expected["asset_sha256"],
        "covenant_approval_sha256": expected["covenant_approval_sha256"],
        "visual_paragraph_sha256": expected["visual_paragraph_sha256"],
        "judge_policy_sha256": expected["judge_policy_sha256"],
        "verdict": verdict["verdict"],
        "dimensions": verdict.get("dimensions", {}),
        "hard_failures": verdict.get("hard_failures", []),
        "warnings": verdict.get("warnings", []),
        "reason": verdict.get("reason", "host multimodal judgment"),
        "retry_strategy": verdict.get("retry_strategy"),
        "judge_model": verdict.get("judge_model", "glm-vision"),
        "judge_call_id": verdict.get("judge_call_id", f"call-judge-{asset_id}"),
    }
    verdict_relative = f"03_images_生成图片/judgments/{asset_id}_a{action['attempt']}.json"
    _write_json(project / verdict_relative, payload)
    event = {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "judge_visual_asset",
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-multimodal",
        "tool_call_id": f"call-judge-{asset_id}-{action['attempt']}",
        "output_path": verdict_relative,
        "output_sha256": sha256_file(project / verdict_relative),
    }
    register_host_event(project, event)
    apply_verdicts(project)


def _catalog_asset(project: Path, asset_id: str) -> dict:
    path = project / "manifests/asset_catalog/ASSET_CATALOG.v2.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    for item in catalog["assets"]:
        if item["asset_id"] == asset_id:
            return item
    raise AssertionError(f"asset {asset_id} not in catalog")


def _events(project: Path) -> list[dict]:
    return json.loads((project / EDIT_TIMELINE_REL).read_text(encoding="utf-8"))["events"]


def _write_rendered_evidence(project: Path) -> None:
    render_static(project)


def test_autonomy_end_to_end_no_human_gates(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    paragraphs = [
        _vp("VP_001", 0.0, 8.0),
        _vp("VP_002", 8.0, 16.0),
        _vp("VP_003", 16.0, 24.0),
        _vp("VP_004", 24.0, 32.0),
        _vp("VP_005", 32.0, 40.0),
    ]
    decisions = [
        _decision("VP_001", "generate_new", "PROD_001"),
        _decision("VP_002", "hold_current"),
        _decision("VP_003", "reuse_asset", "COV_LOC"),
        _decision("VP_004", "reuse_asset", "COV_CHAR"),
        _decision("VP_005", "reuse_asset", "COV_LOC"),
    ]
    plan_assets = [
        _plan_asset("PROD_001", family="character:jane", func="character_narration", bound_paragraph_id="VP_001"),
    ]
    _director_and_plan(project, paragraphs=paragraphs, decisions=decisions, plan_assets=plan_assets)

    # Autonomous Host loop: generate -> retryable judge -> revised generate -> pass.
    ensure_generate_actions(project)
    assert _simulate_generate(project, "PROD_001") == 1
    _simulate_judge(project, "PROD_001", {
        "verdict": "retryable_failure",
        "hard_failures": ["anachronistic_modern_infection"],
        "retry_strategy": {"prompt_strategy": "remove_modern_infection"},
        "reason": "第一次画面出现现代污染。",
    })
    assert _catalog_asset(project, "PROD_001")["status"] == "pending_generation"
    ensure_generate_actions(project)
    assert _simulate_generate(project, "PROD_001") == 2
    _simulate_judge(project, "PROD_001", {"verdict": "pass", "reason": "修改后的生产画面通过审查。"})
    assert _catalog_asset(project, "PROD_001")["status"] == "approved_production_asset"
    assert production_status(project)["status"] == "asset_catalog_ready"

    # Only the failed PROD_001 was retried (attempts 1 and 2 only, no attempt 3),
    # and the covenant assets were never sent through a generate action.
    actions, _ = _ledgers(project)
    gen_keys = [a["idempotency_key"] for a in actions if a["action_type"] == "generate_image"]
    assert not any("attempt-3" in key for key in gen_keys)
    for asset_id in ("COV_CHAR", "COV_LOC"):
        assert not any(f"GEN_{asset_id}:" in key for key in gen_keys)
    assert not any("GEN_PROD_001:" not in key and "GEN_" in key for key in gen_keys)

    # Resolve edit timeline: hold_current -> PROD_001, and COV_LOC reused
    # non-contiguously (VP_003 and VP_005 separated by COV_CHAR).
    result = resolve_edit_timeline(project)
    assert result["status"] == "edit_timeline_resolved"
    events = _events(project)
    assert [item["asset_id"] for item in events] == ["PROD_001", "COV_LOC", "COV_CHAR", "COV_LOC"]
    assert events[1]["asset_id"] == "COV_LOC" and events[3]["asset_id"] == "COV_LOC"
    assert events[1]["edit_id"] != events[3]["edit_id"]

    # Static render + encoded QA + auto delivery, with a non-blocking bgm.
    status = pipeline_status(project)
    assert status["status"] == "delivered"
    assert status["human_review_required"] is False
    delivered = render_delivery_status(project)
    assert delivered["status"] == "delivered"
    verified = verify_delivery_manifest(project)
    assert verified["status"] == "delivery_verified"
    manifest = json.loads((project / DELIVERY_MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["human_approved"] is False
    assert manifest["auto_delivered"] is True
    audio = json.loads((project / "04_audio/AUDIO_TIMELINE.v2.json").read_text(encoding="utf-8"))
    assert audio["bgm"]["bgm_mode"] == "none"

    # Tampering the delivered video fails closed and never falls back to a human gate.
    video_path = project / DEFAULT_VIDEO_REL
    video_path.write_bytes(b"tampered-after-delivery")
    with pytest.raises(V2RenderError) as excinfo:
        verify_delivery_manifest(project)
    assert "video sha256 is stale" in str(excinfo.value)
    blocked = pipeline_status(project)
    assert blocked["status"] == "blocked_by_encoded_qa"
    assert blocked["human_review_required"] is False
