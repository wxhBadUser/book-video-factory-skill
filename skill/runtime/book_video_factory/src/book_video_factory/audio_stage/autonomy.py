"""Runtime-owned V2 narration execution and evidence materialization."""

from __future__ import annotations

import json
import os
import re
import wave
from pathlib import Path
from typing import Any, Sequence

from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.audio_stage.providers.base import NarrationChunkRequest, NarrationChunkResult
from book_video_factory.visual_covenant import verify_asset_catalog, verify_visual_covenant_approval


class AudioAutonomyError(RuntimeError):
    """Narration evidence is missing, undecodable, or inconsistent."""


EVIDENCE_REL = "04_audio/AUDIO_GENERATION_EVIDENCE.v1.json"
TIMELINE_REL = "04_audio/AUDIO_TIMELINE.v2.json"
MASTER_REL = "04_audio/narration_master.wav"
VTT_REL = "04_audio/PROVIDER.vtt"
CAPTIONS_REL = "04_audio/CAPTION_TIMELINE.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _chunk_path(root: Path, evidence_dir: Path, result: NarrationChunkResult) -> Path:
    candidate = Path(result.audio_path)
    path = candidate if candidate.is_absolute() else evidence_dir / candidate
    try:
        path = path.resolve()
        path.relative_to(root.resolve())
    except ValueError as error:
        raise AudioAutonomyError(f"narration chunk escapes project: {result.audio_path}") from error
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise AudioAutonomyError(f"narration chunk is missing or empty: {result.audio_path}")
    if sha256_file(path) != result.audio_sha256:
        raise AudioAutonomyError(f"narration chunk sha256 is stale: {result.chunk_id}")
    return path


