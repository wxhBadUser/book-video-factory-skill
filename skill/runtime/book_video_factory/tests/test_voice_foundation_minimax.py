# -*- coding: utf-8 -*-
"""M4B provider/voice-foundation tests (A1/A2/A3 + A6/A7/A8, offline)."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_voice_foundation,
)
from book_video_factory.audio_stage.providers import (
    NarrationChunkRequest,
    NarrationProviderError,
)
from book_video_factory.audio_stage.providers.minimax import MiniMaxNarrationProvider
from book_video_factory.audio_stage.voice_foundation import (
    VoiceFoundation,
    VoiceFoundationError,
    build_voice_foundation,
    check_clone_activation_window,
    mark_voice_used,
)

SINCERE_ADULT = "Chinese (Mandarin)_Sincere_Adult"


def _tiny_wav_bytes(duration: float = 0.25, rate: int = 8000) -> bytes:
    frames = int(rate * duration)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = Path(handle.name)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * frames)
    data = path.read_bytes()
    path.unlink()
    return data


class _FakeTransport:
    """Word-level timestamps by default; supports sentence fallback + Get Voice."""

    def __init__(
        self,
        *,
        wav: bytes | None = None,
        subtitles: list[dict] | None = None,
        word_fallback_to_sentence: bool = False,
        china_subtitle_file: bool = False,
        voices: list[dict] | None = None,
    ) -> None:
        self.wav = wav if wav is not None else _tiny_wav_bytes()
        self.subtitles = subtitles if subtitles is not None else [
            {"start_time": 0.0, "end_time": 0.06, "text": "真实"},
            {"start_time": 0.06, "end_time": 0.12, "text": "时间戳"},
            {"start_time": 0.12, "end_time": 0.25, "text": "第二句。"},
        ]
        self.word_fallback_to_sentence = word_fallback_to_sentence
        self.china_subtitle_file = china_subtitle_file
        self.voices = voices or [
            {"voice_id": "male-qn-qingse", "voice_name": "Chinese (Mandarin)_Sincere_Adult"},
            {"voice_id": "male-qn-jingying", "voice_name": "Chinese (Mandarin)_Gentleman"},
        ]
        self.posted: list[dict] = []
        self.trace_id = "fake-trace-0001"

    def post_json(self, url, headers, payload, timeout):
        self.posted.append({"url": url, "headers": dict(headers), "payload": dict(payload)})
        if "t2a_v2" in url:
            if self.word_fallback_to_sentence and payload.get("subtitle_type") == "word":
                return {
                    "base_resp": {"status_code": 0, "status_msg": ""},
                    "data": {"audio": base64.b64encode(self.wav).decode("ascii"), "subtitle": [], "trace_id": self.trace_id},
                }
            if self.china_subtitle_file:
                return {
                    "base_resp": {"status_code": 0, "status_msg": ""},
                    "data": {
                        "audio": base64.b64encode(self.wav).decode("ascii"),
                        "subtitle_file": "https://example.invalid/sub.json",
                        "trace_id": self.trace_id,
                    },
                }
            return {
                "base_resp": {"status_code": 0, "status_msg": ""},
                "data": {
                    "audio": base64.b64encode(self.wav).decode("ascii"),
                    "subtitle": list(self.subtitles),
                    "subtitle_type": payload.get("subtitle_type", "word"),
                    "trace_id": self.trace_id,
                },
            }
        if "get_voice" in url:
            # China platform Get Voice: POST /v1/get_voice, voice lists at the
            # top level (also accepted under ``data`` by the parser).
            return {"base_resp": {"status_code": 0, "status_msg": ""}, "system_voice": list(self.voices)}
        if "voice_clone" in url:
            return {"base_resp": {"status_code": 0, "status_msg": ""}, "data": {"voice_id": "voice_cloned_0001"}}
        raise AssertionError(f"unexpected url {url}")

    def get_json(self, url, headers, timeout):
        return {"base_resp": {"status_code": 0, "status_msg": ""}, "data": {"voice_list": list(self.voices)}}

    def get_bytes(self, url, headers, timeout):
        if "sub.json" in url:
            import json as _json
            payload = [
                {
                    "text": "真实时间戳第二句。",
                    "time_begin": 0.0,
                    "time_end": 250.0,
                    "timestamped_words": [
                        {"word": "真", "time_begin": 0.0, "time_end": 60.0},
                        {"word": "实", "time_begin": 60.0, "time_end": 120.0},
                        {"word": "第二句。", "time_begin": 120.0, "time_end": 250.0},
                    ],
                }
            ]
            return _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.wav

    def upload_file(self, url, headers, file_path, purpose, timeout):
        return {"data": {"file_id": "file_0001"}}


def _provider(*, api_key: str = "test-key", transport=None, voice_id: str = "voice_test_0001", **kwargs):
    return MiniMaxNarrationProvider(
        voice_id=voice_id,
        api_key=api_key,
        api_base="https://example.invalid",
        transport=transport or _FakeTransport(),
        **kwargs,
    )


class VoiceFoundationTests(unittest.TestCase):
    def test_system_voice_builds_without_source(self) -> None:
        # The foundation freezes the RESOLVED voice_id (Get Voice) whose name
        # matches the preferred narrator voice; the name itself is not a
        # valid T2A voice_id.
        foundation = build_voice_foundation(
            release_id="r3",
            voice_strategy="system_voice",
            voice_id="male-qn-qingse",
        )
        doc = foundation.to_dict()
        self.assertEqual(doc["voice_strategy"], "system_voice")
        self.assertEqual(doc["voice_id"], "male-qn-qingse")
        self.assertIsNone(doc["cloned_at"])
        validate_voice_foundation(doc)

    def test_cloned_voice_requires_valid_source(self) -> None:
        with self.assertRaises(VoiceFoundationError):
            build_voice_foundation(
                release_id="r3", voice_strategy="cloned_voice", voice_id="voice_cloned_0001"
            )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "voice.mp3"
            source.write_bytes(b"not-a-real-audio-file")
            with self.assertRaises(VoiceFoundationError):
                build_voice_foundation(
                    release_id="r3", voice_strategy="cloned_voice",
                    voice_id="voice_cloned_0001", source_audio=source,
                )

    def test_A2_unused_activation_window_is_advisory(self) -> None:
        # A clone untouched for >168h is WARNED, not hard-rejected on the local
        # timestamp alone (M4B A2); real availability is confirmed via provider.
        old = datetime.now(timezone.utc) - timedelta(hours=200)
        foundation = VoiceFoundation(
            schema_version="voice-foundation.v1",
            release_id="r3",
            provider="minimax",
            voice_strategy="cloned_voice",
            voice_id="voice_cloned_0001",
            model_family="speech-2.8-hd",
            cloned_at=old.isoformat(),
            unused_activation_window_hours=168,
        )
        doc = foundation.to_dict()
        validate_voice_foundation(doc)
        status = check_clone_activation_window(doc, now=datetime.now(timezone.utc))
        self.assertEqual(status["status"], "warn_unused")
        # A recently-used clone stays active even if cloned_at is old.
        used = mark_voice_used(doc, used_at=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertIsNotNone(used["last_used_at"])
        status2 = check_clone_activation_window(used, now=datetime.now(timezone.utc))
        self.assertEqual(status2["status"], "active")

    def test_A1_verify_system_voice_passes_when_present(self) -> None:
        with _provider(transport=_FakeTransport()) as provider:
            found = provider.verify_system_voice(SINCERE_ADULT)
        # The name resolves to the account's real T2A voice_id.
        self.assertEqual(found["voice_name"], SINCERE_ADULT)
        self.assertEqual(found["voice_id"], "male-qn-qingse")
        # Exact voice_id match also works.
        with _provider(transport=_FakeTransport()) as provider2:
            found2 = provider2.verify_system_voice("male-qn-qingse")
        self.assertEqual(found2["voice_id"], "male-qn-qingse")

    def test_A1_verify_system_voice_fails_closed_with_list(self) -> None:
        with _provider(transport=_FakeTransport()) as provider:
            with self.assertRaises(NarrationProviderError) as caught:
                provider.verify_system_voice("male-announcer-not-present")
            message = str(caught.exception)
            self.assertIn("male-announcer-not-present", message)
            self.assertIn(SINCERE_ADULT, message)  # available Mandarin voices listed

    def test_foundation_validation_rejects_bad_strategy(self) -> None:
        foundation = build_voice_foundation(
            release_id="r3", voice_strategy="system_voice", voice_id="male-qn-qingse"
        ).to_dict()
        broken = dict(foundation)
        broken["voice_strategy"] = "deepfake"
        with self.assertRaises(AudioStageContractError):
            validate_voice_foundation(broken)


class MiniMaxClientTests(unittest.TestCase):
    def test_A6_same_voice_id_across_all_chunks(self) -> None:
        transport = _FakeTransport()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            with _provider(transport=transport) as provider:
                results = []
                for index in range(3):
                    request = NarrationChunkRequest(
                        chunk_id=f"chunk-{index}", text=f"第{index}段旁白。",
                        provider="minimax", model="speech-2.8-hd", voice_id="voice_test_0001",
                    )
                    results.append(provider.synthesize(request, evidence_dir=evidence))
            voice_ids = {result.voice_id for result in results}
            self.assertEqual(voice_ids, {"voice_test_0001"})
            for post in transport.posted:
                self.assertEqual(post["payload"]["voice_setting"]["voice_id"], "voice_test_0001")

    def test_A7_control_markers_stay_in_spoken_only(self) -> None:
        provider = _provider()
        request = NarrationChunkRequest(
            chunk_id="chunk-1", text="他走在回家的路上。", provider="minimax",
            model="speech-2.8-hd", voice_id="voice_test_0001",
            sound_tags=["breath"], pause_after_ms=800,
        )
        payload = provider.build_t2a_payload(request)
        self.assertIn("(breath)", payload["text"])
        self.assertIn("<#1#>", payload["text"])
        self.assertNotIn("(breath)", request.text)
        self.assertNotIn("<#", request.text)

    def test_A3_word_timestamps_are_preferred_authority(self) -> None:
        transport = _FakeTransport()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            with _provider(transport=transport) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1", text="真实时间戳第二句。", provider="minimax",
                    model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                result = provider.synthesize(request, evidence_dir=evidence)
            # The request asked for word timestamps and got word granularity.
            self.assertEqual(result.subtitle_granularity, "word")
            self.assertEqual(transport.posted[0]["payload"]["subtitle_type"], "word")
            self.assertEqual(transport.posted[0]["payload"]["subtitle_enable"], True)
            # Word cues are real provider timestamps, never estimates.
            self.assertEqual(len(result.subtitle_timestamps), 3)
            self.assertAlmostEqual(result.subtitle_timestamps[2]["end"], 0.25, places=2)
            record = json.loads((evidence / "chunk-1.json").read_text(encoding="utf-8"))
            self.assertEqual(record["subtitle_granularity"], "word")

    def test_A3_sentence_is_explicit_fallback_only(self) -> None:
        # Without fallback enabled, missing word timestamps fail closed.
        transport = _FakeTransport(word_fallback_to_sentence=True)
        with tempfile.TemporaryDirectory() as temporary:
            with _provider(transport=transport) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1", text="旁白。", provider="minimax",
                    model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                with self.assertRaises(NarrationProviderError):
                    provider.synthesize(request, evidence_dir=Path(temporary))
        # With the explicit opt-in, one sentence-level retry happens and is
        # recorded as a fallback, never reconstructed from character counts.
        transport = _FakeTransport(word_fallback_to_sentence=True)
        with tempfile.TemporaryDirectory() as temporary:
            with _provider(transport=transport, allow_sentence_fallback=True) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1", text="旁白。", provider="minimax",
                    model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                result = provider.synthesize(request, evidence_dir=Path(temporary))
            self.assertEqual(result.subtitle_granularity, "sentence")
            self.assertEqual(len(transport.posted), 2)
            self.assertEqual(transport.posted[1]["payload"]["subtitle_type"], "sentence")

    def test_china_subtitle_file_word_timestamps(self) -> None:
        # China T2A returns subtitles via a signed subtitle_file URL whose
        # timestamped_words use milliseconds; the provider converts to seconds.
        transport = _FakeTransport(china_subtitle_file=True)
        with tempfile.TemporaryDirectory() as temporary:
            with _provider(transport=transport) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1", text="真实时间戳第二句。", provider="minimax",
                    model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                result = provider.synthesize(request, evidence_dir=Path(temporary))
            self.assertEqual(result.subtitle_granularity, "word")
            self.assertEqual(len(result.subtitle_timestamps), 3)
            self.assertAlmostEqual(result.subtitle_timestamps[0]["start"], 0.0, places=2)
            self.assertAlmostEqual(result.subtitle_timestamps[0]["text"], "真")
            self.assertAlmostEqual(result.subtitle_timestamps[2]["end"], 0.25, places=2)

    def test_A8_provider_timestamps_are_authoritative(self) -> None:
        transport = _FakeTransport()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            with _provider(transport=transport) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1",
                    text="一段很长的旁白估计需要九秒，但真实语音只有零点二五秒。",
                    provider="minimax", model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                result = provider.synthesize(request, evidence_dir=evidence)
            self.assertEqual(result.subtitle_granularity, "word")
            self.assertAlmostEqual(result.duration, 0.25, places=2)
            self.assertEqual(result.trace_id, "fake-trace-0001")
            record = json.loads((evidence / "chunk-1.json").read_text(encoding="utf-8"))
            self.assertEqual(record["audio_sha256"], result.audio_sha256)
            self.assertTrue((evidence / "chunk-1.wav").is_file())

    def test_api_error_fails_closed_without_fallback(self) -> None:
        class _FailingTransport(_FakeTransport):
            def post_json(self, url, headers, payload, timeout):
                return {"base_resp": {"status_code": 1004, "status_msg": "quota exceeded"}}

        with tempfile.TemporaryDirectory() as temporary:
            with _provider(transport=_FailingTransport()) as provider:
                request = NarrationChunkRequest(
                    chunk_id="chunk-1", text="旁白。", provider="minimax",
                    model="speech-2.8-hd", voice_id="voice_test_0001",
                )
                with self.assertRaises(NarrationProviderError) as caught:
                    provider.synthesize(request, evidence_dir=Path(temporary))
                self.assertIn("1004", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
