"""Narration provider boundary for the Book Video Factory audio stage.

The M4A expressive-narration pipeline synthesizes narration through a
provider-neutral boundary so the production policy can require a real
expressive provider (MiniMax speech-2.8-hd) while Edge-TTS remains available
only as an explicitly-marked legacy path.

Provider policies:
    legacy_edge        -- Edge-TTS only; kept for legacy projects that never
                          opted into expressive narration. New production must
                          not silently fall back here.
    minimax_required   -- MiniMax speech-2.8-hd required. Missing credentials
                          or provider failure FAIL CLOSED; the pipeline never
                          falls back to Edge-TTS.

This module contains no secrets and never stores API keys or signed URLs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Provider policies (contract values; see audio_stage/contracts.py).
PROVIDER_POLICY_LEGACY_EDGE = "legacy_edge"
PROVIDER_POLICY_MINIMAX_REQUIRED = "minimax_required"
PROVIDER_POLICIES = {PROVIDER_POLICY_LEGACY_EDGE, PROVIDER_POLICY_MINIMAX_REQUIRED}

# Provider identifiers (contract values; see audio_stage/contracts.py).
PROVIDER_EDGE = "edge-tts"
PROVIDER_MINIMAX = "minimax"
PROVIDER_IDS = {PROVIDER_EDGE, PROVIDER_MINIMAX}

# Default MiniMax HD model (verified against MiniMax official docs, 2026-08).
MINIMAX_HD_MODEL = "speech-2.8-hd"

# Supported MiniMax subtitle timestamp granularity for real timing. Word
# timestamps are the preferred timing authority (M4B A3); sentence timestamps
# are an explicit provider fallback only.
SUBTITLE_TIMESTAMPS_WORD = "word"
SUBTITLE_TIMESTAMPS_SENTENCE = "sentence"


class NarrationProviderError(RuntimeError):
    """Base error for provider synthesis failures (never a silent fallback)."""


class NarrationCredentialError(NarrationProviderError):
    """Provider credentials are missing or unusable; the pipeline fails closed."""


def _digest_request(payload: Mapping[str, Any]) -> str:
    """Stable request digest for idempotent evidence and de-duplication.

    The digest covers the normalized provider-agnostic request so the same
    performance segment synthesised twice produces the same digest. Secret
    values (API keys, signed URLs) are never part of this payload.
    """

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class NarrationChunkRequest:
    """One synthesis request for one performance segment (never one caption).

    ``text`` is the *spoken* text and may carry provider control markers (for
    example MiniMax ``(sighs)`` sound tags or ``<#1#>`` pause syntax). Display
    captions are restored separately and never contain control markers.
    """

    chunk_id: str
    text: str
    provider: str
    model: str
    voice_id: str
    speed: float = 1.0          # 0.5-1.5 multiplier; 1.0 = natural
    volume: float = 1.0         # 0.0-2.0 gain multiplier; 1.0 = normal
    pitch: float = 0.0          # semitones relative; 0 = natural
    pause_before_ms: int = 0
    pause_after_ms: int = 0
    sound_tags: Sequence[str] = field(default_factory=tuple)
    emotion: str | None = None  # provider-agnostic hint; mapped at the client
    intensity: float = 0.35     # 0-1 performance intensity for this segment
    extra: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        payload = {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "voice_id": self.voice_id,
            "speed": self.speed,
            "volume": self.volume,
            "pitch": self.pitch,
            "pause_before_ms": self.pause_before_ms,
            "pause_after_ms": self.pause_after_ms,
            "sound_tags": list(self.sound_tags),
            "emotion": self.emotion,
            "intensity": self.intensity,
        }
        return _digest_request(payload)


@dataclass(frozen=True)
class NarrationChunkResult:
    """Provider evidence for one synthesized chunk.

    ``subtitle_timestamps`` are the provider's real timestamps (the
    authoritative timing source); ``subtitle_granularity`` records whether
    they are word-level (preferred) or sentence-level (explicit fallback).
    ``audio_path`` is relative to the evidence output directory.
    """

    chunk_id: str
    provider: str
    model: str
    voice_id: str
    audio_path: str
    audio_sha256: str
    duration: float
    subtitle_timestamps: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    subtitle_granularity: str = SUBTITLE_TIMESTAMPS_WORD
    trace_id: str | None = None
    request_digest: str = ""


class NarrationProvider:
    """Provider-neutral synthesis boundary.

    Implementations must:
    * fail closed on missing credentials / failed requests (no Edge fallback);
    * return real audio plus real provider timestamps;
    * never persist API keys or signed URLs.
    """

    provider_id: str = ""
    policy: str = PROVIDER_POLICY_LEGACY_EDGE

    def synthesize(self, request: NarrationChunkRequest) -> NarrationChunkResult:
        raise NotImplementedError

    def preflight(self) -> None:
        """Validate credentials/config before any request; raises on failure."""

    def close(self) -> None:
        """Release any client resources."""

    def __enter__(self) -> "NarrationProvider":
        self.preflight()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
        return None
