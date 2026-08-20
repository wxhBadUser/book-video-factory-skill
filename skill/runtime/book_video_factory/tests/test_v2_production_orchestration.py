"""V2 Host ImageGen + Multimodal Judge autonomous loop tests (Stage 5, offline)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from PIL import Image

from book_video_factory.director_stage.contracts import resolve_edit_timeline
from book_video_factory.host_orchestration import read_ledgers, register_host_event
from book_video_factory.manifests import sha256_file
from book_video_factory.production_orchestration import (
    ProductionOrchestrationError,
    apply_verdicts,
    ensure_generate_actions,
    ensure_judge_actions,
    incorporate_generated_assets,
    production_status,
)
from book_video_factory.visual_covenant import (
    covenant_canonical_sha,
    promote_covenant_assets,
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
    script_text = "locked narration for production orchestration"
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
    image_sha = _make_png(project, "03_images_生成图片/covenant/jane.png")
    covenant_payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": name,
        "locked_script_sha256": _sha(script_text),
        "assets": [{
            "asset_id": "COV_JANE",
            "asset_family": "character:jane",
            "visual_function": "character_narration",
            "source": "visual_covenant",
            "production_eligible": True,
            "technical_status": "verified",
            "path": "03_images_生成图片/covenant/jane.png",
            "file_sha256": image_sha,
            "provenance": {"provider": "host-imagegen", "tool_call_id": "call-cov-jane"},
        }],
    }
    covenant_payload["visual_covenant_sha256"] = covenant_canonical_sha(covenant_payload)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant_payload)
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


def _director_and_plan(
    project: Path,
    *,
    duration: float = 24.0,
    paragraphs: list,
    decisions: list,
    plan_assets: list,
) -> None:
    _write_json(project / "04_audio/AUDIO_TIMELINE.v2.json", {
        "schema_version": "audio-timeline.v2",
        "release_id": "release-1",
        "project_id": project.name,
        "narration_master_path": "04_audio/master.wav",
        "narration_master_sha256": _sha("master"),
        "provider_vtt_path": "04_audio/provider.vtt",
        "provider_vtt_sha256": _sha("vtt"),
        "caption_timeline_path": "04_audio/CAPTION_TIMELINE.json",
        "caption_timeline_sha256": _sha("captions"),
        "timing_authority": "provider",
        "narration_duration_seconds": duration,
        "bgm": {"bgm_mode": "none"},
    })
    audio_sha = sha256_file(project / "04_audio/AUDIO_TIMELINE.v2.json")
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
    """Register the active Host generate action's succeeded event with a real image."""
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
    """Register the judge action, write a real verdict file, and apply it."""
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


def _catalog(project: Path) -> dict:
    path = project / "manifests/asset_catalog/ASSET_CATALOG.v2.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_asset(project: Path, asset_id: str) -> dict:
    for item in _catalog(project)["assets"]:
        if item["asset_id"] == asset_id:
            return item
    raise AssertionError(f"asset {asset_id} not in catalog")


def _single_asset_setup(tmp_path: Path, family: str, func: str) -> Path:
    project = _locked_project(tmp_path)
    _director_and_plan(
        project,
        paragraphs=[_vp("VP_001", 0.0, 24.0)],
        decisions=[_decision("VP_001", "generate_new", "PROD_001")],
        plan_assets=[_plan_asset("PROD_001", family=family, func=func, bound_paragraph_id="VP_001")],
    )
    return project


def test_stale_host_output_fails_closed(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "location:thrushcross", "location_establishing")
    ensure_generate_actions(project)
    action = _active_action(project, "generate_image", "PROD_001")
    image_relative = "03_images_生成图片/production/PROD_001_a1.png"
    image_sha = _make_png(project, image_relative)
    event = {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "generate_image",
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen",
        "tool_call_id": "call-gen-stale",
        "output_path": image_relative,
        "output_sha256": image_sha,
    }
    register_host_event(project, event)
    (project / image_relative).write_bytes(b"tampered-production-asset")
    with pytest.raises(ProductionOrchestrationError) as excinfo:
        incorporate_generated_assets(project)
    assert "hash is stale" in str(excinfo.value)


def test_runtime_does_not_judge_outputs_judge_action(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "character:jane", "character_narration")
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    status = production_status(project)
    assert status["status"] == "host_action_pending"
    assert status["host_action"]["action_type"] == "judge_visual_asset"
    assert status["host_action"]["expected_output"]["asset_id"] == "PROD_001"


def test_judge_pass_promotes_to_approved(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "character:jane", "character_narration")
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "pass",
            "reason": "人物旁白与场景构图成立，符合旁白段落的文学氛围；画面中无需出现额外人物。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "approved_production_asset"
    assert entry["judge"]["verdict"] == "pass"
    assert entry["warnings"] == []
    assert production_status(project)["status"] == "asset_catalog_ready"


