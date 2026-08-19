from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest import mock

from book_video_factory.audio_stage.compiler import finalize_audio_stage, generate_audio_stage
from book_video_factory.audio_stage.media_probe import normalize_text
from book_video_factory.audio_stage.providers import NarrationChunkResult
from book_video_factory.audio_stage.status import audio_stage_status
from book_video_factory.manifests import sha256_file
from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    write_minimax_phase4_inputs,
)


class _FakeMiniMaxProvider:
    provider_id = "minimax"

    def __init__(self, *, voice_id: str, **_kwargs) -> None:
        self.voice_id = voice_id
        self.synthesis_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def verify_system_voice(self, voice_id: str):
        assert voice_id == self.voice_id
        return {"voice_id": voice_id, "voice_name": voice_id}

    def synthesize(self, request, *, evidence_dir: Path):
        assert request.provider == "minimax"
        assert request.voice_id == self.voice_id
        self.synthesis_count += 1
        duration = max(1.0, len(request.text) * 0.11)
        wav_path = evidence_dir / f"{request.chunk_id}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        with wave.open(str(wav_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(b"\x00\x00" * int(sample_rate * duration))
        timed_chars = [char for char in request.text if normalize_text(char)]
        timestamps = [
            {
                "start": round(duration * index / len(timed_chars), 3),
                "end": round(duration * (index + 1) / len(timed_chars), 3),
                "text": char,
            }
            for index, char in enumerate(timed_chars)
        ]
        result = NarrationChunkResult(
            chunk_id=request.chunk_id,
            provider="minimax",
            model=request.model,
            voice_id=request.voice_id,
            audio_path=wav_path.name,
            audio_sha256=sha256_file(wav_path),
            duration=round(duration, 3),
            subtitle_timestamps=tuple(timestamps),
            subtitle_granularity="word",
            trace_id=f"fake-trace-{self.synthesis_count:04d}",
            request_digest=request.digest(),
        )
        (evidence_dir / f"{request.chunk_id}.json").write_text(
            json.dumps({
                "audio_path": result.audio_path,
                "audio_sha256": result.audio_sha256,
                "duration": result.duration,
                "subtitle_timestamps": list(result.subtitle_timestamps),
                "subtitle_granularity": result.subtitle_granularity,
                "trace_id": result.trace_id,
                "request_digest": result.request_digest,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result


def test_minimax_generate_finalize_status_and_identical_rerun(tmp_path: Path) -> None:
    project = build_approved_phase3_project(tmp_path)
    input_path, lexicon_path = write_minimax_phase4_inputs(project)

    with mock.patch(
        "book_video_factory.audio_stage.compiler.verify_phase4_prerequisites",
        return_value={"visual_approval_sha256": sha256_file(project / "03_images_生成图片/ANCHOR_APPROVAL.json")},
    ), mock.patch(
        "book_video_factory.audio_stage.compiler.MiniMaxNarrationProvider",
        _FakeMiniMaxProvider,
        create=True,
    ):
        generated = generate_audio_stage(project, input_path, lexicon_path)

    assert generated.status == "created"
    preliminary = json.loads(generated.manifest_path.read_text(encoding="utf-8"))
    assert preliminary["provider"] == "minimax"
    assert preliminary["provider_policy"] == "minimax_required"
    assert preliminary["voice_id"] == "Chinese (Mandarin)_Sincere_Adult"
    assert preliminary["tool_provenance"]["minimax_provider_exercised"] is True
    evidence_path = project / preliminary["evidence_relative"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["timing_source"] == "provider"
    assert all(item["trace_id"].startswith("fake-trace-") for item in evidence["chunks"])
    assert (project / "assets/audio/narration_master.wav").is_file()
    assert (project / "assets/audio/provider.vtt").is_file()
    with mock.patch(
        "book_video_factory.audio_stage.status.visual_stage_next_status",
        return_value="ready_for_narration",
    ):
        assert audio_stage_status(project, "r1") == "awaiting_audio_storyboard_plan"
        evidence_bytes = evidence_path.read_bytes()
        evidence_path.write_bytes(evidence_bytes + b"tamper")
        assert audio_stage_status(project, "r1") == "blocked_by_audio_manifest_integrity"
        evidence_path.write_bytes(evidence_bytes)

    plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
    write_json(plan_path, build_storyboard_audio_plan(project))
    with mock.patch(
        "book_video_factory.audio_stage.compiler.verify_phase4_prerequisites",
        return_value={"visual_approval_sha256": sha256_file(project / "03_images_生成图片/ANCHOR_APPROVAL.json")},
    ), mock.patch(
        "book_video_factory.audio_stage.compiler.prepare_audio_staging_project",
        side_effect=AssertionError("MiniMax finalize must not enter HBG staging"),
    ), mock.patch(
        "book_video_factory.audio_stage.compiler.run_hbg_narration",
        side_effect=AssertionError("MiniMax finalize must not call Edge/HBG"),
    ):
        first = finalize_audio_stage(project, plan_path)
        first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        second = finalize_audio_stage(project, plan_path)
        second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))

    assert first.status == "created"
    assert second.status == "unchanged"
    assert first_manifest["provider"] == "minimax"
    assert first_manifest["final_output_hashes"] == second_manifest["final_output_hashes"]
    with mock.patch(
        "book_video_factory.audio_stage.status.visual_stage_next_status",
        return_value="ready_for_narration",
    ):
        assert audio_stage_status(project, "r1") == "ready_for_image_task_planning"


def test_minimax_missing_credentials_never_falls_back_to_edge(tmp_path: Path) -> None:
    project = build_approved_phase3_project(tmp_path)
    input_path, lexicon_path = write_minimax_phase4_inputs(project)
    with mock.patch(
        "book_video_factory.audio_stage.compiler.verify_phase4_prerequisites",
        return_value={"visual_approval_sha256": sha256_file(project / "03_images_生成图片/ANCHOR_APPROVAL.json")},
    ), mock.patch.dict("os.environ", {}, clear=True), mock.patch(
        "book_video_factory.audio_stage.compiler.run_hbg_narration",
        side_effect=AssertionError("MiniMax failure must not fall back to Edge/HBG"),
    ):
        try:
            generate_audio_stage(project, input_path, lexicon_path)
        except Exception as error:
            assert "MINIMAX_API_KEY" in str(error)
        else:
            raise AssertionError("missing MiniMax credentials must fail closed")
