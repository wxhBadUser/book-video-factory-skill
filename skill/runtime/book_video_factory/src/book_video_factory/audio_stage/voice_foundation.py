"""MiniMax Voice Foundation (M4A / M4B).

A Voice Foundation pins the production voice identity before any narration is
synthesized. Two strategies:

* ``cloned_voice`` -- an authorized voice source (MP3/M4A/WAV, 10s-5min,
  <=20MB, optional prompt audio <8s) is uploaded and cloned to a stable
  MiniMax ``voice_id``. MiniMax may delete cloned voices that are NOT USED
  within its stated window (currently ~7 days). This is modeled as an
  ``unused_activation_window``: a local timestamp alone never rejects an
  otherwise-available voice; real availability is confirmed against the
  provider (Get Voice / actual synthesis response) at runtime.
* ``system_voice`` -- no voice sample: a MiniMax system voice is verified
  against the current account's Get Voice result and frozen.

The foundation never stores API keys or signed URLs; only evidence hashes and
timestamps. The same ``voice_id`` must be reused across every performance
segment of one production (A6).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .providers.base import MINIMAX_HD_MODEL, PROVIDER_MINIMAX

VOICE_STRATEGY_CLONED = "cloned_voice"
VOICE_STRATEGY_SYSTEM = "system_voice"
VOICE_STRATEGIES = {VOICE_STRATEGY_CLONED, VOICE_STRATEGY_SYSTEM}
# MiniMax documented window: cloned voices not used within ~7 days may be
# deleted. This is an UNUSED-activation window, not an absolute expiry.
CLONE_UNUSED_WINDOW_HOURS = 168

_ALLOWED_SOURCE_EXT = {".mp3", ".m4a", ".wav"}
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MIN_SOURCE_SECONDS = 10.0
_MAX_SOURCE_SECONDS = 5 * 60.0
_PROMPT_MAX_SECONDS = 8.0

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._():-]{0,127}$")


class VoiceFoundationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceFoundation:
    schema_version: str
    release_id: str
    provider: str
    voice_strategy: str
    voice_id: str
    model_family: str
    cloned_at: str | None = None
    last_used_at: str | None = None
    unused_activation_window_hours: int = CLONE_UNUSED_WINDOW_HOURS
    source_audio_sha256: str | None = None
    prompt_audio_sha256: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "provider": self.provider,
            "voice_strategy": self.voice_strategy,
            "voice_id": self.voice_id,
            "model_family": self.model_family,
            "cloned_at": self.cloned_at,
            "last_used_at": self.last_used_at,
            "unused_activation_window_hours": self.unused_activation_window_hours,
            "source_audio_sha256": self.source_audio_sha256,
            "prompt_audio_sha256": self.prompt_audio_sha256,
            "evidence": dict(self.evidence),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_source_audio(
    source: Path,
    *,
    prompt: bool = False,
) -> dict[str, Any]:
    target = source.expanduser().resolve()
    if not target.is_file() or target.stat().st_size <= 0:
        raise VoiceFoundationError(f"voice source is missing or empty: {source}")
    if target.suffix.lower() not in _ALLOWED_SOURCE_EXT:
        raise VoiceFoundationError(
            f"voice source must be MP3/M4A/WAV, got {target.suffix or 'none'}"
        )
    size = target.stat().st_size
    if size > _MAX_SOURCE_BYTES:
        raise VoiceFoundationError("voice source exceeds MiniMax 20MB limit")
    from .media_probe import MediaValidationError, probe_audio
    try:
        probe = probe_audio(target)
    except MediaValidationError as error:
        raise VoiceFoundationError(f"voice source is not decodable audio: {error}") from error
    if prompt:
        if probe.duration > _PROMPT_MAX_SECONDS:
            raise VoiceFoundationError(
                f"prompt audio must be <8s, got {probe.duration:.1f}s"
            )
    elif probe.duration < _MIN_SOURCE_SECONDS or probe.duration > _MAX_SOURCE_SECONDS:
        raise VoiceFoundationError(
            f"voice source duration {probe.duration:.1f}s is outside MiniMax 10s-5min"
        )
    return {
        "source_path": target.name,
        "source_size": size,
        "source_duration": round(probe.duration, 3),
        "source_sample_rate": probe.sample_rate,
        "source_channels": probe.channels,
    }


def build_voice_foundation(
    *,
    release_id: str,
    voice_strategy: str,
    voice_id: str | None = None,
    source_audio: Path | None = None,
    prompt_audio: Path | None = None,
    cloned_at: datetime | None = None,
    last_used_at: datetime | None = None,
    model_family: str = MINIMAX_HD_MODEL,
    extra_evidence: Mapping[str, Any] | None = None,
) -> VoiceFoundation:
    if voice_strategy not in VOICE_STRATEGIES:
        raise VoiceFoundationError("voice_strategy must be cloned_voice or system_voice")
    now = cloned_at or last_used_at or datetime.now(timezone.utc)
    evidence: dict[str, Any] = {"strategy_notes": {}}
    if voice_strategy == VOICE_STRATEGY_CLONED:
        if source_audio is None:
            raise VoiceFoundationError("cloned_voice requires an authorized voice source")
        evidence.update(_validate_source_audio(source_audio))
        evidence["source_audio_sha256"] = _sha256_file(source_audio)
        if prompt_audio is not None:
            evidence.update(_validate_source_audio(prompt_audio, prompt=True))
            evidence["prompt_audio_sha256"] = _sha256_file(prompt_audio)
        if voice_id is None or not _VOICE_ID_RE.fullmatch(voice_id):
            raise VoiceFoundationError("cloned_voice requires a stable MiniMax voice_id")
        cloned_at_iso = now.isoformat()
    else:
        if voice_id is None or not _VOICE_ID_RE.fullmatch(voice_id):
            raise VoiceFoundationError("system_voice requires a MiniMax system voice_id")
        cloned_at_iso = None
    if extra_evidence:
        evidence.update(extra_evidence)
    return VoiceFoundation(
        schema_version="voice-foundation.v1",
        release_id=release_id,
        provider=PROVIDER_MINIMAX,
        voice_strategy=voice_strategy,
        voice_id=voice_id,
        model_family=model_family,
        cloned_at=cloned_at_iso,
        last_used_at=last_used_at.isoformat() if last_used_at is not None else None,
        unused_activation_window_hours=CLONE_UNUSED_WINDOW_HOURS,
        source_audio_sha256=evidence.get("source_audio_sha256"),
        prompt_audio_sha256=evidence.get("prompt_audio_sha256"),
        evidence=evidence,
    )


def check_clone_activation_window(
    foundation: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advisory check for the clone unused-activation window.

    Never rejects a voice on a local timestamp alone (M4B A2): a clone that has
    been used recently is active; a clone not used within the window *may* have
    been deleted by the provider, so real availability must be confirmed via
    Get Voice or the actual synthesis response. The returned status is
    ``active`` or ``warn_unused``.
    """

    if foundation.get("voice_strategy") != VOICE_STRATEGY_CLONED:
        return {"status": "active", "note": "system voice does not use the clone window"}
    window = int(foundation.get("unused_activation_window_hours") or CLONE_UNUSED_WINDOW_HOURS)
    last_used_raw = foundation.get("last_used_at")
    reference_raw = last_used_raw or foundation.get("cloned_at")
    if not isinstance(reference_raw, str) or not reference_raw:
        return {"status": "active", "note": "clone has no activation timestamp yet"}
    try:
        reference = datetime.fromisoformat(reference_raw)
    except ValueError:
        return {"status": "active", "note": "activation timestamp unreadable; verify via provider"}
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_hours = ((now or datetime.now(timezone.utc)) - reference).total_seconds() / 3600
    if age_hours > window:
        return {
            "status": "warn_unused",
            "unused_hours": round(age_hours, 1),
            "window_hours": window,
            "note": (
                "cloned voice has not been used within the provider's "
                f"{window}h window; confirm availability via Get Voice before use"
            ),
        }
    return {
        "status": "active",
        "unused_hours": round(age_hours, 1),
        "window_hours": window,
        "note": "clone used within the provider window",
    }


def mark_voice_used(
    foundation: Mapping[str, Any],
    *,
    used_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a copy of the foundation with ``last_used_at`` updated."""

    updated = dict(foundation)
    updated["last_used_at"] = (used_at or datetime.now(timezone.utc)).isoformat()
    return updated


def foundation_digest(foundation: Mapping[str, Any]) -> str:
    canonical = json.dumps(foundation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
