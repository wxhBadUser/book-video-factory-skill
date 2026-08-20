"""V2 audio timeline + autonomous BGM tests (Stage 3, offline)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

from book_video_factory.audio_stage.audio_timeline import (
    AudioTimelineError,
    MIX_MANIFEST_REL,
    TIMELINE_REL,
    build_audio_timeline,
    select_bgm,
)
from book_video_factory.audio_stage.provider_timeline import (
    build_audio_generation_evidence,
    concat_audio_master,
    write_provider_vtt,
)
from book_video_factory.audio_stage.providers import NarrationChunkResult
from book_video_factory.manifests import sha256_file


def _wav_file(path: Path, duration: float = 0.25, rate: int = 48000) -> str:
    frames = int(rate * duration)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return sha256_file(path)


def _chunk(chunk_id: str, evidence_dir: Path, *, start: float, end: float, text: str, duration: float = 0.25) -> NarrationChunkResult:
    audio_path = evidence_dir / f"{chunk_id}.wav"
    sha = _wav_file(audio_path, duration=duration)
    return NarrationChunkResult(
        chunk_id=chunk_id,
        provider="minimax",
        model="speech-2.8-hd",
        voice_id="voice_test_0001",
        audio_path=audio_path.relative_to(evidence_dir).as_posix(),
        audio_sha256=sha,
        duration=duration,
        subtitle_timestamps=({"index": 0, "start": start, "end": end, "text": text},),
        trace_id=f"trace-{chunk_id}",
        request_digest="0" * 64,
    )


def _write_provider_audio(project: Path) -> tuple[str, dict]:
    """Write real chunk WAVs, master, provider VTT, and generation evidence."""
    audio_dir = project / "04_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    chunks = [
        _chunk("c1", audio_dir, start=0.0, end=0.20, text="第一段。", duration=0.25),
        _chunk("c2", audio_dir, start=0.0, end=0.18, text="第二段。", duration=0.30),
    ]
    vtt_path, _, _ = write_provider_vtt(chunks, audio_dir / "provider.vtt")
    master = concat_audio_master(chunks, audio_dir)
    evidence = build_audio_generation_evidence(
        chunk_results=chunks,
        master=master,
        vtt_path=vtt_path,
        vtt_sha256=sha256_file(vtt_path),
        release_id="release-1",
        variant="B",
        voice_id="voice_test_0001",
        model="speech-2.8-hd",
    )
    # The evidence document must reference project-relative paths so the
    # runtime can re-resolve them against the project root.
    evidence["provider_vtt"]["path"] = "04_audio/provider.vtt"
    evidence["master"]["path"] = "04_audio/" + master["path"]
    evidence_path = audio_dir / "AUDIO_GENERATION_EVIDENCE.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence_path.relative_to(project).as_posix(), evidence


def _caption_timeline(project: Path, text: str = "第一段。") -> str:
    path = project / "04_audio/CAPTION_TIMELINE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": text}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path.relative_to(project).as_posix()


def _bgm_track(project: Path, *, relative: str = "assets/audio/bgm/licensed.mp3") -> str:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"licensed-bgm-audio-bytes")
    return relative


def _candidates(project: Path, *tracks: dict, release_id: str = "release-1") -> None:
    path = project / "04_audio/BGM_CANDIDATES.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": "bgm-candidates.v1", "release_id": release_id, "tracks": list(tracks)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _cleared_track(relative: str, score: float, *, source_sha256: str | None = None) -> dict:
    return {
        "track_id": f"track-{score}",
        "path": relative,
        "rights_status": "cleared",
        "selection_score": score,
        "selection_reason": "北 Atlantic 文学氛围",
        "loop_policy": "crossfade_loop",
        "ducking_db": -13.0,
        "target_lufs": -24.0,
        "source_sha256": source_sha256,
        "license": {"provider": "user-declared", "grant": "royalty-free"},
    }


def _probe(_path: Path, _seconds: float | None) -> dict[str, float]:
    return {"duration_seconds": 5.0, "integrated_lufs": -15.0, "true_peak_dbtp": -4.0}


class AudioTimelineV2Tests(unittest.TestCase):
    def test_provider_is_only_timing_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            evidence_rel, evidence = _write_provider_audio(project)
            caption_rel = _caption_timeline(project)
            timeline = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
            )
            self.assertEqual(timeline["status"], "audio_timeline_ready")
            self.assertEqual(timeline["timing_authority"], "provider")

            # A legacy edge_vtt timing source is rejected.
            broken = dict(evidence)
            broken["timing_source"] = "edge_vtt"
            broken_path = project / "04_audio/EDGE_EVIDENCE.json"
            broken_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(AudioTimelineError, "edge|provider|timing"):
                build_audio_timeline(
                    project, evidence_path=broken_path.relative_to(project).as_posix(),
                    caption_timeline_path=caption_rel, probe_runner=_probe,
                )

            # A stale master hash is rejected (file changed, evidence unchanged).
            master = project / evidence["master"]["path"]
            master.write_bytes(master.read_bytes() + b"tamper")
            with self.assertRaisesRegex(AudioTimelineError, "master|sha256|stale"):
                build_audio_timeline(
                    project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
                )

            # A missing provider VTT blocks.
            master.write_bytes(b"")
            with self.assertRaisesRegex(AudioTimelineError, "master|missing"):
                build_audio_timeline(
                    project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
                )

    def test_auto_selects_highest_scored_cleared_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            evidence_rel, _ = _write_provider_audio(project)
            caption_rel = _caption_timeline(project)
            low = _bgm_track(project, relative="assets/audio/bgm/low.mp3")
            high = _bgm_track(project, relative="assets/audio/bgm/high.mp3")
            _bgm_track(project, relative="assets/audio/bgm/other.mp3")
            _candidates(
                project,
                _cleared_track(low, 3.0),
                _cleared_track(high, 9.0),
            )
            selected = select_bgm(project)
            self.assertEqual(selected["bgm_mode"], "library")
            self.assertEqual(selected["source_path"], high)
            self.assertEqual(selected["source_sha256"], sha256_file(project / high))
            timeline = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
            )
            self.assertEqual(timeline["bgm_mode"], "library")
            manifest = json.loads((project / MIX_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(manifest["bgm"]["source_path"], high)
            self.assertEqual(manifest["mix_qa"]["status"], "ready")

    def test_no_tracks_auto_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            evidence_rel, _ = _write_provider_audio(project)
            caption_rel = _caption_timeline(project)
            self.assertEqual(select_bgm(project)["bgm_mode"], "none")
            timeline = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
            )
            self.assertEqual(timeline["bgm_mode"], "none")
            self.assertEqual(timeline["mix_qa_status"], "not_applicable")
            manifest = json.loads((project / MIX_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(manifest["mix_qa"]["status"], "not_applicable")

    def test_hash_mismatch_blocks_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            track = _bgm_track(project)
            recorded = hashlib.sha256(b"different-recorded-bytes").hexdigest()
            _candidates(project, _cleared_track(track, 5.0, source_sha256=recorded))
            with self.assertRaisesRegex(AudioTimelineError, "hash|corrupt|integrity"):
                select_bgm(project)
            # An invalid (non-SHA) recorded hash is also corrupt rights evidence.
            broken = _cleared_track(track, 5.0, source_sha256="not-a-sha")
            _candidates(project, broken)
            with self.assertRaisesRegex(AudioTimelineError, "source_sha256|hash"):
                select_bgm(project)

    def test_clipping_and_loudness_qa_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            evidence_rel, _ = _write_provider_audio(project)
            caption_rel = _caption_timeline(project)
            track = _bgm_track(project)
            _candidates(project, _cleared_track(track, 5.0))
            timeline = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=_probe
            )
            recorded = json.loads((project / MIX_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(recorded["mix_qa"]["source_probe"]["integrated_lufs"], -15.0)
            self.assertEqual(recorded["mix_qa"]["source_probe"]["true_peak_dbtp"], -4.0)
            self.assertIn("ducking_db", recorded["mix_qa"])
            self.assertIn("loop_policy", recorded["mix_qa"])
            self.assertEqual(timeline["mix_qa_status"], "ready")

            def hot(_path: Path, _seconds: float | None) -> dict[str, float]:
                return {"duration_seconds": 5.0, "integrated_lufs": -15.0, "true_peak_dbtp": -2.9}

            with self.assertRaisesRegex(AudioTimelineError, "peak|clipping|3"):
                build_audio_timeline(
                    project, evidence_path=evidence_rel, caption_timeline_path=caption_rel, probe_runner=hot
                )

    def test_caption_wrapping_does_not_change_bgm_or_visual_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "warehouse" / "projects" / "pilot"
            evidence_rel, _ = _write_provider_audio(project)
            track = _bgm_track(project)
            _candidates(project, _cleared_track(track, 5.0))
            first_rel = _caption_timeline(project, text="第一段。")
            first = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=first_rel, probe_runner=_probe
            )
            first_payload = json.loads((project / TIMELINE_REL).read_text(encoding="utf-8"))
            first_bgm = first_payload["bgm"]
            first_timing = {
                "narration_master_path": first_payload["narration_master_path"],
                "provider_vtt_path": first_payload["provider_vtt_path"],
                "timing_authority": first_payload["timing_authority"],
            }

            # Rewrite the caption text (simulating line wrapping). Provider
            # timing and BGM selection must be unchanged; only the caption hash
            # is allowed to change.
            wrapped_rel = _caption_timeline(project, text="第一段。" + "字" * 20)
            second = build_audio_timeline(
                project, evidence_path=evidence_rel, caption_timeline_path=wrapped_rel, probe_runner=_probe
            )
            second_payload = json.loads((project / TIMELINE_REL).read_text(encoding="utf-8"))
            self.assertEqual(second_payload["bgm"], first_bgm)
            self.assertEqual(
                {
                    "narration_master_path": second_payload["narration_master_path"],
                    "provider_vtt_path": second_payload["provider_vtt_path"],
                    "timing_authority": second_payload["timing_authority"],
                },
                first_timing,
            )
            self.assertNotEqual(
                second_payload["caption_timeline_sha256"],
                first_payload["caption_timeline_sha256"],
            )
            self.assertEqual(first["bgm_mode"], "library")

    def test_schemas_are_closed_and_versioned(self) -> None:
        schemas = Path(__file__).resolve().parents[1] / "schemas"
        expected = {
            "audio_timeline.v2.schema.json": "audio-timeline.v2",
            "bgm_candidates.v1.schema.json": "bgm-candidates.v1",
            "audio_mix_manifest.v1.schema.json": "audio-mix-manifest.v1",
        }
        for filename, schema_version in expected.items():
            payload = json.loads((schemas / filename).read_text(encoding="utf-8"))
            self.assertFalse(payload["additionalProperties"])
            self.assertIn("schema_version", payload["properties"])
            self.assertEqual(payload["properties"]["schema_version"]["const"], schema_version)


if __name__ == "__main__":
    unittest.main()
