"""V2 audio timeline and autonomous BGM selection (fail-closed, provider-only)."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_audio_generation_evidence,
)
from book_video_factory.audio_stage.provider_timeline import (
    ProviderTimelineError,
    assert_provider_timeline_authority,
)
from book_video_factory.manifests import safe_project_output, sha256_file


class AudioTimelineError(RuntimeError):
    """V2 audio timeline or BGM selection is missing, tampered, or out of bounds."""


TIMELINE_REL = "04_audio/AUDIO_TIMELINE.v2.json"
MIX_MANIFEST_REL = "04_audio/AUDIO_MIX_MANIFEST.json"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TRUE_PEAK_LIMIT_DBTP = -3.0


ProbeRunner = Callable[[Path, float | None], dict[str, float]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_object(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise AudioTimelineError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioTimelineError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise AudioTimelineError(f"{label} must be a JSON object")
    return value


def _media(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise AudioTimelineError(f"{label} path is invalid")
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise AudioTimelineError(f"{label} is missing, empty, or symlinked: {relative}")
    return path


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise AudioTimelineError(f"{label} is not a finite number")
    return float(value)


def _normalized_probe(value: dict[str, Any], label: str) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"duration_seconds", "integrated_lufs", "true_peak_dbtp"}:
        raise AudioTimelineError(f"{label} fields are invalid")
    return {
        key: round(_number(item, f"{label}.{key}"), 6)
        for key, item in value.items()
    }


def _probe_media(path: Path, seconds: float | None) -> dict[str, float]:
    """Probe a media file for duration, integrated loudness, and true peak."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise AudioTimelineError(probe.stderr.strip() or f"ffprobe failed for {path}")
    try:
        source_duration = float(probe.stdout.strip())
    except ValueError as error:
        raise AudioTimelineError(f"ffprobe returned an invalid duration for {path}") from error
    command = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if seconds is not None:
        command.extend(("-t", f"{seconds:g}"))
    command.extend(("-filter_complex", "ebur128=peak=true", "-f", "null", os.devnull))
    measured = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if measured.returncode != 0:
        raise AudioTimelineError(measured.stderr.strip() or f"ffmpeg ebur128 failed for {path}")
    integrated = re.findall(r"\bI:\s*(-?(?:inf|\d+(?:\.\d+)?))\s+LUFS", measured.stderr, re.IGNORECASE)
    peaks = re.findall(r"\bPeak:\s*(-?(?:inf|\d+(?:\.\d+)?))\s+dB(?:FS|TP)?", measured.stderr, re.IGNORECASE)
    if not integrated or not peaks or integrated[-1].casefold() == "-inf" or peaks[-1].casefold() == "-inf":
        raise AudioTimelineError(f"ffmpeg did not report finite loudness and true peak for {path}")
    return {
        "duration_seconds": min(source_duration, seconds) if seconds is not None else source_duration,
        "integrated_lufs": float(integrated[-1]),
        "true_peak_dbtp": float(peaks[-1]),
    }


