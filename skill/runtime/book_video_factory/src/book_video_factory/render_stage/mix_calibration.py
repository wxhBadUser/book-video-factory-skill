from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from book_video_factory.manifests import record_approval, safe_project_output, sha256_file
from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root


class MixCalibrationError(RuntimeError):
    """The fixed opening BGM calibration or its human approval is invalid."""


@dataclass(frozen=True)
class MixCalibrationResult:
    status: str
    calibration_path: Path
    next_stage_status: str


@dataclass(frozen=True)
class MixApprovalResult:
    status: str
    approval_path: Path
    next_stage_status: str


ProbeRunner = Callable[[Path, float | None], dict[str, float]]

_CALIBRATION_FIELDS = {
    "schema_version", "calibration_id", "release_id", "render_input_path", "render_input_sha256",
    "bgm_source_path", "bgm_source_sha256", "opening_preview_manifest_sha256",
    "preview_path", "preview_sha256", "hbg_style_sha256", "mix_mode", "ducking",
    "gain_linear", "gain_db", "gain_step_db", "source_probe", "source_first_20_seconds_probe",
    "preview_duration_seconds", "preview_integrated_lufs", "encoded_true_peak_dbtp",
    "true_peak_limit_dbtp", "recorded_at", "next_stage_status",
}
_APPROVAL_FIELDS = {
    "schema_version", "release_id", "reviewer", "note", "calibration_path", "calibration_sha256",
    "preview_path", "preview_sha256", "bgm_source_path", "bgm_source_sha256", "hbg_style_sha256",
    "gain_linear", "gain_db", "approval_event_path", "approval_event_sha256", "human_approved",
    "next_stage_status",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MixCalibrationError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MixCalibrationError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise MixCalibrationError(f"{label} must be an object")
    return value


def _media(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise MixCalibrationError(f"{label} path is invalid")
    try:
        path = safe_project_output(root, Path(relative))
    except (OSError, ValueError) as error:
        raise MixCalibrationError(f"{label} path is invalid: {error}") from error
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise MixCalibrationError(f"{label} is missing, empty, or symlinked")
    return path


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise MixCalibrationError(f"{label} is not a finite number")
    return float(value)


def _probe_media(path: Path, seconds: float | None) -> dict[str, float]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise MixCalibrationError(probe.stderr.strip() or f"ffprobe failed for {path}")
    try:
        source_duration = float(probe.stdout.strip())
    except ValueError as error:
        raise MixCalibrationError(f"ffprobe returned an invalid duration for {path}") from error
    command = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if seconds is not None:
        command.extend(("-t", f"{seconds:g}"))
    command.extend(("-filter_complex", "ebur128=peak=true", "-f", "null", os.devnull))
    measured = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if measured.returncode != 0:
        raise MixCalibrationError(measured.stderr.strip() or f"ffmpeg ebur128 failed for {path}")
    integrated = re.findall(r"\bI:\s*(-?(?:inf|\d+(?:\.\d+)?))\s+LUFS", measured.stderr, re.IGNORECASE)
    peaks = re.findall(r"\bPeak:\s*(-?(?:inf|\d+(?:\.\d+)?))\s+dB(?:FS|TP)?", measured.stderr, re.IGNORECASE)
    if not integrated or not peaks or integrated[-1].casefold() == "-inf" or peaks[-1].casefold() == "-inf":
        raise MixCalibrationError(f"ffmpeg did not report finite loudness and true peak for {path}")
    return {
        "duration_seconds": min(source_duration, seconds) if seconds is not None else source_duration,
        "integrated_lufs": float(integrated[-1]),
        "true_peak_dbtp": float(peaks[-1]),
    }


def _normalized_probe(value: dict[str, float], label: str) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"duration_seconds", "integrated_lufs", "true_peak_dbtp"}:
        raise MixCalibrationError(f"{label} fields are invalid")
    return {key: round(_number(item, f"{label}.{key}"), 6) for key, item in value.items()}


def _current_inputs(root: Path, input_path: Path) -> tuple[dict[str, Any], Path, Path, Path, dict[str, Any], float]:
    render_input = _load(input_path, "render stage input")
    if render_input.get("schema_version") != "render-stage-input.v1":
        raise MixCalibrationError("render stage input schema is invalid")
    release_id = render_input.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise MixCalibrationError("render stage release is invalid")
    bgm = _media(root, render_input.get("bgm_source"), "BGM source")
    opening = render_input.get("opening")
    preview_relative = opening.get("preview_video") if isinstance(opening, dict) else None
    preview = _media(root, preview_relative, "HBG opening preview")
    preview_manifest_path = root / "07_render/OPENING_PREVIEW_MANIFEST.json"
    preview_manifest = _load(preview_manifest_path, "opening preview manifest")
    expected_preview_fields = {
        "schema_version", "release_id", "render_input_sha256", "scene_approval_sha256",
        "audio_stage_sha256", "hbg_style_sha256", "project_spec_sha256", "preview_path",
        "preview_sha256", "preview_bytes", "renderer", "hbg_vendor_lock_sha256", "next_stage_status",
    }
    if (
        set(preview_manifest) != expected_preview_fields
        or preview_manifest.get("schema_version") != "opening-preview-manifest.v1"
        or preview_manifest.get("release_id") != release_id
        or preview_manifest.get("renderer") != "hbg-hyperframes-opening-preview"
        or preview_manifest.get("preview_path") != preview_relative
        or preview_manifest.get("preview_sha256") != sha256_file(preview)
        or preview_manifest.get("preview_bytes") != preview.stat().st_size
        or preview_manifest.get("render_input_sha256") != sha256_file(input_path)
        or preview_manifest.get("next_stage_status") != "awaiting_opening_mix_calibration"
    ):
        raise MixCalibrationError("HBG opening preview evidence is stale or invalid")
    style_path = root / "HBG_STYLE.json"
    style = _load(style_path, "HBG style")
    if preview_manifest.get("hbg_style_sha256") != sha256_file(style_path):
        raise MixCalibrationError("HBG opening preview style hash is stale")
    current_bindings = {
        "scene_approval_sha256": root / "06_visual_production/SCENE_ASSET_APPROVAL.json",
        "audio_stage_sha256": root / "04_audio/AUDIO_STAGE_MANIFEST.json",
        "project_spec_sha256": root / "PROJECT_SPEC.json",
        "hbg_vendor_lock_sha256": repository_root() / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json",
    }
    try:
        _verify_vendor(repository_root())
    except RuntimeError as error:
        raise MixCalibrationError(f"HBG vendor integrity failed: {error}") from error
    for key, binding_path in current_bindings.items():
        if binding_path.is_symlink() or not binding_path.is_file() or preview_manifest.get(key) != sha256_file(binding_path):
            raise MixCalibrationError(f"HBG opening preview binding is stale: {key}")
    audio = style.get("audio")
    gain = audio.get("bgmVolume") if isinstance(audio, dict) else None
    gain = _number(gain, "HBG style audio.bgmVolume")
    if not 0.0 < gain <= 1.0:
        raise MixCalibrationError("HBG fixed BGM gain must be greater than zero and at most one")
    return render_input, bgm, preview, style_path, preview_manifest, gain


def calibrate_opening_mix(
    project: Path,
    input_path: Path,
    *,
    probe_runner: ProbeRunner | None = None,
) -> MixCalibrationResult:
    root = project.expanduser().resolve()
    resolved_input = input_path.expanduser().resolve()
    try:
        resolved_input.relative_to(root)
    except ValueError as error:
        raise MixCalibrationError("render stage input must be inside the project") from error
    render_input, bgm, preview, style_path, preview_manifest, gain = _current_inputs(root, resolved_input)
    runner = probe_runner or _probe_media
    source_probe = _normalized_probe(runner(bgm, None), "source probe")
    first_twenty = _normalized_probe(runner(bgm, 20.0), "source first 20 seconds probe")
    preview_probe = _normalized_probe(runner(preview, None), "opening preview probe")
    duration = preview_probe["duration_seconds"]
    peak = preview_probe["true_peak_dbtp"]
    if not 15.0 <= duration <= 20.0:
        raise MixCalibrationError("HBG opening mix preview duration must be between 15 and 20 seconds")
    if peak > -3.0:
        raise MixCalibrationError("encoded opening mix true peak must not exceed -3 dBTP")
    gain_db = round(20.0 * math.log10(gain), 6)

    history_dir = safe_project_output(root, Path("07_render/mix-calibrations"))
    prior_records: list[dict[str, Any]] = []
    if history_dir.exists():
        if history_dir.is_symlink() or not history_dir.is_dir():
            raise MixCalibrationError("mix calibration history path is invalid")
        for path in history_dir.glob("*.json"):
            item = _load(path, "mix calibration history")
            if set(item) != _CALIBRATION_FIELDS or item.get("schema_version") != "mix-calibration.v1":
                raise MixCalibrationError("mix calibration history contains an invalid record")
            if item.get("release_id") == render_input["release_id"] and item.get("bgm_source_path") == render_input["bgm_source"]:
                prior_records.append(item)
    prior_records.sort(key=lambda item: str(item.get("recorded_at", "")))
    step: float | None = None
    if prior_records:
        previous_gain = _number(prior_records[-1].get("gain_db"), "previous mix gain")
        difference = round(abs(gain_db - previous_gain), 6)
        if difference != 0.0 and not 3.0 <= difference <= 4.0:
            raise MixCalibrationError("each changed BGM gain step must be between 3 and 4 dB")
        step = difference

    core = {
        "schema_version": "mix-calibration.v1",
        "release_id": render_input["release_id"],
        "render_input_path": resolved_input.relative_to(root).as_posix(),
        "render_input_sha256": sha256_file(resolved_input),
        "bgm_source_path": render_input["bgm_source"],
        "bgm_source_sha256": sha256_file(bgm),
        "opening_preview_manifest_sha256": sha256_file(root / "07_render/OPENING_PREVIEW_MANIFEST.json"),
        "preview_path": preview.relative_to(root).as_posix(),
        "preview_sha256": sha256_file(preview),
        "hbg_style_sha256": sha256_file(style_path),
        "mix_mode": "fixed",
        "ducking": False,
        "gain_linear": gain,
        "gain_db": gain_db,
        "gain_step_db": step,
        "source_probe": source_probe,
        "source_first_20_seconds_probe": first_twenty,
        "preview_duration_seconds": duration,
        "preview_integrated_lufs": preview_probe["integrated_lufs"],
        "encoded_true_peak_dbtp": peak,
        "true_peak_limit_dbtp": -3.0,
        "next_stage_status": "awaiting_opening_mix_approval",
    }
    identifier = hashlib.sha256(_canonical(core)).hexdigest()[:20]
    calibration = {**core, "calibration_id": identifier, "recorded_at": _now()}
    path = safe_project_output(root, Path(f"07_render/mix-calibrations/{identifier}.json"))
    if path.exists():
        existing = _load(path, "mix calibration")
        if any(existing.get(key) != value for key, value in core.items()) or existing.get("calibration_id") != identifier:
            raise MixCalibrationError("existing mix calibration is stale or modified")
        return MixCalibrationResult("unchanged", path, existing["next_stage_status"])
    history_dir.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(calibration, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
    except OSError as error:
        raise MixCalibrationError(f"mix calibration could not be published: {error}") from error
    return MixCalibrationResult("created", path, "awaiting_opening_mix_approval")


def _verify_calibration(root: Path, input_path: Path, path: Path) -> dict[str, Any]:
    calibration = _load(path, "mix calibration")
    if set(calibration) != _CALIBRATION_FIELDS or calibration.get("schema_version") != "mix-calibration.v1":
        raise MixCalibrationError("mix calibration fields are invalid")
    render_input, bgm, preview, style_path, _preview_manifest, gain = _current_inputs(root, input_path)
    expected = {
        "release_id": render_input["release_id"],
        "render_input_path": input_path.relative_to(root).as_posix(),
        "render_input_sha256": sha256_file(input_path),
        "bgm_source_path": render_input["bgm_source"],
        "bgm_source_sha256": sha256_file(bgm),
        "opening_preview_manifest_sha256": sha256_file(root / "07_render/OPENING_PREVIEW_MANIFEST.json"),
        "preview_path": preview.relative_to(root).as_posix(),
        "preview_sha256": sha256_file(preview),
        "hbg_style_sha256": sha256_file(style_path),
        "mix_mode": "fixed",
        "ducking": False,
        "gain_linear": gain,
        "gain_db": round(20.0 * math.log10(gain), 6),
        "true_peak_limit_dbtp": -3.0,
        "next_stage_status": "awaiting_opening_mix_approval",
    }
    for key, value in expected.items():
        if calibration.get(key) != value:
            raise MixCalibrationError(f"mix calibration is stale: {key}")
    if not 15.0 <= _number(calibration.get("preview_duration_seconds"), "preview duration") <= 20.0:
        raise MixCalibrationError("mix calibration preview duration is invalid")
    if _number(calibration.get("encoded_true_peak_dbtp"), "encoded true peak") > -3.0:
        raise MixCalibrationError("mix calibration true peak exceeds -3 dBTP")
    return calibration


def approve_opening_mix(
    project: Path,
    calibration_path: Path,
    *,
    reviewer: str,
    note: str,
) -> MixApprovalResult:
    root = project.expanduser().resolve()
    reviewer = reviewer.strip() if isinstance(reviewer, str) else ""
    note = note.strip() if isinstance(note, str) else ""
    if not reviewer or not note:
        raise MixCalibrationError("reviewer and approval note are required")
    path = calibration_path.expanduser().resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise MixCalibrationError("mix calibration must be inside the project") from error
    raw = _load(path, "mix calibration")
    input_relative = raw.get("render_input_path")
    input_path = _media(root, input_relative, "render stage input")
    calibration = _verify_calibration(root, input_path, path)
    approval_path = safe_project_output(root, Path("07_render/OPENING_MIX_APPROVAL.json"))
    if approval_path.exists():
        try:
            existing = verify_opening_mix_approval(root, input_path)
        except MixCalibrationError:
            existing = None
        if existing is not None:
            if existing.get("calibration_path") == relative.as_posix() and existing.get("reviewer") == reviewer and existing.get("note") == note:
                return MixApprovalResult("unchanged", approval_path, "ready_for_render_preflight")
            raise MixCalibrationError("current opening mix already has a different valid approval")
    preview = root / calibration["preview_path"]
    bgm = root / calibration["bgm_source_path"]
    style = root / "HBG_STYLE.json"
    event_path = record_approval(
        root,
        release_id=calibration["release_id"],
        gate="opening_mix",
        decision="approved",
        reviewer=reviewer,
        subjects=[path, preview, style, bgm],
        evidence_refs=[relative.as_posix(), calibration["preview_path"]],
        note=note,
    )
    approval = {
        "schema_version": "opening-mix-approval.v1",
        "release_id": calibration["release_id"],
        "reviewer": reviewer,
        "note": note,
        "calibration_path": relative.as_posix(),
        "calibration_sha256": sha256_file(path),
        "preview_path": calibration["preview_path"],
        "preview_sha256": calibration["preview_sha256"],
        "bgm_source_path": calibration["bgm_source_path"],
        "bgm_source_sha256": calibration["bgm_source_sha256"],
        "hbg_style_sha256": calibration["hbg_style_sha256"],
        "gain_linear": calibration["gain_linear"],
        "gain_db": calibration["gain_db"],
        "approval_event_path": event_path.relative_to(root).as_posix(),
        "approval_event_sha256": sha256_file(event_path),
        "human_approved": True,
        "next_stage_status": "ready_for_render_preflight",
    }
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".opening-mix-", suffix=".json", dir=approval_path.parent, delete=False) as output:
        temp_path = Path(output.name)
        output.write(_pretty(approval))
    try:
        os.replace(temp_path, approval_path)
    except OSError as error:
        temp_path.unlink(missing_ok=True)
        raise MixCalibrationError(f"opening mix approval could not be published: {error}") from error
    return MixApprovalResult("created", approval_path, "ready_for_render_preflight")


def verify_opening_mix_approval(project: Path, input_path: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    resolved_input = input_path.expanduser().resolve()
    try:
        resolved_input.relative_to(root)
    except ValueError as error:
        raise MixCalibrationError("render stage input must be inside the project") from error
    approval_path = root / "07_render/OPENING_MIX_APPROVAL.json"
    approval = _load(approval_path, "opening mix approval")
    if set(approval) != _APPROVAL_FIELDS or approval.get("schema_version") != "opening-mix-approval.v1":
        raise MixCalibrationError("opening mix approval fields are invalid")
    calibration_path = _media(root, approval.get("calibration_path"), "mix calibration")
    calibration = _verify_calibration(root, resolved_input, calibration_path)
    expected = {
        "release_id": calibration["release_id"],
        "calibration_sha256": sha256_file(calibration_path),
        "preview_path": calibration["preview_path"],
        "preview_sha256": calibration["preview_sha256"],
        "bgm_source_path": calibration["bgm_source_path"],
        "bgm_source_sha256": calibration["bgm_source_sha256"],
        "hbg_style_sha256": calibration["hbg_style_sha256"],
        "gain_linear": calibration["gain_linear"],
        "gain_db": calibration["gain_db"],
        "human_approved": True,
        "next_stage_status": "ready_for_render_preflight",
    }
    for key, value in expected.items():
        if approval.get(key) != value:
            raise MixCalibrationError(f"opening mix approval is stale: {key}")
    event = _media(root, approval.get("approval_event_path"), "opening mix approval event")
    if sha256_file(event) != approval.get("approval_event_sha256"):
        raise MixCalibrationError("opening mix approval event hash is stale")
    event_record = _load(event, "opening mix approval event")
    if (
        event_record.get("gate") != "opening_mix"
        or event_record.get("decision") != "approved"
        or event_record.get("reviewer") != approval.get("reviewer")
        or event_record.get("note") != approval.get("note")
    ):
        raise MixCalibrationError("opening mix approval event decision is invalid")
    expected_subjects = {
        approval["calibration_path"]: approval["calibration_sha256"],
        approval["preview_path"]: approval["preview_sha256"],
        "HBG_STYLE.json": approval["hbg_style_sha256"],
        approval["bgm_source_path"]: approval["bgm_source_sha256"],
    }
    subjects = event_record.get("subjects")
    actual_subjects = {
        item.get("path"): item.get("sha256")
        for item in subjects if isinstance(item, dict)
    } if isinstance(subjects, list) else {}
    if actual_subjects != expected_subjects:
        raise MixCalibrationError("opening mix approval event subjects are stale or incomplete")
    return approval


def opening_mix_status(project: Path, input_path: Path) -> tuple[str, Path | None]:
    root = project.expanduser().resolve()
    resolved_input = input_path.expanduser().resolve()
    try:
        verify_opening_mix_approval(root, resolved_input)
        return "ready_for_render_preflight", root / "07_render/OPENING_MIX_APPROVAL.json"
    except MixCalibrationError:
        pass
    history = root / "07_render/mix-calibrations"
    if history.is_dir() and not history.is_symlink():
        candidates = sorted(history.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for candidate in candidates:
            try:
                _verify_calibration(root, resolved_input, candidate)
                return "awaiting_opening_mix_approval", candidate
            except MixCalibrationError:
                continue
    return "awaiting_opening_mix_calibration", None
