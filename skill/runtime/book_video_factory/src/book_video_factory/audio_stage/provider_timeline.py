"""Provider timeline rebuild and audio assembly (M4A).

After MiniMax synthesizes every performance segment, this module:

* offsets each chunk's real sentence timestamps by the cumulative audio
  duration -> the provider VTT (the authoritative timing source);
* concatenates the chunk WAVs into one 48k mono audio master;
* restores display captions from the provider VTT using the existing
  spoken->display mapping (control markers never enter captions);
* emits an ``audio_generation_evidence.v1`` document binding every chunk SHA
  and the master SHA.

The legacy Edge VTT is never reused for an expressive production: the new
pipeline fails closed unless the timing source is the provider itself (A9).
"""

from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .captions import restore_display_captions
from .media_probe import VttCue, normalize_text
from .pronunciation import SpokenCompilation
from .providers.base import NarrationChunkResult

TIMING_SOURCE_PROVIDER = "provider"
TIMING_SOURCE_EDGE_LEGACY = "edge_vtt"
MASTER_SAMPLE_RATE = 48000
MASTER_CHANNELS = 1
MASTER_SAMPLE_WIDTH = 2  # 16-bit PCM


class ProviderTimelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderTimeline:
    vtt_path: Path
    cues: tuple[VttCue, ...]
    total_duration: float
    chunk_offsets: tuple[tuple[str, float], ...] = field(default_factory=tuple)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def offset_chunk_timestamps(
    chunk_results: Sequence[NarrationChunkResult],
) -> tuple[list[VttCue], list[tuple[str, float]]]:
    """Offset every chunk's real sentence timestamps into one master timeline.

    Returns ``(cues, chunk_offsets)`` where offsets are ``(chunk_id, start)``.
    Raises on non-monotonic or overlapping provider timing.
    """

    if not chunk_results:
        raise ProviderTimelineError("provider timeline requires at least one chunk")
    cues: list[VttCue] = []
    offsets: list[tuple[str, float]] = []
    cursor = 0.0
    for chunk in chunk_results:
        if not chunk.subtitle_timestamps:
            raise ProviderTimelineError(f"chunk {chunk.chunk_id!r} has no provider timestamps")
        chunk_start = cursor
        for index, item in enumerate(chunk.subtitle_timestamps):
            start = float(item["start"]) + cursor
            end = float(item["end"]) + cursor
            text = str(item["text"]).strip()
            if end <= start or start < cursor:
                raise ProviderTimelineError(
                    f"chunk {chunk.chunk_id!r} timestamp {index} is non-monotonic"
                )
            cues.append(VttCue(start=round(start, 3), end=round(end, 3), text=text))
        if chunk.duration <= 0:
            raise ProviderTimelineError(f"chunk {chunk.chunk_id!r} has invalid duration")
        cursor += chunk.duration
        offsets.append((chunk.chunk_id, round(chunk_start, 3)))
    return cues, offsets


