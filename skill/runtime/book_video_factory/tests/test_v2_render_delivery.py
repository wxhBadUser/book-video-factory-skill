"""V2 static render + encoded QA + auto delivery tests (Stage 6, offline)."""

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
    world_profile_canonical_sha,
)
from book_video_factory.v2_render import (
    DEFAULT_VIDEO_REL,
    DELIVERY_MANIFEST_REL,
    ENCODED_QA_REL,
    V2RenderError,
    _media,
    finalize_delivery,
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


def _locked_project(tmp_path: Path, *, name: str = "pilot", asset_ids: tuple[str, ...] = ("COV_CHAR", "COV_LOC")) -> Path:
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
    script_text = "locked narration for render delivery"
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
    assets = []
    for index, asset_id in enumerate(asset_ids):
        relative = f"03_images_生成图片/covenant/{asset_id}.png"
        image_sha = _make_png(project, relative)
        family = "character:jane" if index == 0 else "location:thrushcross"
        func = "character_narration" if index == 0 else "location_establishing"
        assets.append({
            "asset_id": asset_id,
            "asset_family": family,
            "visual_function": func,
            "source": "visual_covenant",
            "production_eligible": True,
            "technical_status": "verified",
            "path": relative,
            "file_sha256": image_sha,
            "provenance": {"provider": "host-imagegen", "tool_call_id": f"call-{asset_id}"},
        })
    covenant_payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": name,
        "locked_script_sha256": _sha(script_text),
        "world_profile": {"period": "nineteenth-century Yorkshire", "palette": "muted earth tones"},
        "assets": assets,
    }
    covenant_payload["world_profile_sha256"] = world_profile_canonical_sha(covenant_payload["world_profile"])
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


def _audio_timeline(project: Path, *, duration: float = 24.0) -> str:
    master_path = project / "04_audio/master.wav"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(master_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(duration * 8000))
    provider_vtt = project / "04_audio/provider.vtt"
    provider_vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nlocked narration\n", encoding="utf-8")
    captions = project / "04_audio/CAPTION_TIMELINE.json"
    _write_json(captions, {"captions": [{"start": 0.0, "end": duration, "text": "locked narration"}]})
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


def _director_and_plan(
    project: Path,
    *,
    paragraphs: list,
    decisions: list,
    plan_assets: list,
    duration: float | None = None,
) -> None:
    if duration is None:
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


def _simulate_generate(project: Path, asset_id: str) -> None:
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


def _approve_production_assets(project: Path) -> None:
    """Run generate + judge for every pending production asset, approving each."""
    while True:
        ensured = ensure_generate_actions(project)
        ensure_judge_actions(project)
        actions, events = _ledgers(project)
        terminal = {str(item["idempotency_key"]) for item in events}
        judge_keys = [
            a["idempotency_key"]
            for a in actions
            if a.get("action_type") == "judge_visual_asset"
            and str(a.get("idempotency_key")) not in terminal
        ]
        if judge_keys:
            for key in judge_keys:
                asset_id = _parse_asset_id(key, "JUDGE")
                _simulate_judge(project, asset_id, {"verdict": "pass", "reason": "生产画面通过审查。"})
            continue
        gen_keys = [
            a["idempotency_key"]
            for a in actions
            if a.get("action_type") == "generate_image"
            and str(a.get("idempotency_key")) not in terminal
        ]
        if gen_keys:
            for key in gen_keys:
                asset_id = _parse_asset_id(key, "GEN")
                _simulate_generate(project, asset_id)
            continue
        break


def _parse_asset_id(key: str, prefix: str) -> str:
    marker = f":{prefix}_"
    _, tail = key.split(marker, 1)
    asset_id, _ = tail.split(":attempt-", 1)
    return asset_id


def _render_eligible_project(tmp_path: Path, *, asset_ids: tuple[str, ...] = ("COV_CHAR", "COV_LOC")) -> Path:
    project = _locked_project(tmp_path, asset_ids=asset_ids)
    # One generate_new paragraph per requested production asset.
    paragraphs = []
    decisions = []
    plan_assets = []
    families = {
        "COV_CHAR": ("character:jane", "character_narration"),
        "COV_LOC": ("location:thrushcross", "location_establishing"),
    }
    for index, asset_id in enumerate(asset_ids, start=1):
        paragraph_id = f"VP_{index:03d}"
        paragraphs.append(_vp(paragraph_id, (index - 1) * 12.0, index * 12.0))
        decisions.append(_decision(paragraph_id, "generate_new", asset_id))
        family, func = families[asset_id]
        plan_assets.append(_plan_asset(asset_id, family=family, func=func, bound_paragraph_id=paragraph_id))
    _director_and_plan(project, paragraphs=paragraphs, decisions=decisions, plan_assets=plan_assets)
    _approve_production_assets(project)
    return project


def _write_rendered_evidence(project: Path) -> tuple[str, str]:
    rendered = render_static(project)
    return rendered["video_sha256"], json.dumps(rendered["qa"], ensure_ascii=False)


