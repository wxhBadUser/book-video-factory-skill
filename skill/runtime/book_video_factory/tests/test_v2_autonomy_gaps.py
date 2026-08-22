from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest
from PIL import Image

from book_video_factory.host_orchestration import (
    derive_next_action,
    read_ledgers,
    register_action,
    register_host_event,
)
from book_video_factory.audio_stage.autonomy import generate_audio_timeline_v2
from book_video_factory.audio_stage.providers.base import (
    NarrationChunkRequest,
    NarrationChunkResult,
)
from book_video_factory.literary_director import (
    materialize_literary_director_result,
    register_literary_director_action,
)
from book_video_factory.pipeline_runtime import pipeline_status
from book_video_factory.director_stage.contracts import resolve_edit_timeline
from book_video_factory.manifests import sha256_file
from book_video_factory.v2_render import render_delivery_status, render_static
from book_video_factory.visual_covenant import (
    VisualCovenantError,
    covenant_canonical_sha,
    promote_covenant_assets,
    record_visual_covenant_approval,
    world_profile_canonical_sha,
)


def _sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _locked_project(tmp_path: Path) -> Path:
    project = tmp_path / "warehouse" / "projects" / "pilot"
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
    script = "locked narration"
    script_path = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    image = project / "03_images_生成图片/covenant/anchor.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (120, 140, 160)).save(image, format="PNG")
    asset = {
        "asset_id": "COV_ANCHOR",
        "asset_family": "character:jane",
        "visual_function": "character_portrait",
        "source": "visual_covenant",
        "production_eligible": True,
        "technical_status": "verified",
        "path": "03_images_生成图片/covenant/anchor.png",
        "file_sha256": sha256_file(image),
        "provenance": {"provider": "host-imagegen", "tool_call_id": "call-anchor"},
    }
    covenant = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "locked_script_sha256": _sha(script),
        "world_profile": {"period": "nineteenth-century Yorkshire", "palette": "muted earth tones"},
        "assets": [asset],
    }
    covenant["world_profile_sha256"] = world_profile_canonical_sha(covenant["world_profile"])
    covenant["visual_covenant_sha256"] = covenant_canonical_sha(covenant)
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant)
    return project


def _generate_action() -> dict:
    return {
        "schema_version": "host-agent-action.v2",
        "action_id": "ACT_0001",
        "release_id": "release-1",
        "project_id": "pilot",
        "action_type": "generate_image",
        "idempotency_key": "release-1:TASK_001:attempt-1",
        "attempt": 1,
        "inputs": [{"kind": "generation_request", "sha256": _sha("original") }],
        "expected_output": {"asset_id": "SCENE_001", "asset_sha256": _sha("asset")},
        "max_runtime_seconds": 900,
    }