def write_provider_vtt(
    chunk_results: Sequence[NarrationChunkResult],
    output_path: Path,
) -> tuple[Path, list[VttCue], float]:
    """Write the provider WEBVTT from real chunk timestamps."""

    cues, _offsets = offset_chunk_timestamps(chunk_results)
    target = output_path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, start=1):
        lines.append(f"{_vtt_time(cue.start)} --> {_vtt_time(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    total = cues[-1].end if cues else 0.0
    return target, cues, round(total, 3)


def _vtt_time(value: float) -> str:
    millis = int(round(value * 1000))
    hours, rem = divmod(millis, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _resample_pcm16_mono(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample concatenated mono s16le PCM to the documented 48k master rate.

    The China T2A platform does not accept 48000 Hz sample rates, so chunks
    arrive at e.g. 44100 Hz and are normalized here (ffmpeg is an existing
    project dependency).
    """
    import subprocess
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(src_rate), "-ac", "1", "-i", "-",
            "-f", "s16le", "-ar", str(dst_rate), "-ac", "1", "-",
        ],
        input=data,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProviderTimelineError(
            f"audio master resample {src_rate}->{dst_rate} failed: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()[:300]}"
        )
    return completed.stdout


def concat_audio_master(
    chunk_results: Sequence[NarrationChunkResult],
    evidence_dir: Path,
    *,
    audio_dir: Path | None = None,
    name: str = "narration_master",
) -> dict[str, Any]:
    """Concatenate chunk WAVs (48k mono 16-bit) into one audio master."""

    if not chunk_results:
        raise ProviderTimelineError("audio master requires at least one chunk")
    output_dir = evidence_dir.expanduser().resolve()
    source_dir = (audio_dir or evidence_dir).expanduser().resolve()
    frames: list[bytes] = []
    params: tuple[int, int, int] | None = None
    for chunk in chunk_results:
        audio_path = source_dir / chunk.audio_path
        if not audio_path.is_file():
            raise ProviderTimelineError(f"chunk audio is missing: {audio_path}")
        try:
            with wave.open(str(audio_path), "rb") as handle:
                chunk_params = (handle.getframerate(), handle.getsampwidth(), handle.getnchannels())
                if params is None:
                    params = chunk_params
                elif params != chunk_params:
                    raise ProviderTimelineError(
                        f"chunk {chunk.chunk_id!r} PCM params {chunk_params} differ from master {params}"
                    )
                frames.append(handle.readframes(handle.getnframes()))
        except (wave.Error, OSError) as error:
            raise ProviderTimelineError(f"chunk {chunk.chunk_id!r} is not a readable WAV: {error}") from error
    if params is None:
        raise ProviderTimelineError("audio master has no PCM params")
    framerate, sampwidth, channels = params
    if sampwidth != MASTER_SAMPLE_WIDTH or channels != MASTER_CHANNELS:
        raise ProviderTimelineError(
            f"audio master requires mono 16-bit PCM, got {channels}ch {sampwidth * 8}bit"
        )
    body = b"".join(frames)
    if framerate != MASTER_SAMPLE_RATE:
        body = _resample_pcm16_mono(body, framerate, MASTER_SAMPLE_RATE)
        framerate = MASTER_SAMPLE_RATE
    target = output_dir / f"{name}.wav"
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(framerate)
        handle.writeframes(body)
    duration = len(body) / (framerate * sampwidth * channels)
    # The master digest is the SHA-256 of the full file bytes (same semantics
    # as manifests.sha256_file used everywhere else in the repo).
    master_sha = _sha256_bytes(target.read_bytes())
    return {
        "path": target.relative_to(output_dir).as_posix(),
        "sha256": master_sha,
        "duration": round(duration, 3),
        "sample_rate": framerate,
        "channels": channels,
        "sample_width": sampwidth,
    }


def restore_display_captions_from_provider(
    chunk_results: Sequence[NarrationChunkResult],
    compilation: SpokenCompilation,
    *,
    min_chars: int,
    max_chars: int,
    min_duration: float,
    section_register: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    """Restore display captions from the provider VTT (control-marker free)."""

    cues, _ = offset_chunk_timestamps(chunk_results)
    coalesce_target_chars = min(max_chars, 12) if all(
        len(normalize_text(cue.text)) <= 1 for cue in cues
    ) else None
    return restore_display_captions(
        cues,
        compilation,
        min_chars=min_chars,
        max_chars=max_chars,
        min_duration=min_duration,
        section_register=section_register,
        coalesce_target_chars=coalesce_target_chars,
    )


def build_audio_generation_evidence(
    *,
    chunk_results: Sequence[NarrationChunkResult],
    master: Mapping[str, Any],
    vtt_path: Path,
    vtt_sha256: str,
    release_id: str,
    variant: str,
    voice_id: str,
    model: str,
) -> dict[str, Any]:
    """Assemble the ``audio_generation_evidence.v1`` document (all SHA bound)."""

    chunks = []
    for chunk in chunk_results:
        chunks.append({
            "chunk_id": chunk.chunk_id,
            "provider": chunk.provider,
            "model": chunk.model,
            "voice_id": chunk.voice_id,
            "audio_path": chunk.audio_path,
            "audio_sha256": chunk.audio_sha256,
            "duration": chunk.duration,
            "trace_id": chunk.trace_id,
            "request_digest": chunk.request_digest,
            "subtitle_granularity": chunk.subtitle_granularity,
            "subtitle_timestamps": list(chunk.subtitle_timestamps),
        })
    return {
        "schema_version": "audio-generation-evidence.v1",
        "release_id": release_id,
        "variant": variant,
        "voice_id": voice_id,
        "model": model,
        "timing_source": TIMING_SOURCE_PROVIDER,
        "chunks": chunks,
        "master": dict(master),
        "provider_vtt": {
            "path": vtt_path.as_posix(),
            "sha256": vtt_sha256,
        },
    }


def assert_provider_timeline_authority(evidence: Mapping[str, Any]) -> None:
    """Fail closed when timing is not provider-authoritative (A9).

    The new expressive pipeline must never reuse a legacy Edge VTT: provider
    timestamps plus real audio hashes are required, and an explicit
    ``edge_vtt`` timing source is rejected.
    """

    if not isinstance(evidence, Mapping):
        raise ProviderTimelineError("audio generation evidence must be an object")
    timing_source = evidence.get("timing_source")
    if timing_source == TIMING_SOURCE_EDGE_LEGACY:
        raise ProviderTimelineError(
            "legacy Edge VTT is not reusable for an expressive production; "
            "provider timestamps are the only valid timing source"
        )
    if timing_source != TIMING_SOURCE_PROVIDER:
        raise ProviderTimelineError(
            f"audio generation evidence timing_source must be {TIMING_SOURCE_PROVIDER!r}"
        )
    chunks = evidence.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ProviderTimelineError("audio generation evidence has no chunks")
    master = evidence.get("master")
    if not isinstance(master, Mapping) or not master.get("sha256"):
        raise ProviderTimelineError("audio generation evidence has no master sha256")
    for chunk in chunks:
        if not isinstance(chunk, Mapping) or not chunk.get("audio_sha256"):
            raise ProviderTimelineError("audio generation evidence chunk has no audio_sha256")
        if not chunk.get("subtitle_timestamps"):
            raise ProviderTimelineError("audio generation evidence chunk has no provider timestamps")


def evidence_digest(evidence: Mapping[str, Any]) -> str:
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