def _selection_score(track: dict[str, Any]) -> float:
    value = track.get("selection_score", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return 0.0
    return float(value)


def _selection_reason(track: dict[str, Any]) -> str:
    explicit = track.get("selection_reason")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return "automatic selection from the cleared licensed library"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_bytes(_pretty(payload))
    os.replace(staging, path)


def select_bgm(
    project: Path,
    *,
    candidates_relative: str = "04_audio/BGM_CANDIDATES.json",
) -> dict[str, Any]:
    """Deterministically select a cleared licensed BGM track or ``none``.

    ``none`` is a legal production result and never blocks. Only corrupt rights
    evidence (a recorded but mismatched hash) raises ``AudioTimelineError``.
    """

    root = project.expanduser().resolve()
    candidates_path = safe_project_output(root, Path(candidates_relative))
    if candidates_path.is_symlink() or not candidates_path.is_file():
        return {"bgm_mode": "none", "selection_reason": "no_bgm_candidates_library"}
    try:
        payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioTimelineError(f"bgm candidates are unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise AudioTimelineError("bgm candidates must be a JSON object")
    if payload.get("schema_version") != "bgm-candidates.v1":
        raise AudioTimelineError("bgm candidates schema_version is invalid")
    release_id = payload.get("release_id")
    if release_id is not None and (not isinstance(release_id, str) or not release_id.strip()):
        raise AudioTimelineError("bgm candidates release_id is invalid")
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        raise AudioTimelineError("bgm candidates tracks must be a list")
    cleared = [
        item
        for item in tracks
        if isinstance(item, dict)
        and item.get("rights_status") == "cleared"
        and isinstance(item.get("path"), str)
        and item["path"]
    ]
    cleared.sort(key=_selection_score, reverse=True)
    for track in cleared:
        path_value = track["path"]
        try:
            track_path = _media(root, path_value, "BGM track")
        except AudioTimelineError:
            continue  # an unusable track degrades, never blocks
        recomputed = sha256_file(track_path)
        recorded = track.get("source_sha256")
        if recorded is not None:
            if not isinstance(recorded, str) or not _SHA.fullmatch(recorded):
                raise AudioTimelineError(f"BGM track {path_value} has an invalid source_sha256 (rights evidence)")
            if recomputed != recorded:
                raise AudioTimelineError(
                    f"BGM track {path_value} source hash is corrupt (rights evidence)"
                )
        ducking = track.get("ducking_db")
        target = track.get("target_lufs")
        ducking = ducking if isinstance(ducking, (int, float)) and not isinstance(ducking, bool) else -13.0
        target = target if isinstance(target, (int, float)) and not isinstance(target, bool) else -24.0
        license_doc = track.get("license")
        if not isinstance(license_doc, dict):
            license_doc = {}
        return {
            "bgm_mode": "library",
            "track_id": str(track.get("track_id") or path_value),
            "source_path": path_value,
            "source_sha256": recomputed,
            "rights_status": "cleared",
            "selection_reason": _selection_reason(track),
            "loop_policy": str(track.get("loop_policy") or "crossfade_loop"),
            "ducking_db": float(ducking),
            "target_lufs": float(target),
            "license": license_doc,
        }
    return {"bgm_mode": "none", "selection_reason": "no_eligible_cleared_track"}


def _build_mix_qa(
    root: Path,
    bgm: dict[str, Any],
    narration_duration: float,
    probe_runner: ProbeRunner | None,
) -> dict[str, Any]:
    if bgm.get("bgm_mode") != "library":
        return {"status": "not_applicable"}
    track_path = _media(root, bgm.get("source_path", ""), "BGM track")
    runner = probe_runner or _probe_media
    source_probe = _normalized_probe(runner(track_path, None), "source probe")
    if source_probe["true_peak_dbtp"] > _TRUE_PEAK_LIMIT_DBTP:
        raise AudioTimelineError("BGM source true peak exceeds the -3 dBTP clipping limit")
    return {
        "status": "ready",
        "source_probe": source_probe,
        "loop_policy": bgm.get("loop_policy"),
        "ducking_db": bgm.get("ducking_db"),
        "target_lufs": bgm.get("target_lufs"),
        "source_shorter_than_narration": source_probe["duration_seconds"] < narration_duration,
        "true_peak_limit_dbtp": _TRUE_PEAK_LIMIT_DBTP,
    }


def build_audio_timeline(
    project: Path,
    *,
    evidence_path: str,
    caption_timeline_path: str,
    probe_runner: ProbeRunner | None = None,
) -> dict[str, Any]:
    """Build the provider-authoritative V2 audio timeline and BGM mix manifest.

    Fails closed on any non-provider timing source, stale master/vtt/caption
    hash, corrupt BGM rights evidence, or a hot BGM true peak.
    """

    root = project.expanduser().resolve()
    evidence = _load_object(root, evidence_path, "audio generation evidence")
    try:
        normalized_evidence = validate_audio_generation_evidence(evidence)
        assert_provider_timeline_authority(normalized_evidence)
    except (AudioStageContractError, ProviderTimelineError, ValueError) as error:
        raise AudioTimelineError(f"audio generation evidence is invalid: {error}") from error
    release_id = normalized_evidence["release_id"]

    master = _media(root, normalized_evidence["master"]["path"], "narration master")
    if sha256_file(master) != normalized_evidence["master"]["sha256"]:
        raise AudioTimelineError("narration master sha256 is stale")
    vtt = _media(root, normalized_evidence["provider_vtt"]["path"], "provider VTT")
    if sha256_file(vtt) != normalized_evidence["provider_vtt"]["sha256"]:
        raise AudioTimelineError("provider VTT sha256 is stale")
    caption = _media(root, caption_timeline_path, "caption timeline")
    caption_sha = sha256_file(caption)

    bgm = select_bgm(root)
    narration_duration = _number(normalized_evidence["master"]["duration"], "master duration")
    mix_qa = _build_mix_qa(root, bgm, narration_duration, probe_runner)

    master_rel = normalized_evidence["master"]["path"]
    vtt_rel = normalized_evidence["provider_vtt"]["path"]
    timeline = {
        "schema_version": "audio-timeline.v2",
        "release_id": release_id,
        "project_id": root.name,
        "narration_master_path": master_rel,
        "narration_master_sha256": normalized_evidence["master"]["sha256"],
        "provider_vtt_path": vtt_rel,
        "provider_vtt_sha256": normalized_evidence["provider_vtt"]["sha256"],
        "caption_timeline_path": caption_timeline_path,
        "caption_timeline_sha256": caption_sha,
        "timing_authority": "provider",
        "narration_duration_seconds": narration_duration,
        "bgm": bgm,
    }
    timeline_path = safe_project_output(root, Path(TIMELINE_REL))
    _atomic_json(timeline_path, timeline)

    mix_manifest = {
        "schema_version": "audio-mix-manifest.v1",
        "release_id": release_id,
        "project_id": root.name,
        "narration_master_path": master_rel,
        "narration_master_sha256": normalized_evidence["master"]["sha256"],
        "timing_authority": "provider",
        "bgm_mode": bgm["bgm_mode"],
        "bgm": bgm,
        "mix_qa": mix_qa,
    }
    mix_path = safe_project_output(root, Path(MIX_MANIFEST_REL))
    _atomic_json(mix_path, mix_manifest)
    return {
        "status": "audio_timeline_ready",
        "release_id": release_id,
        "timeline_path": TIMELINE_REL,
        "mix_manifest_path": MIX_MANIFEST_REL,
        "bgm_mode": bgm["bgm_mode"],
        "mix_qa_status": mix_qa["status"],
        "timing_authority": "provider",
    }