def _read_wav(path: Path) -> tuple[dict[str, int], bytes, float]:
    try:
        with wave.open(str(path), "rb") as handle:
            params = {
                "channels": handle.getnchannels(),
                "sample_width": handle.getsampwidth(),
                "sample_rate": handle.getframerate(),
            }
            frames = handle.readframes(handle.getnframes())
            duration = handle.getnframes() / float(handle.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as error:
        raise AudioAutonomyError(f"narration chunk is not a decodable WAV: {path}") from error
    if not frames or min(params.values()) < 1:
        raise AudioAutonomyError(f"narration chunk has no decodable frames: {path}")
    return params, frames, duration


def _timestamp(entry: dict[str, Any], name: str) -> float:
    value = entry.get(name)
    if value is None and name == "start":
        value = entry.get("start_time")
    if value is None and name == "end":
        value = entry.get("end_time")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioAutonomyError(f"provider timestamp {name} is invalid")
    return float(value)


def _caption_rows(results: Sequence[NarrationChunkResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0.0
    index = 0
    for result in results:
        entries = list(result.subtitle_timestamps)
        if not entries:
            raise AudioAutonomyError(f"provider returned no timestamps for {result.chunk_id}")
        for raw in entries:
            if not isinstance(raw, dict):
                raise AudioAutonomyError(f"provider timestamp is not an object: {result.chunk_id}")
            start = offset + _timestamp(raw, "start")
            end = offset + _timestamp(raw, "end")
            text = raw.get("text")
            if end <= start or not isinstance(text, str) or not text.strip():
                raise AudioAutonomyError(f"provider timestamp is invalid: {result.chunk_id}")
            rows.append({"index": index, "start": start, "end": end, "text": text.strip()})
            index += 1
        offset += float(result.duration)
    return rows


def _vtt(rows: Sequence[dict[str, Any]]) -> str:
    def stamp(seconds: float) -> str:
        millis = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(millis, 3_600_000)
        minutes, millis = divmod(remainder, 60_000)
        seconds, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    lines = ["WEBVTT", ""]
    for row in rows:
        lines.extend([str(row["index"] + 1), f"{stamp(row['start'])} --> {stamp(row['end'])}", row["text"], ""])
    return "\n".join(lines)


def generate_audio_timeline_v2(
    project: Path,
    provider: Any,
    requests: Sequence[NarrationChunkRequest],
) -> dict[str, Any]:
    """Synthesize chunks through the provider boundary and emit real V2 evidence."""
    root = project.expanduser().resolve()
    lock = verify_locked_script(root)
    if lock.get("status") != "script_locked":
        raise AudioAutonomyError(f"locked script is not verified: {lock.get('status')}")
    if not requests:
        raise AudioAutonomyError("at least one narration request is required")
    evidence_dir = safe_project_output(root, Path("04_audio/chunks"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: list[NarrationChunkResult] = []
    for request in requests:
        try:
            result = provider.synthesize(request, evidence_dir=evidence_dir)
        except TypeError:
            result = provider.synthesize(request)
        if not isinstance(result, NarrationChunkResult):
            raise AudioAutonomyError(f"provider returned an invalid result for {request.chunk_id}")
        _chunk_path(root, evidence_dir, result)
        results.append(result)

    params: dict[str, int] | None = None
    frames: list[bytes] = []
    actual_duration = 0.0
    chunk_records: list[dict[str, Any]] = []
    for result in results:
        path = _chunk_path(root, evidence_dir, result)
        current, data, duration = _read_wav(path)
        if params is None:
            params = current
        elif current != params:
            raise AudioAutonomyError("narration chunks do not share WAV parameters")
        frames.append(data)
        actual_duration += duration
        chunk_records.append({
            "chunk_id": result.chunk_id,
            "provider": result.provider,
            "model": result.model,
            "voice_id": result.voice_id,
            "audio_path": _relative(root, path),
            "audio_sha256": result.audio_sha256,
            "duration": duration,
            "trace_id": result.trace_id,
            "request_digest": result.request_digest,
            "subtitle_timestamps": [
                {
                    "index": int(item.get("index", index)),
                    "start": float(item.get("start", item.get("start_time", 0.0))),
                    "end": float(item.get("end", item.get("end_time", 0.0))),
                    "text": str(item.get("text", "")),
                }
                for index, item in enumerate(result.subtitle_timestamps)
            ],
            "subtitle_granularity": result.subtitle_granularity,
        })

    assert params is not None
    master = safe_project_output(root, Path(MASTER_REL))
    master.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(master), "wb") as handle:
        handle.setnchannels(params["channels"])
        handle.setsampwidth(params["sample_width"])
        handle.setframerate(params["sample_rate"])
        handle.writeframes(b"".join(frames))

    rows = _caption_rows(results)
    provider_vtt = safe_project_output(root, Path(VTT_REL))
    provider_vtt.write_text(_vtt(rows), encoding="utf-8")
    captions = safe_project_output(root, Path(CAPTIONS_REL))
    _atomic_json(captions, {"schema_version": "caption-timeline.v2", "captions": rows})

    evidence = {
        "schema_version": "audio-generation-evidence.v1",
        "release_id": lock["release_id"],
        "variant": "autonomy-v2",
        "voice_id": results[0].voice_id,
        "model": results[0].model,
        "timing_source": "provider",
        "chunks": chunk_records,
        "master": {
            "path": MASTER_REL,
            "sha256": sha256_file(master),
            "duration": actual_duration,
            "sample_rate": params["sample_rate"],
            "channels": params["channels"],
            "sample_width": params["sample_width"],
        },
        "provider_vtt": {"path": VTT_REL, "sha256": sha256_file(provider_vtt)},
    }
    evidence_path = safe_project_output(root, Path(EVIDENCE_REL))
    _atomic_json(evidence_path, evidence)
    timeline = {
        "schema_version": "audio-timeline.v2",
        "release_id": lock["release_id"],
        "project_id": root.name,
        "narration_master_path": MASTER_REL,
        "narration_master_sha256": sha256_file(master),
        "provider_vtt_path": VTT_REL,
        "provider_vtt_sha256": sha256_file(provider_vtt),
        "caption_timeline_path": CAPTIONS_REL,
        "caption_timeline_sha256": sha256_file(captions),
        "timing_authority": "provider",
        "narration_duration_seconds": actual_duration,
        "bgm": {"bgm_mode": "none"},
    }
    _atomic_json(safe_project_output(root, Path(TIMELINE_REL)), timeline)
    return {"status": "audio_timeline_ready", "timeline_path": TIMELINE_REL, "duration": actual_duration}


def _provider_value(provider: Any, public_name: str, private_name: str, default: str) -> str:
    value = getattr(provider, public_name, None)
    if not isinstance(value, str) or not value:
        value = getattr(provider, private_name, None)
    return value if isinstance(value, str) and value else default


def _locked_script_requests(root: Path, lock: dict[str, Any], provider: Any) -> list[NarrationChunkRequest]:
    script_path = safe_project_output(root, Path(str(lock["script_path"])))
    try:
        text = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AudioAutonomyError(f"locked script cannot be read for narration: {error}") from error
    units = [unit.strip() for unit in re.split(r"\n\s*\n", text) if unit.strip()]
    if not units:
        raise AudioAutonomyError("locked script contains no narration text")
    provider_id = _provider_value(provider, "provider_id", "provider_id", "provider-neutral")
    model = _provider_value(provider, "model", "_model", "provider-neutral-model")
    voice_id = _provider_value(provider, "voice_id", "_voice_id", "provider-neutral-voice")
    return [
        NarrationChunkRequest(
            chunk_id=f"LOCKED_SCRIPT_{index:04d}",
            text=unit,
            provider=provider_id,
            model=model,
            voice_id=voice_id,
            extra={"locked_script_sha256": lock["script_sha256"], "unit_index": index},
        )
        for index, unit in enumerate(units, start=1)
    ]


def run_narration_from_locked_script(project: Path, provider: Any) -> dict[str, Any]:
    """Build provider-neutral narration requests from the locked script itself."""
    root = project.expanduser().resolve()
    lock = verify_locked_script(root)
    if lock.get("status") != "script_locked":
        raise AudioAutonomyError(f"locked script is not verified: {lock.get('status')}")
    approval = verify_visual_covenant_approval(root)
    if approval.get("status") != "visual_covenant_approval_verified":
        raise AudioAutonomyError(f"visual covenant approval is not verified: {approval.get('status')}")
    catalog = verify_asset_catalog(root)
    if catalog.get("status") != "asset_catalog_verified":
        raise AudioAutonomyError(f"asset catalog is not verified: {catalog.get('status')}")
    requests = _locked_script_requests(root, lock, provider)
    preflight = getattr(provider, "preflight", None)
    close = getattr(provider, "close", None)
    if callable(preflight):
        preflight()
    try:
        return generate_audio_timeline_v2(root, provider, requests)
    finally:
        if callable(close):
            close()