def test_visual_covenant_promotion_requires_independent_approval(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    with pytest.raises(VisualCovenantError, match="approval"):
        promote_covenant_assets(project)

    approval = record_visual_covenant_approval(
        project,
        reviewer="human-reviewer",
        approved_at="2026-08-22T00:00:00+08:00",
    )
    result = promote_covenant_assets(project)
    assert result["covenant_approval_sha256"] == approval["visual_covenant_approval_sha256"]
    assert result["covenant_approval_sha256"] != approval["visual_covenant_sha256"]


def test_pipeline_exposes_exactly_one_visual_covenant_human_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _locked_project(tmp_path)
    monkeypatch.setattr("book_video_factory.pipeline_runtime.repository_integrity_block", lambda: None)
    status = pipeline_status(project)
    assert status["status"] == "awaiting_visual_covenant_approval"
    assert status["human_review_required"] is True


def test_retry_is_persisted_and_integrity_failures_do_not_retry(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    action = _generate_action()
    register_action(project, action)
    register_host_event(project, {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "generate_image",
        "status": "failed",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen",
        "tool_call_id": "call-fail",
        "failure_class": "provider_transient",
        "retryable": True,
        "has_registered_output": False,
    })
    retry = derive_next_action(project)
    assert retry is not None
    assert retry["attempt"] == 2
    assert retry["retry_of"] == action["action_id"]
    assert retry["previous_failure"]["classification"] == "provider_transient"
    assert any(item["idempotency_key"] == retry["idempotency_key"] for item in read_ledgers(project)["actions"])
    assert derive_next_action(project)["action_id"] == retry["action_id"]


def test_quality_retry_changes_inputs_and_integrity_retry_fails_closed(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    action = _generate_action()
    register_action(project, action)
    register_host_event(project, {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": "generate_image",
        "status": "failed",
        "idempotency_key": action["idempotency_key"],
        "provider": "host-imagegen",
        "tool_call_id": "call-quality-fail",
        "failure_class": "generation_quality_failure",
        "retry_strategy": {"prompt_strategy": "remove_modern_infection"},
        "retryable": True,
        "has_registered_output": True,
    })
    retry = derive_next_action(project)
    assert retry is not None
    assert retry["retry_classification"] == "generation_quality_failure"
    assert len(retry["inputs"]) == len(action["inputs"]) + 1

    project2 = _locked_project(tmp_path / "integrity")
    action2 = _generate_action()
    register_action(project2, action2)
    register_host_event(project2, {
        "schema_version": "host-agent-event.v2",
        "action_id": action2["action_id"],
        "event_type": "generate_image",
        "status": "failed",
        "idempotency_key": action2["idempotency_key"],
        "provider": "host-imagegen",
        "tool_call_id": "call-integrity-fail",
        "failure_class": "contract_integrity_failure",
        "retryable": True,
        "has_registered_output": False,
    })
    assert derive_next_action(project2) is None


def test_real_wav_fixture_is_decodable() -> None:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    buffer.seek(0)
    with wave.open(buffer, "rb") as handle:
        assert handle.getnframes() == 800


class _OfflineProvider:
    provider_id = "offline-test"
    policy = "minimax_required"

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
            model=request.model,
            voice_id=request.voice_id,
            audio_path=audio_path.name,
            audio_sha256=_sha(audio_path.read_bytes()),
            duration=0.1,
            subtitle_timestamps=({"index": 0, "start": 0.0, "end": 0.1, "text": request.text},),
            subtitle_granularity="sentence",
            trace_id="offline-trace",
            request_digest=request.digest(),
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_runtime_narration_emits_real_audio_evidence_and_timeline(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    result = generate_audio_timeline_v2(
        project,
        _OfflineProvider(),
        [NarrationChunkRequest(
            chunk_id="CHUNK_001",
            text="locked narration",
            provider="offline-test",
            model="offline",
            voice_id="voice-1",
        )],
    )
    assert result["status"] == "audio_timeline_ready"
    timeline = json.loads((project / "04_audio/AUDIO_TIMELINE.v2.json").read_text(encoding="utf-8"))
    assert timeline["timing_authority"] == "provider"
    with wave.open(str(project / timeline["narration_master_path"]), "rb") as handle:
        assert handle.getnframes() > 0
    evidence = project / "04_audio/AUDIO_GENERATION_EVIDENCE.v1.json"
    assert evidence.is_file()


def test_runtime_materializes_director_result_into_three_bound_artifacts(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    approval = record_visual_covenant_approval(
        project,
        reviewer="human-reviewer",
        approved_at="2026-08-22T00:00:00+08:00",
    )
    promote_covenant_assets(project)
    audio = project / "04_audio/AUDIO_TIMELINE.v2.json"
    audio.parent.mkdir(parents=True, exist_ok=True)
    _write_json(audio, {
        "schema_version": "audio-timeline.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "narration_duration_seconds": 1.0,
        "narration_master_path": "04_audio/master.wav",
        "narration_master_sha256": _sha("master"),
        "provider_vtt_path": "04_audio/PROVIDER.vtt",
        "provider_vtt_sha256": _sha("vtt"),
        "caption_timeline_path": "04_audio/CAPTION_TIMELINE.json",
        "caption_timeline_sha256": _sha("captions"),
        "timing_authority": "provider",
        "bgm": {"bgm_mode": "none"},
    })
    (audio.parent / "CAPTION_TIMELINE.json").write_text("{\"captions\": []}\n", encoding="utf-8")
    policy = project / "config/DIRECTOR_POLICY.json"
    _write_json(policy, {"policy": "test-policy"})
    catalog_sha = _sha((project / "manifests/asset_catalog/ASSET_CATALOG.v2.json").read_bytes())
    result = {
        "schema_version": "literary-director-result.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "locked_script_sha256": _sha("locked narration"),
        "audio_timeline_sha256": _sha(audio.read_bytes()),
        "caption_timeline_sha256": _sha((audio.parent / "CAPTION_TIMELINE.json").read_bytes()),
        "visual_covenant_approval_sha256": approval["visual_covenant_approval_sha256"],
        "asset_catalog_sha256": catalog_sha,
        "director_policy_sha256": _sha(policy.read_bytes()),
        "paragraphs": [{
            "paragraph_id": "VP_001",
            "start": 0.0,
            "end": 1.0,
            "source_caption_ids": ["C1"],
            "narrative_focus": "孤独",
            "visual_function": "character_narration",
            "visual_center": "Jane",
            "mood": "steadfast",
            "rationale": "保持人物中心",
            "decision": "generate_new",
            "asset_family": "character:jane",
            "minimal_generation_intent": "portrait in a dim room",
            "recommended_reference_asset_ids": ["COV_ANCHOR"],
        }],
    }
    result_path = project / "manifests/host_orchestration/DIRECTOR_RESULT.json"
    _write_json(result_path, result)
    action = register_literary_director_action(project, policy_path=policy)
    assert action["action_type"] == "plan_literary_director"
    materialized = materialize_literary_director_result(project, result_path)
    assert materialized["status"] == "literary_director_materialized"
    paragraphs = json.loads((project / "04_director/VISUAL_PARAGRAPHS.json").read_text(encoding="utf-8"))
    assert paragraphs["schema_version"] == "visual-paragraphs.v2"
    assert paragraphs["paragraphs"][0]["narrative_focus"] == "孤独"
    plan = json.loads((project / "04_director/PRODUCTION_IMAGE_PLAN.v2.json").read_text(encoding="utf-8"))
    assert plan["assets"][0]["asset_id"] == "PROD_VP_001"


def test_static_render_uses_real_ffmpeg_and_probe_derived_qa(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    record_visual_covenant_approval(project, reviewer="human-reviewer", approved_at="2026-08-22T00:00:00+08:00")
    promote_covenant_assets(project)
    audio_dir = project / "04_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    master = audio_dir / "master.wav"
    with wave.open(str(master), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    vtt = audio_dir / "PROVIDER.vtt"
    vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.100\nlocked narration\n", encoding="utf-8")
    captions = audio_dir / "CAPTION_TIMELINE.json"
    _write_json(captions, {"captions": [{"start": 0.0, "end": 0.1, "text": "locked narration"}]})
    _write_json(audio_dir / "AUDIO_TIMELINE.v2.json", {
        "schema_version": "audio-timeline.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "narration_master_path": "04_audio/master.wav",
        "narration_master_sha256": sha256_file(master),
        "provider_vtt_path": "04_audio/PROVIDER.vtt",
        "provider_vtt_sha256": sha256_file(vtt),
        "caption_timeline_path": "04_audio/CAPTION_TIMELINE.json",
        "caption_timeline_sha256": sha256_file(captions),
        "timing_authority": "provider",
        "narration_duration_seconds": 0.1,
        "bgm": {"bgm_mode": "none"},
    })
    audio_sha = sha256_file(audio_dir / "AUDIO_TIMELINE.v2.json")
    _write_json(project / "04_director/VISUAL_PARAGRAPHS.json", {
        "schema_version": "visual-paragraphs.v1",
        "release_id": "release-1",
        "project_id": "pilot",
        "audio_timeline_sha256": audio_sha,
        "narration_duration_seconds": 0.1,
        "paragraphs": [{"paragraph_id": "VP_001", "start": 0.0, "end": 0.1, "source_caption_ids": ["C1"], "visual_intent": "hold", "mood": "calm"}],
    })
    _write_json(project / "04_director/EDIT_DECISIONS.v2.json", {
        "schema_version": "edit-decisions.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "visual_paragraphs_sha256": sha256_file(project / "04_director/VISUAL_PARAGRAPHS.json"),
        "decisions": [{"paragraph_id": "VP_001", "decision": "reuse_asset", "asset_id": "COV_ANCHOR"}],
    })
    resolve_edit_timeline(project)
    rendered = render_static(project)
    assert rendered["status"] == "rendered"
    assert (project / rendered["video_path"]).stat().st_size > 0
    assert render_delivery_status(project)["status"] == "delivered"
