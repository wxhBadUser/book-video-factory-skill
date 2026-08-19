"""MiniMax narration provider (speech-2.8-hd).

Production expressive narration provider. Real audio is synthesized via the
MiniMax T2A API (``/v1/t2a_v2``) with ``speech-2.8-hd`` and sentence-level
subtitle timestamps; voice identity comes from a stable cloned ``voice_id``
(``VOICE_FOUNDATION.json``) or a MiniMax system voice.

Fail-closed contract:
* Missing credentials raise ``NarrationCredentialError`` (never an Edge
  fallback); the error names the exact missing environment variable.
* A failed request is recorded and re-raised; the pipeline never silently
  degrades to Edge-TTS.
* API keys and signed URLs are never persisted; only the request digest and
  evidence hashes enter manifests.

The HTTP transport is injectable so the whole client is unit-testable offline
with a fake transport. Verified against MiniMax official docs (2026-08):
``/v1/t2a_v2`` model ``speech-2.8-hd``; voice clone source MP3/M4A/WAV 10s-5min
<=20MB with stable ``voice_id`` reuse within the 168h retention window.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import wave
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..providers.base import (
    MINIMAX_HD_MODEL,
    NarrationChunkRequest,
    NarrationChunkResult,
    NarrationCredentialError,
    NarrationProvider,
    NarrationProviderError,
    PROVIDER_MINIMAX,
    PROVIDER_POLICY_MINIMAX_REQUIRED,
    SUBTITLE_TIMESTAMPS_SENTENCE,
    SUBTITLE_TIMESTAMPS_WORD,
)

# MiniMax official endpoints (overseas base; domestic key is not interchangeable).
DEFAULT_API_BASE = "https://api.minimax.io"
T2A_V2_PATH = "/v1/t2a_v2"
VOICE_CLONE_PATH = "/v1/voice_clone"
FILES_UPLOAD_PATH = "/v1/files/upload"
GET_VOICE_PATH = "/v1/get_voice"

_ENV_API_KEY = "MINIMAX_API_KEY"
_ENV_GROUP_ID = "MINIMAX_GROUP_ID"
_ENV_API_BASE = "MINIMAX_API_BASE"
_REQUIRED_ENV = (_ENV_API_KEY,)


@runtime_checkable
class NarrationTransport(Protocol):
    def post_json(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> dict[str, Any]: ...

    def get_json(self, url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]: ...

    def get_bytes(self, url: str, headers: Mapping[str, str], timeout: float) -> bytes: ...

    def upload_file(self, url: str, headers: Mapping[str, str], file_path: Path, purpose: str, timeout: float) -> dict[str, Any]: ...


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, OSError, ValueError, ZeroDivisionError):
        return None


def _ms_to_sec(value: Any) -> float | None:
    """Convert a China subtitle millisecond value to seconds (None-safe)."""
    if value is None:
        return None
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


# Sound tags (e.g. (breath), (sighs)) and pause markers (<#1#>) are control
# directives, not spoken text; the provider may surface them as word cues and
# they must never enter the display caption timeline.
_CONTROL_CUE_RE = re.compile(r"^(?:\([a-z][a-z-]*\)|<#\d+(?:\.\d+)?#>)$")


# Punctuation-only cues (the China word stream emits ``，``/``。`` as separate
# words) collapse to empty after normalize_text and must not enter the timeline.
_PUNCT_ONLY_RE = re.compile(r"[\s\u201c\u201d\u2018\u2019\"\'\u300a\u300b\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001,.!?;:\u2026\u2014-]")

def _is_cue_blank(text: str) -> bool:
    return not _PUNCT_ONLY_RE.sub("", text)


class MiniMaxNarrationProvider(NarrationProvider):
    provider_id = PROVIDER_MINIMAX
    policy = PROVIDER_POLICY_MINIMAX_REQUIRED

    def __init__(
        self,
        *,
        voice_id: str,
        api_key: str | None = None,
        group_id: str | None = None,
        api_base: str | None = None,
        model: str = MINIMAX_HD_MODEL,
        transport: NarrationTransport | None = None,
        timeout: float = 120.0,
        allow_sentence_fallback: bool = False,
    ) -> None:
        self._voice_id = voice_id
        self._model = model
        self._api_key = api_key if api_key is not None else os.environ.get(_ENV_API_KEY)
        self._group_id = group_id if group_id is not None else os.environ.get(_ENV_GROUP_ID)
        self._api_base = (api_base or os.environ.get(_ENV_API_BASE) or DEFAULT_API_BASE).rstrip("/")
        self._transport = transport or _UrllibTransport()
        self._timeout = timeout
        # Word timestamps are the preferred timing authority (M4B A3).
        # Sentence timestamps are accepted only as an explicit, recorded
        # provider fallback when word timing is unavailable or invalid.
        self._allow_sentence_fallback = allow_sentence_fallback
        self._closed = False

    # -- credentials --------------------------------------------------------

    def _missing_env(self) -> list[str]:
        return [name for name in _REQUIRED_ENV if not self._api_key]

    def preflight(self) -> None:
        missing = self._missing_env()
        if missing:
            raise NarrationCredentialError(
                "MiniMax narration is blocked: missing required environment "
                f"variable(s): {', '.join(missing)}"
            )
        if not self._voice_id or not self._voice_id.strip():
            raise NarrationCredentialError(
                "MiniMax narration is blocked: voice_id is empty (build "
                "VOICE_FOUNDATION.json first)"
            )
        if self._closed:
            raise NarrationProviderError("MiniMax provider is closed")

    # -- T2A synthesis -------------------------------------------------------

    def synthesize(
        self,
        request: NarrationChunkRequest,
        *,
        evidence_dir: Path,
    ) -> NarrationChunkResult:
        """Synthesize one performance segment and persist real evidence.

        ``evidence_dir`` receives the audio file and a per-chunk JSON evidence
        record. Raises on any failure; never falls back to Edge-TTS.
        """

        self.preflight()
        target = evidence_dir.expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        # Word timestamps are the preferred timing authority (M4B A3).
        payload = self.build_t2a_payload(request)
        response, granularity, subtitle_entries = self._request_with_timestamps(request, payload)
        data = response["data"]
        audio_value = data.get("audio")
        if not audio_value:
            raise NarrationProviderError(f"MiniMax T2A response has no audio for {request.chunk_id!r}")
        audio_bytes = self._decode_audio(audio_value, headers=self._headers(), timeout=self._timeout)
        if not audio_bytes:
            raise NarrationProviderError(f"MiniMax T2A audio is empty for {request.chunk_id!r}")
        audio_path = target / f"{request.chunk_id}.wav"
        audio_path.write_bytes(audio_bytes)
        duration = self._duration_of(audio_path, response)
        timestamps = self._normalize_subtitles(subtitle_entries, request=request)
        trace_id = None
        for source in (data, response):
            if isinstance(source.get("trace_id"), str):
                trace_id = source["trace_id"]
                break
        result = NarrationChunkResult(
            chunk_id=request.chunk_id,
            provider=self.provider_id,
            model=self._model,
            voice_id=self._voice_id,
            audio_path=audio_path.relative_to(target).as_posix(),
            audio_sha256=_sha256_bytes(audio_bytes),
            duration=duration,
            subtitle_timestamps=tuple(timestamps),
            subtitle_granularity=granularity,
            trace_id=trace_id,
            request_digest=request.digest(),
        )
        (target / f"{request.chunk_id}.json").write_text(
            json.dumps({
                "chunk_id": request.chunk_id,
                "provider": result.provider,
                "model": result.model,
                "voice_id": result.voice_id,
                "audio_sha256": result.audio_sha256,
                "duration": result.duration,
                "subtitle_granularity": result.subtitle_granularity,
                "trace_id": result.trace_id,
                "request_digest": result.request_digest,
                "subtitle_timestamps": list(result.subtitle_timestamps),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    def _request_with_timestamps(
        self,
        request: NarrationChunkRequest,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """POST the T2A request and return ``(response, granularity, entries)``.

        Word timing is requested first. If the provider returns no usable word
        timestamps and ``allow_sentence_fallback`` is explicitly enabled, one
        sentence-level retry is performed and recorded as a fallback. Timing is
        never reconstructed from character counts or estimates. Entries are
        normalized to ``{'start_time', 'end_time', 'text'}`` in seconds (the China
        platform returns them via ``subtitle_file`` in milliseconds).
        """

        url = self._t2a_url()
        response = self._post_t2a(url, payload, chunk_id=request.chunk_id)
        entries, granularity = self._collect_subtitles(response, requested=SUBTITLE_TIMESTAMPS_WORD)
        if granularity == SUBTITLE_TIMESTAMPS_WORD and entries:
            return response, granularity, entries
        if self._allow_sentence_fallback:
            sentence_payload = dict(payload)
            sentence_payload["subtitle_type"] = SUBTITLE_TIMESTAMPS_SENTENCE
            response = self._post_t2a(url, sentence_payload, chunk_id=request.chunk_id)
            entries, granularity = self._collect_subtitles(response, requested=SUBTITLE_TIMESTAMPS_SENTENCE)
            if entries:
                return response, granularity, entries
        raise NarrationProviderError(
            f"MiniMax T2A returned no usable word timestamps for {request.chunk_id!r}"
        )


    def _post_t2a(self, url: str, payload: Mapping[str, Any], *, chunk_id: str) -> dict[str, Any]:
        try:
            response = self._transport.post_json(url, self._headers(), dict(payload), timeout=self._timeout)
        except NarrationProviderError:
            raise
        except Exception as error:  # transport/network failure
            raise NarrationProviderError(
                f"MiniMax T2A request failed for chunk {chunk_id!r}: {error}"
            ) from error
        self._raise_for_base_resp(response, chunk_id=chunk_id)
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise NarrationProviderError(f"MiniMax T2A response has no data for {chunk_id!r}")
        return response

    def _collect_subtitles(
        self,
        response: Mapping[str, Any],
        *,
        requested: str,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """Return ``(normalized entries, actual granularity)``.

        Reads inline ``data.subtitle`` / top-level ``subtitle`` when present,
        otherwise downloads the China platform's ``subtitle_file`` (signed URL)
        and converts its millisecond timestamps into the standard shape.
        """
        data = response.get("data")
        subtitle = data.get("subtitle") if isinstance(data, Mapping) else None
        if not isinstance(subtitle, list):
            subtitle = response.get("subtitle")
        if isinstance(subtitle, list) and subtitle:
            return subtitle, requested
        sub_file = data.get("subtitle_file") if isinstance(data, Mapping) else None
        if isinstance(sub_file, str) and sub_file:
            return self._load_china_subtitle_file(sub_file)
        return None, requested

    def _load_china_subtitle_file(
        self,
        url: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Download and parse a China ``subtitle_file`` (JSON array).

        Sentence items use ``time_begin/time_end`` in milliseconds; word items
        live in ``timestamped_words`` with ``time_begin/time_end`` in milliseconds.
        Returns entries normalized to ``{'start_time','end_time','text'}`` in seconds.
        """
        try:
            raw = self._transport.get_bytes(url, headers={"Accept": "application/json"}, timeout=self._timeout)
        except Exception as error:
            raise NarrationProviderError(f"MiniMax subtitle_file download failed: {error}") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NarrationProviderError("MiniMax subtitle_file is not valid JSON") from error
        if not isinstance(payload, list) or not payload:
            raise NarrationProviderError("MiniMax subtitle_file contains no subtitle items")
        word_entries: list[dict[str, Any]] = []
        sentence_entries: list[dict[str, Any]] = []
        has_words = False
        for item in payload:
            if not isinstance(item, Mapping):
                raise NarrationProviderError("MiniMax subtitle_file item is not an object")
            timestamped = item.get("timestamped_words")
            if isinstance(timestamped, list) and timestamped:
                has_words = True
                for word in timestamped:
                    if not isinstance(word, Mapping):
                        raise NarrationProviderError("MiniMax subtitle word is not an object")
                    word_entries.append({
                        "start_time": _ms_to_sec(word.get("time_begin")),
                        "end_time": _ms_to_sec(word.get("time_end")),
                        "text": word.get("word") or word.get("text"),
                    })
            else:
                sentence_entries.append({
                    "start_time": _ms_to_sec(item.get("time_begin")),
                    "end_time": _ms_to_sec(item.get("time_end")),
                    "text": item.get("text"),
                })
        if has_words:
            return word_entries, SUBTITLE_TIMESTAMPS_WORD
        if sentence_entries:
            return sentence_entries, SUBTITLE_TIMESTAMPS_SENTENCE
        raise NarrationProviderError("MiniMax subtitle_file has no usable timestamps")


    def _t2a_url(self) -> str:
        if self._group_id:
            return f"{self._api_base}{T2A_V2_PATH}?GroupId={self._group_id}"
        return f"{self._api_base}{T2A_V2_PATH}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _decode_audio(self, audio_value: Any, *, headers: Mapping[str, str], timeout: float) -> bytes:
        if isinstance(audio_value, str):
            if audio_value.startswith("data:"):
                # data URL: data:audio/wav;base64,....
                _, _, encoded = audio_value.partition(",")
                try:
                    return base64.b64decode(encoded)
                except (ValueError, TypeError) as error:
                    raise NarrationProviderError("MiniMax audio data URL is invalid") from error
            if audio_value.startswith("http://") or audio_value.startswith("https://"):
                try:
                    return self._transport.get_bytes(audio_value, headers={"Accept": "audio/*"}, timeout=timeout)
                except Exception as error:
                    raise NarrationProviderError(f"MiniMax audio download failed: {error}") from error
            if re.fullmatch(r"[0-9a-fA-F]+", audio_value) and len(audio_value) % 2 == 0:
                # China platform returns hex-encoded audio bytes.
                try:
                    return bytes.fromhex(audio_value)
                except ValueError as error:
                    raise NarrationProviderError("MiniMax audio hex payload is invalid") from error
            try:
                return base64.b64decode(audio_value)
            except (ValueError, TypeError) as error:
                raise NarrationProviderError("MiniMax audio payload is not base64/URL/hex") from error
        if isinstance(audio_value, list):
            try:
                return b"".join(base64.b64decode(part) for part in audio_value)
            except (ValueError, TypeError) as error:
                raise NarrationProviderError("MiniMax audio chunk payload is invalid") from error
        raise NarrationProviderError("MiniMax T2A audio has an unsupported shape")

    def _duration_of(self, audio_path: Path, response: Mapping[str, Any]) -> float:
        duration = _wav_duration(audio_path)
        if duration:
            return round(duration, 3)
        # Fall back to any duration the response exposes; otherwise the audio
        # is unusable as evidence.
        data = response.get("data")
        if isinstance(data, Mapping):
            for key in ("duration", "audio_duration"):
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    return round(float(value), 3)
        raise NarrationProviderError("MiniMax audio duration could not be determined")

    def _normalize_subtitles(
        self,
        subtitle: Any,
        *,
        request: NarrationChunkRequest,
    ) -> list[dict[str, Any]]:
        if not isinstance(subtitle, list) or not subtitle:
            raise NarrationProviderError(
                f"MiniMax T2A returned no sentence timestamps for {request.chunk_id!r}"
            )
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(subtitle):
            if not isinstance(item, Mapping):
                raise NarrationProviderError("MiniMax subtitle item is not an object")
            try:
                start = float(item.get("start_time", item.get("start")))
                end = float(item.get("end_time", item.get("end")))
            except (TypeError, ValueError):
                raise NarrationProviderError("MiniMax subtitle timing is invalid") from None
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise NarrationProviderError("MiniMax subtitle text is empty")
            if start < 0 or end <= start:
                raise NarrationProviderError("MiniMax subtitle timing is non-monotonic")
            stripped = text.strip()
            if _CONTROL_CUE_RE.fullmatch(stripped):
                # Sound tags / pause markers are control, never captions.
                continue
            if _is_cue_blank(stripped):
                # Punctuation-only word cues add no display text.
                continue
            normalized.append({
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": stripped,
            })
        return normalized

    def _raise_for_base_resp(self, response: Mapping[str, Any], *, chunk_id: str) -> None:
        base = response.get("base_resp")
        if isinstance(base, Mapping):
            code = base.get("status_code")
            message = base.get("status_msg")
            if code not in (0, 1000) and code is not None:
                raise NarrationProviderError(
                    f"MiniMax T2A failed for {chunk_id!r}: code={code} message={message}"
                )

    # -- system voice discovery (M4B A1) --------------------------------------

    def get_voice_url(self) -> str:
        if self._group_id:
            return f"{self._api_base}{GET_VOICE_PATH}?GroupId={self._group_id}"
        return f"{self._api_base}{GET_VOICE_PATH}"

    def list_voices(self, voice_type: str = "all") -> list[dict[str, Any]]:
        """Return the current account's available voices (Get Voice API).

        POST with ``voice_type`` (system / voice_cloning / voice_generation /
        all), the shape documented on both the China (api.minimaxi.com) and
        international platforms. Voice lists may appear at the top level or
        under ``data``.
        """

        self.preflight()
        try:
            response = self._transport.post_json(
                self.get_voice_url(),
                self._headers(),
                {"voice_type": voice_type},
                timeout=self._timeout,
            )
        except Exception as error:
            raise NarrationProviderError(f"MiniMax Get Voice failed: {error}") from error
        self._raise_for_base_resp(response, chunk_id="get-voice")
        containers = []
        data = response.get("data")
        if isinstance(data, Mapping):
            containers.append(data)
        containers.append(response)
        voices: list[dict[str, Any]] = []
        for container in containers:
            for key in ("system_voice", "voice_cloning", "voice_generation", "voice_list"):
                bucket = container.get(key)
                if isinstance(bucket, list):
                    for item in bucket:
                        if isinstance(item, Mapping):
                            voices.append(dict(item))
        if not voices:
            raise NarrationProviderError("MiniMax Get Voice returned no voices")
        return voices

    def verify_system_voice(self, preferred: str) -> dict[str, Any]:
        """Resolve a preferred system voice against the live Get Voice list.

        ``preferred`` may be a ``voice_name`` (for example
        ``Chinese (Mandarin)_Sincere_Adult``) or an exact ``voice_id``. The
        returned dict contains the account's real ``voice_id`` (the value T2A
        accepts). Fails closed -- never silently selects another voice -- and
        lists the available Mandarin voices so a human can choose.
        """

        voices = self.list_voices()
        for voice in voices:
            if voice.get("voice_id") == preferred or voice.get("voice_name") == preferred:
                return voice
        mandarin = sorted({
            f"{v.get('voice_name', '')} ({v.get('voice_id', '')})"
            for v in voices
            if "Mandarin" in str(v.get("voice_name", "")) or "Mandarin" in str(v.get("voice_id", ""))
        })
        raise NarrationProviderError(
            f"system voice {preferred!r} is not available on this account; "
            f"available Mandarin voices: {mandarin}"
        )

    # -- voice cloning --------------------------------------------------------

    def clone_voice(
        self,
        *,
        source_audio: Path,
        prompt_audio: Path | None = None,
        prompt_text: str | None = None,
    ) -> dict[str, Any]:
        """Upload an authorized voice source and clone a stable voice_id."""

        self.preflight()
        upload_url = f"{self._api_base}{FILES_UPLOAD_PATH}"
        try:
            upload = self._transport.upload_file(
                upload_url,
                {"Authorization": f"Bearer {self._api_key}"},
                source_audio.expanduser().resolve(),
                purpose="voice_clone",
                timeout=self._timeout,
            )
        except Exception as error:
            raise NarrationProviderError(f"MiniMax voice source upload failed: {error}") from error
        file_id = (upload.get("data") or {}).get("file_id") if isinstance(upload.get("data"), Mapping) else None
        if not isinstance(file_id, str) or not file_id:
            raise NarrationProviderError("MiniMax voice source upload returned no file_id")
        clone_payload: dict[str, Any] = {
            "model": self._model,
            "file_id": file_id,
            "voice_setting": {"voice_id": self._voice_id},
        }
        if prompt_audio is not None:
            clone_payload["prompt_audio"] = str(prompt_audio.expanduser().resolve())
        if prompt_text:
            clone_payload["prompt_text"] = prompt_text
        try:
            response = self._transport.post_json(
                f"{self._api_base}{VOICE_CLONE_PATH}",
                self._headers(),
                clone_payload,
                timeout=self._timeout,
            )
        except Exception as error:
            raise NarrationProviderError(f"MiniMax voice clone failed: {error}") from error
        self._raise_for_base_resp(response, chunk_id="voice-clone")
        data = response.get("data")
        voice_id = data.get("voice_id") if isinstance(data, Mapping) else None
        if not isinstance(voice_id, str) or not voice_id:
            raise NarrationProviderError("MiniMax voice clone returned no voice_id")
        return {"file_id": file_id, "voice_id": voice_id, "response_data": dict(data) if isinstance(data, Mapping) else {}}

    # -- provider boundary --------------------------------------------------

    def close(self) -> None:
        self._closed = True

    # -- request construction (deterministic, secret-free) -------------------

    def build_t2a_payload(self, request: NarrationChunkRequest) -> dict[str, Any]:
        text = request.text
        for tag in request.sound_tags:
            marker = f"({tag.strip()})"
            if marker not in text:
                text = f"{marker}{text}" if request.pause_before_ms else f"{text}{marker}"
        if request.pause_before_ms:
            text = f"<#{max(1, request.pause_before_ms // 500)}#>{text}"
        if request.pause_after_ms:
            text = f"{text}<#{max(1, request.pause_after_ms // 500)}#>"
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "language_boost": "auto",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self._voice_id,
                "speed": max(0.5, min(1.5, request.speed)),
                "vol": max(0.0, min(2.0, request.volume)),
                "pitch": int(math.floor(max(-12.0, min(12.0, request.pitch)) + 0.5)),  # China T2A requires int64 pitch
            },
            "audio_setting": {
                "sample_rate": 44100,  # China T2A rejects 48000; master is resampled to 48k at assembly
                "bitrate": 128000,
                "format": "wav",
                "channel": 1,
            },
            "text": text,
            "subtitle_enable": True,
            "subtitle_type": SUBTITLE_TIMESTAMPS_WORD,
        }
        return payload

    def request_digest(self, request: NarrationChunkRequest) -> str:
        return request.digest()


class _UrllibTransport:
    """Default transport built on the standard library (no new dependency)."""

    def post_json(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        import urllib.request
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_json(self, url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
        import urllib.request
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_bytes(self, url: str, headers: Mapping[str, str], timeout: float) -> bytes:
        import urllib.request
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def upload_file(self, url: str, headers: Mapping[str, str], file_path: Path, purpose: str, timeout: float) -> dict[str, Any]:
        import urllib.request
        import uuid
        boundary = uuid.uuid4().hex
        file_bytes = file_path.read_bytes()
        parts = [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="purpose"\r\n\r\n{purpose}\r\n'.encode("utf-8"),
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode("utf-8"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        body = b"".join(parts)
        request_headers = dict(headers)
        request_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