def test_judge_warning_is_approved_with_warnings(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "character:jane", "character_narration")
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "warning",
            "warnings": ["minor facial drift"],
            "reason": "次要人物形象轻微漂移，不影响生产使用。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "approved_production_asset"
    assert entry["judge"]["verdict"] == "warning"
    assert entry["warnings"] == ["minor facial drift"]


def test_hard_failure_no_fallback_blocks(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "location:thrushcross", "location_establishing")
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "hard_failure",
            "hard_failures": ["period_or_place_error"],
            "reason": "画面出现明显现代污染，时代地点错误。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "blocked_quality_failure"
    assert entry["hard_failures"] == ["period_or_place_error"]
    status = production_status(project)
    assert status["status"] == "blocked_quality_failure"
    assert status["blocked_asset_ids"] == ["PROD_001"]


def test_retry_requires_changed_strategy_max_three_attempts(tmp_path: Path) -> None:
    project = _single_asset_setup(tmp_path, "location:thrushcross", "location_establishing")
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "retryable_failure",
            "hard_failures": ["anachronistic_modern_infection"],
            "retry_strategy": {"prompt_strategy": "remove_modern_infection"},
            "reason": "第一次出现现代污染。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "pending_generation"
    assert entry["generation_attempt"] == 1
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "retryable_failure",
            "hard_failures": ["anachronistic_modern_infection"],
            "retry_strategy": {"prompt_strategy": "strengthen_period_markers"},
            "reason": "第二次仍轻微现代污染，改用强化时代标识。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "pending_generation"
    assert entry["generation_attempt"] == 2
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "hard_failure",
            "hard_failures": ["garbled_text_watermark_or_ui"],
            "reason": "第三次出现乱码水印。",
        },
    )
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "blocked_quality_failure"
    actions, _ = _ledgers(project)
    gen_keys = [a["idempotency_key"] for a in actions if a["action_type"] == "generate_image"]
    assert not any("attempt-4" in key for key in gen_keys)
    assert production_status(project)["status"] == "blocked_quality_failure"


def test_failed_sibling_does_not_affect_approved_sibling(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _director_and_plan(
        project,
        paragraphs=[_vp("VP_001", 0.0, 12.0), _vp("VP_002", 12.0, 24.0)],
        decisions=[
            _decision("VP_001", "generate_new", "PROD_001"),
            _decision("VP_002", "generate_new", "PROD_002"),
        ],
        plan_assets=[
            _plan_asset("PROD_001", family="character:jane", func="character_narration", bound_paragraph_id="VP_001"),
            _plan_asset("PROD_002", family="location:thrushcross", func="location_establishing", bound_paragraph_id="VP_002"),
        ],
    )
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_generate(project, "PROD_002")
    _simulate_judge(project, "PROD_001", {"verdict": "pass", "reason": "通过。"})
    _simulate_judge(
        project,
        "PROD_002",
        {
            "verdict": "hard_failure",
            "hard_failures": ["visually_unusable"],
            "reason": "画面不可用。",
        },
    )
    assert _catalog_asset(project, "PROD_001")["status"] == "approved_production_asset"
    assert _catalog_asset(project, "PROD_002")["status"] == "blocked_quality_failure"


def test_fallback_rewrites_decisions_and_resolves_timeline(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    _director_and_plan(
        project,
        paragraphs=[_vp("VP_001", 0.0, 12.0), _vp("VP_002", 12.0, 24.0)],
        decisions=[
            _decision("VP_001", "generate_new", "PROD_001"),
            _decision("VP_002", "generate_new", "PROD_002"),
        ],
        plan_assets=[
            _plan_asset("PROD_001", family="character:jane", func="character_narration", bound_paragraph_id="VP_001"),
            _plan_asset("PROD_002", family="location:thrushcross", func="location_establishing", bound_paragraph_id="VP_002"),
        ],
    )
    ensure_generate_actions(project)
    _simulate_generate(project, "PROD_001")
    _simulate_generate(project, "PROD_002")
    _simulate_judge(
        project,
        "PROD_001",
        {
            "verdict": "hard_failure",
            "hard_failures": ["severe_style_drift"],
            "reason": "风格严重漂移。",
        },
    )
    _simulate_judge(project, "PROD_002", {"verdict": "pass", "reason": "场景图通过。"})
    entry = _catalog_asset(project, "PROD_001")
    assert entry["status"] == "blocked_quality_failure"
    assert entry["fallback_asset_id"] == "COV_JANE"
    decisions = json.loads((project / "04_director/EDIT_DECISIONS.v2.json").read_text(encoding="utf-8"))
    vp001 = next(item for item in decisions["decisions"] if item["paragraph_id"] == "VP_001")
    assert vp001["decision"] == "reuse_asset"
    assert vp001["asset_id"] == "COV_JANE"
    assert production_status(project)["status"] == "asset_catalog_ready"
    result = resolve_edit_timeline(project)
    assert result["status"] == "edit_timeline_resolved"
    timeline = json.loads((project / "04_director/EDIT_TIMELINE.v2.json").read_text(encoding="utf-8"))
    assert [item["asset_id"] for item in timeline["events"]] == ["COV_JANE", "PROD_002"]
