"""M4A provider timeline + evidence tests (A9/A10/A12, offline)."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import wave
from pathlib import Path

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_audio_generation_evidence,
)
from book_video_factory.audio_stage.media_probe import parse_vtt
from book_video_factory.audio_stage.provider_timeline import (
    evidence_digest,
    ProviderTimelineError,
    TIMING_SOURCE_EDGE_LEGACY,
    TIMING_SOURCE_PROVIDER,
    assert_provider_timeline_authority,
    build_audio_generation_evidence,
    concat_audio_master,
    offset_chunk_timestamps,
    restore_display_captions_from_provider,
    write_provider_vtt,
)
from book_video_factory.audio_stage.providers import NarrationChunkResult


def _wav_file(path: Path, duration: float = 0.25, rate: int = 48000) -> str:
    frames = int(rate * duration)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunk(chunk_id: str, evidence: Path, *, start: float, end: float, text: str, duration: float = 0.25) -> NarrationChunkResult:
    audio_path = evidence / f"{chunk_id}.wav"
    sha = _wav_file(audio_path, duration=duration)
    return NarrationChunkResult(
        chunk_id=chunk_id,
        provider="minimax",
        model="speech-2.8-hd",
        voice_id="voice_test_0001",
        audio_path=audio_path.relative_to(evidence).as_posix(),
        audio_sha256=sha,
        duration=duration,
        subtitle_timestamps=({"index": 0, "start": start, "end": end, "text": text},),
        trace_id=f"trace-{chunk_id}",
        request_digest="0" * 64,
    )


class ProviderTimelineTests(unittest.TestCase):
    def test_A10_offsets_are_monotonic_and_master_vtt_parses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            chunks = [
                _chunk("c1", evidence, start=0.0, end=0.20, text="第一段。", duration=0.25),
                _chunk("c2", evidence, start=0.0, end=0.18, text="第二段。", duration=0.30),
                _chunk("c3", evidence, start=0.0, end=0.22, text="第三段。", duration=0.28),
            ]
            vtt_path, cues, total = write_provider_vtt(chunks, evidence / "provider.vtt")
            # c1 occupies 0-0.25s, c2 starts at 0.25, c3 at 0.55; the VTT total
            # is the end of the last real cue (0.77), while the audio master
            # duration is the sum of chunk durations (0.83).
            self.assertEqual(cues[0].start, 0.0)
            self.assertAlmostEqual(cues[1].start, 0.25, places=3)
            self.assertAlmostEqual(cues[2].start, 0.55, places=3)
            self.assertAlmostEqual(total, 0.77, places=2)
            # Parsing back round-trips (real provider timing is authoritative).
            parsed = parse_vtt(vtt_path)
            self.assertEqual(len(parsed), 3)
            self.assertTrue(all(b.start >= a.end for a, b in zip(parsed, parsed[1:])))

    def test_A10_timestamps_are_not_text_estimates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            chunks = [_chunk("c1", evidence, start=0.0, end=0.2, text="一段旁白。", duration=0.25)]
            _cues, offsets = offset_chunk_timestamps(chunks)
            self.assertEqual(offsets, [("c1", 0.0)])

    def test_A12_evidence_binds_all_hashes_and_validation_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            chunks = [
                _chunk("c1", evidence, start=0.0, end=0.20, text="第一段。", duration=0.25),
                _chunk("c2", evidence, start=0.0, end=0.18, text="第二段。", duration=0.30),
            ]
            vtt_path, _, _ = write_provider_vtt(chunks, evidence / "provider.vtt")
            master = concat_audio_master(chunks, evidence)
            vtt_sha = hashlib.sha256(vtt_path.read_bytes()).hexdigest()
            doc = build_audio_generation_evidence(
                chunk_results=chunks,
                master=master,
                vtt_path=vtt_path,
                vtt_sha256=vtt_sha,
                release_id="r3",
                variant="B",
                voice_id="voice_test_0001",
                model="speech-2.8-hd",
            )
            normalized = validate_audio_generation_evidence(doc)
            self.assertEqual(normalized["timing_source"], "provider")
            # Master duration equals the concatenated chunk durations.
            self.assertAlmostEqual(master["duration"], 0.55, places=2)
            # Every chunk SHA is in the evidence; master SHA matches the file.
            self.assertEqual(len(normalized["chunks"]), 2)
            self.assertEqual(doc["chunks"][0]["audio_sha256"], chunks[0].audio_sha256)
            self.assertTrue((evidence / master["path"]).is_file())
            # Master file SHA matches the recorded digest.
            self.assertEqual(
                hashlib.sha256((evidence / master["path"]).read_bytes()).hexdigest(),
                master["sha256"],
            )

    def test_concat_writes_master_to_output_when_chunks_are_in_a_separate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks_dir = root / "chunks"
            output_dir = root / "assembled"
            chunks_dir.mkdir()
            output_dir.mkdir()
            chunks = [
                _chunk("c1", chunks_dir, start=0.0, end=0.20, text="第一段。", duration=0.25),
                _chunk("c2", chunks_dir, start=0.0, end=0.18, text="第二段。", duration=0.30),
            ]

            master = concat_audio_master(chunks, output_dir, audio_dir=chunks_dir)

            self.assertTrue((output_dir / master["path"]).is_file())
            self.assertAlmostEqual(master["duration"], 0.55, places=2)

    def test_A12_tampered_evidence_is_rejected_and_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            chunks = [_chunk("c1", evidence, start=0.0, end=0.2, text="第一段。", duration=0.25)]
            vtt_path, _, _ = write_provider_vtt(chunks, evidence / "provider.vtt")
            master = concat_audio_master(chunks, evidence)
            doc = build_audio_generation_evidence(
                chunk_results=chunks, master=master, vtt_path=vtt_path,
                vtt_sha256=hashlib.sha256(vtt_path.read_bytes()).hexdigest(),
                release_id="r3", variant="B", voice_id="voice_test_0001", model="speech-2.8-hd",
            )
            # Invalid hash format is rejected.
            broken = dict(doc)
            broken_master = dict(doc["master"])
            broken_master["sha256"] = "not-a-sha"
            broken["master"] = broken_master
            with self.assertRaises(AudioStageContractError):
                validate_audio_generation_evidence(broken)
            # The evidence digest binds every hash: any tamper changes it.
            baseline = evidence_digest(doc)
            for tamper in (
                {"chunks": [dict(doc["chunks"][0], audio_sha256="1" * 64)]},
                {"master": dict(doc["master"], sha256="2" * 64)},
                {"provider_vtt": dict(doc["provider_vtt"], sha256="3" * 64)},
            ):
                altered = dict(doc)
                altered.update(tamper)
                self.assertNotEqual(evidence_digest(altered), baseline)

    def test_A9_legacy_edge_vtt_is_not_reusable(self) -> None:
        # The expressive pipeline fails closed on legacy edge_vtt timing.
        with self.assertRaises(ProviderTimelineError):
            assert_provider_timeline_authority({"timing_source": TIMING_SOURCE_EDGE_LEGACY})
        # Missing provider hashes/timestamps are also rejected.
        with self.assertRaises(ProviderTimelineError):
            assert_provider_timeline_authority({"timing_source": TIMING_SOURCE_PROVIDER, "chunks": []})
        # A valid provider evidence passes.
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            chunks = [_chunk("c1", evidence, start=0.0, end=0.2, text="第一段。", duration=0.25)]
            vtt_path, _, _ = write_provider_vtt(chunks, evidence / "provider.vtt")
            master = concat_audio_master(chunks, evidence)
            doc = build_audio_generation_evidence(
                chunk_results=chunks, master=master, vtt_path=vtt_path,
                vtt_sha256=hashlib.sha256(vtt_path.read_bytes()).hexdigest(),
                release_id="r3", variant="B", voice_id="voice_test_0001", model="speech-2.8-hd",
            )
            assert_provider_timeline_authority(doc)  # no raise


class ProviderCaptionRestoreTests(unittest.TestCase):
    def test_character_level_provider_timestamps_are_grouped_into_readable_captions(self) -> None:
        from book_video_factory.audio_stage.pronunciation import compile_spoken_script

        compilation = compile_spoken_script(
            "故事的开头。",
            {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": []},
        )
        chunk = NarrationChunkResult(
            chunk_id="c1",
            provider="minimax",
            model="speech-2.8-hd",
            voice_id="voice_test_0001",
            audio_path="unused.wav",
            audio_sha256="0" * 64,
            duration=0.5,
            subtitle_timestamps=tuple(
                {"index": index, "start": index * 0.1, "end": (index + 1) * 0.1, "text": char}
                for index, char in enumerate("故事的开头")
            ),
            trace_id="trace-c1",
            request_digest="0" * 64,
        )

        captions = restore_display_captions_from_provider(
            [chunk], compilation, min_chars=2, max_chars=30, min_duration=0.1
        )

        self.assertEqual([caption["text"] for caption in captions], ["故事的开头。"])
        self.assertEqual(captions[0]["rawCueIndexes"], [0, 1, 2, 3, 4])

    def test_character_level_provider_timestamps_prefer_sentence_boundaries(self) -> None:
        from book_video_factory.audio_stage.pronunciation import compile_spoken_script

        compilation = compile_spoken_script(
            "第一句。第二句。",
            {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": []},
        )
        chunk = NarrationChunkResult(
            chunk_id="c1",
            provider="minimax",
            model="speech-2.8-hd",
            voice_id="voice_test_0001",
            audio_path="unused.wav",
            audio_sha256="0" * 64,
            duration=0.6,
            subtitle_timestamps=tuple(
                {"index": index, "start": index * 0.1, "end": (index + 1) * 0.1, "text": char}
                for index, char in enumerate("第一句第二句")
            ),
            trace_id="trace-c1",
            request_digest="0" * 64,
        )

        captions = restore_display_captions_from_provider(
            [chunk], compilation, min_chars=2, max_chars=30, min_duration=0.1
        )

        self.assertEqual([caption["text"] for caption in captions], ["第一句。", "第二句。"])

    def test_display_captions_restored_from_provider_timestamps(self) -> None:
        from book_video_factory.audio_stage.pronunciation import compile_spoken_script

        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            spoken = "第一句。第二句。"
            compilation = compile_spoken_script(spoken, {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": []})
            chunks = [
                _chunk("c1", evidence, start=0.0, end=0.20, text="第一句。", duration=0.25),
                _chunk("c2", evidence, start=0.0, end=0.18, text="第二句。", duration=0.30),
            ]
            captions = restore_display_captions_from_provider(
                chunks, compilation, min_chars=2, max_chars=30, min_duration=0.1
            )
            self.assertEqual(len(captions), 2)
            self.assertEqual(captions[0]["text"], "第一句。")
            self.assertAlmostEqual(captions[1]["start"], 0.25, places=2)
            self.assertEqual(captions[1]["text"], "第二句。")


if __name__ == "__main__":
    unittest.main()