def test_render_status_awaits_render_not_asset_catalog(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    # Production is ready, but no resolved timeline means we stay blocked.
    result = resolve_edit_timeline(project)
    assert result["status"] == "edit_timeline_resolved"
    status = render_delivery_status(project)
    assert status["status"] == "awaiting_static_render"
    assert status["human_review_required"] is False
    assert "awaiting_scene_approval" not in json.dumps(status)
    assert "awaiting_final_master_approval" not in json.dumps(status)


def test_pipeline_status_advances_to_render_when_catalog_ready(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    status = pipeline_status(project)
    assert status["stage"] == "render"
    assert status["status"] == "awaiting_static_render"
    assert status["human_review_required"] is False
    assert "execute" in status["command"]
    assert not (project / DEFAULT_VIDEO_REL).exists()
    assert "awaiting_final_master_approval" not in json.dumps(status)


def test_delivery_autofinalizes_from_real_evidence(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    video_sha, _ = _write_rendered_evidence(project)
    status = render_delivery_status(project)
    assert status["status"] == "delivered"
    # The first call auto-finalizes (flat result); the second verifies and
    # reports the nested delivery evidence binding.
    status = render_delivery_status(project)
    assert status["status"] == "delivered"
    assert status["delivery"]["video_sha256"] == video_sha
    verified = verify_delivery_manifest(project)
    assert verified["status"] == "delivery_verified"
    assert verified["video_sha256"] == video_sha


def test_finalize_delivery_binds_all_hashes(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    video_path = project / DEFAULT_VIDEO_REL
    video_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_static(project)
    video_sha = rendered["video_sha256"]
    result = finalize_delivery(
        project,
        video_relative=DEFAULT_VIDEO_REL,
        video_sha=video_sha,
    )
    assert result["status"] == "delivered"
    manifest = json.loads((project / DELIVERY_MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["human_approved"] is False
    assert manifest["auto_delivered"] is True
    verified = verify_delivery_manifest(project)
    assert verified["status"] == "delivery_verified"


def test_tampered_video_fails_closed(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    video_sha, _ = _write_rendered_evidence(project)
    assert render_delivery_status(project)["status"] == "delivered"
    video_path = project / DEFAULT_VIDEO_REL
    video_path.write_bytes(b"tampered-video-bytes")
    with pytest.raises(V2RenderError) as excinfo:
        verify_delivery_manifest(project)
    assert "video sha256 is stale" in str(excinfo.value)
    status = render_delivery_status(project)
    assert status["status"] == "blocked_by_encoded_qa"


def test_tampered_delivery_manifest_fails_closed(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    _write_rendered_evidence(project)
    render_delivery_status(project)
    manifest_path = project / DELIVERY_MANIFEST_REL
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["human_approved"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(V2RenderError) as excinfo:
        verify_delivery_manifest(project)
    assert "must not require human approval" in str(excinfo.value)
    status = render_delivery_status(project)
    assert status["status"] == "delivered"


def test_missing_edit_timeline_fails_closed_blocked(tmp_path: Path) -> None:
    # A project with no resolved edit timeline must report a blocked render
    # state rather than crash while reading the timeline for its release id.
    project = _locked_project(tmp_path)
    status = render_delivery_status(project)
    assert status["status"] == "blocked_by_render_integrity"
    assert status["release_id"] == ""


def test_traversal_narration_master_fails_closed(tmp_path: Path) -> None:
    project = _render_eligible_project(tmp_path)
    resolve_edit_timeline(project)
    # A traversal narration master path must be rejected as out of bounds by
    # the fail-closed media guard, not resolved to a path outside the project.
    with pytest.raises(V2RenderError, match="out of bounds"):
        _media(project, "../../../outside/master.wav", "narration master")


def test_same_asset_reused_non_contiguous_in_render(tmp_path: Path) -> None:
    project = _locked_project(tmp_path, asset_ids=("COV_CHAR", "COV_LOC"))
    # Both covenant assets are directly reused, no production generation needed.
    paragraphs = [
        _vp("VP_001", 0.0, 8.0),
        _vp("VP_002", 8.0, 16.0),
        _vp("VP_003", 16.0, 24.0),
    ]
    decisions = [
        _decision("VP_001", "reuse_asset", "COV_CHAR"),
        _decision("VP_002", "reuse_asset", "COV_LOC"),
        _decision("VP_003", "reuse_asset", "COV_CHAR"),
    ]
    _director_and_plan(project, paragraphs=paragraphs, decisions=decisions, plan_assets=[])
    resolve_edit_timeline(project)
    timeline = json.loads((project / EDIT_TIMELINE_REL).read_text(encoding="utf-8"))
    events = timeline["events"]
    assert [item["asset_id"] for item in events] == ["COV_CHAR", "COV_LOC", "COV_CHAR"]
    assert events[0]["edit_id"] != events[2]["edit_id"]
    status = render_delivery_status(project)
    assert status["status"] == "awaiting_static_render"
