from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.hbg_bridge.provenance import _default_repository_root, _verify_vendor
from book_video_factory.manifests import sha256_file
from book_video_factory.visual_stage.approval import VisualApprovalError, verify_visual_approval


class AudioStageContractError(RuntimeError):
    """Phase 4 input or prerequisite evidence is invalid."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RATE_RE = re.compile(r"^[+-](?:0|[1-9]\d{0,2})%$")
_PITCH_RE = re.compile(r"^[+-](?:0|[1-9]\d{0,3})Hz$")
_VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+Neural$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_LOCAL_PATH_RE = re.compile(r"(?:^|[\s'\"])(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:\\|~[/\\])")
_SECRET_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|api[_-]?key|secret[_-]?key|bearer\s+[A-Za-z0-9._-]+)", re.I)
_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER|FIXME)\b|待定|后补|自动生成", re.I)
_SSML_RE = re.compile(r"</?[A-Za-z][^>]*>")
_COMMAND_RE = re.compile(r"(?:\$\(|`|&&|\|\||;\s*(?:rm|curl|wget|python|bash|sh)\b)", re.I)

_INPUT_KEYS = {
    "schema_version", "release_id", "provider", "voice", "body_rate", "lead_rate",
    "reveal_rate", "pitch", "lead_text", "reveal_text", "caption_min_chars",
    "caption_max_chars", "caption_min_duration", "lead_start", "flash_gap_after_lead",
    "flash_duration", "reveal_hold", "body_gap", "body_mode", "bindings",
}
_BINDING_PATHS = {
    "script_md_sha256": "SCRIPT.md",
    "project_spec_sha256": "PROJECT_SPEC.json",
    "hbg_style_sha256": "HBG_STYLE.json",
    "storyboard_base_sha256": "STORYBOARD_BASE.json",
    "visual_approval_sha256": "03_images_生成图片/ANCHOR_APPROVAL.json",
    "pronunciation_lexicon_sha256": "04_audio/PRONUNCIATION_LEXICON.json",
}
_LEXICON_KEYS = {"schema_version", "release_id", "entries"}
_ENTRY_KEYS = {"entry_id", "display", "spoken", "scope", "occurrence_policy", "note"}
_OCCURRENCE_KEYS = {"mode", "count"}
_ALLOWED_SCOPES = {"body", "lead", "reveal"}


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or (not value and not allow_empty):
        raise AudioStageContractError(f"{label} must be a nonempty trimmed string")
    if _LOCAL_PATH_RE.search(value):
        raise AudioStageContractError(f"{label} contains a local filesystem path")
    if _SECRET_RE.search(value):
        raise AudioStageContractError(f"{label} contains a secret-like value")
    if _PLACEHOLDER_RE.search(value):
        raise AudioStageContractError(f"{label} contains placeholder text")
    return value


def _number(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioStageContractError(f"{label} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise AudioStageContractError(f"{label} must be between {minimum} and {maximum}")
    return result


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise AudioStageContractError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioStageContractError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise AudioStageContractError(f"{label} must be a JSON object")
    return value


def _ensure_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise AudioStageContractError(f"{label} fields are invalid; missing={missing}, extra={extra}")


def _safe_project_evidence(root: Path, relative: str, label: str) -> Path:
    lexical = root / relative
    if lexical.is_symlink():
        raise AudioStageContractError(f"{label} is an unsafe symlink: {relative}")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise AudioStageContractError(f"{label} is missing or unsafe: {relative}") from error
    if not resolved.is_file():
        raise AudioStageContractError(f"{label} is not a regular file: {relative}")
    return resolved


def validate_audio_stage_input(project: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AudioStageContractError("audio stage input must be a JSON object")
    _ensure_exact_keys(payload, _INPUT_KEYS, "audio stage input")
    if payload.get("schema_version") != "audio-stage-input.v1":
        raise AudioStageContractError("audio stage input schema_version is invalid")
    release_id = _text(payload.get("release_id"), "release_id")
    if payload.get("provider") != "edge-tts":
        raise AudioStageContractError("provider must be edge-tts")
    voice = _text(payload.get("voice"), "voice")
    if not _VOICE_RE.fullmatch(voice):
        raise AudioStageContractError("voice must be an Edge Neural voice identifier")
    rates: dict[str, str] = {}
    for field in ("body_rate", "lead_rate", "reveal_rate"):
        rate = _text(payload.get(field), field)
        if not _RATE_RE.fullmatch(rate):
            raise AudioStageContractError(f"{field} must use strict Edge syntax like +0%")
        rates[field] = rate
    pitch = _text(payload.get("pitch"), "pitch")
    if not _PITCH_RE.fullmatch(pitch):
        raise AudioStageContractError("pitch must use strict Edge syntax like +0Hz")
    lead_text = _text(payload.get("lead_text"), "lead_text")
    reveal_text = _text(payload.get("reveal_text"), "reveal_text")
    if _SSML_RE.search(lead_text) or _SSML_RE.search(reveal_text):
        raise AudioStageContractError("opening copy cannot contain SSML or markup")
    caption_min_chars = int(_number(payload.get("caption_min_chars"), "caption_min_chars", minimum=1, maximum=20))
    caption_max_chars = int(_number(payload.get("caption_max_chars"), "caption_max_chars", minimum=6, maximum=30))
    if caption_min_chars > caption_max_chars:
        raise AudioStageContractError("caption_min_chars cannot exceed caption_max_chars")
    caption_min_duration = _number(payload.get("caption_min_duration"), "caption_min_duration", minimum=0.2, maximum=3.0)
    timings = {
        field: _number(payload.get(field), field, minimum=0.0, maximum=10.0)
        for field in ("lead_start", "flash_gap_after_lead", "flash_duration", "reveal_hold", "body_gap")
    }
    if payload.get("body_mode") != "continuous":
        raise AudioStageContractError("body_mode must be continuous")
    bindings = payload.get("bindings")
    if not isinstance(bindings, Mapping):
        raise AudioStageContractError("bindings must be a JSON object")
    _ensure_exact_keys(bindings, set(_BINDING_PATHS), "bindings")
    root = project.expanduser().resolve()
    normalized_bindings: dict[str, str] = {}
    for field, relative in _BINDING_PATHS.items():
        expected = bindings.get(field)
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            raise AudioStageContractError(f"{field} must be a lowercase SHA-256")
        path = _safe_project_evidence(root, relative, field)
        if sha256_file(path) != expected:
            raise AudioStageContractError(f"{field} is stale or does not match {relative}")
        normalized_bindings[field] = expected
    return {
        "schema_version": "audio-stage-input.v1",
        "release_id": release_id,
        "provider": "edge-tts",
        "voice": voice,
        **rates,
        "pitch": pitch,
        "lead_text": lead_text,
        "reveal_text": reveal_text,
        "caption_min_chars": caption_min_chars,
        "caption_max_chars": caption_max_chars,
        "caption_min_duration": caption_min_duration,
        **timings,
        "body_mode": "continuous",
        "bindings": normalized_bindings,
    }


def validate_pronunciation_lexicon(project: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    del project  # project is reserved for future chapter-scope resolution
    if not isinstance(payload, Mapping):
        raise AudioStageContractError("pronunciation lexicon must be a JSON object")
    _ensure_exact_keys(payload, _LEXICON_KEYS, "pronunciation lexicon")
    if payload.get("schema_version") != "pronunciation-lexicon.v1":
        raise AudioStageContractError("pronunciation lexicon schema_version is invalid")
    release_id = _text(payload.get("release_id"), "lexicon release_id")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise AudioStageContractError("pronunciation lexicon entries must be a list")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise AudioStageContractError(f"entries[{index}] must be an object")
        _ensure_exact_keys(raw, _ENTRY_KEYS, f"entries[{index}]")
        entry_id = _text(raw.get("entry_id"), f"entries[{index}].entry_id")
        if not _ID_RE.fullmatch(entry_id) or entry_id in ids:
            raise AudioStageContractError(f"entries[{index}].entry_id is invalid or duplicated")
        ids.add(entry_id)
        display = _text(raw.get("display"), f"entries[{index}].display")
        spoken = _text(raw.get("spoken"), f"entries[{index}].spoken")
        if _SSML_RE.search(display) or _SSML_RE.search(spoken):
            raise AudioStageContractError("pronunciation entries cannot contain SSML or markup")
        if _COMMAND_RE.search(display) or _COMMAND_RE.search(spoken):
            raise AudioStageContractError("pronunciation entries cannot contain executable shell content")
        if any(ch in spoken for ch in ("\n", "\r", "\x00")):
            raise AudioStageContractError("spoken replacement cannot contain control lines")
        scope = raw.get("scope")
        if not isinstance(scope, str) or (scope not in _ALLOWED_SCOPES and not re.fullmatch(r"chapter:[1-9]\d*", scope)):
            raise AudioStageContractError(f"entries[{index}].scope is invalid")
        occurrence = raw.get("occurrence_policy")
        if not isinstance(occurrence, Mapping):
            raise AudioStageContractError("occurrence_policy must be an object")
        mode = occurrence.get("mode")
        if mode == "all":
            if set(occurrence) != {"mode"}:
                raise AudioStageContractError("all occurrence policy may contain only mode")
            normalized_occurrence = {"mode": "all"}
        elif mode == "exact":
            if set(occurrence) != {"mode", "count"}:
                raise AudioStageContractError("exact occurrence policy requires mode and count")
            count = occurrence.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise AudioStageContractError("exact occurrence count must be a positive integer")
            normalized_occurrence = {"mode": "exact", "count": count}
        else:
            raise AudioStageContractError("occurrence_policy.mode must be all or exact")
        pair = (display, scope)
        if pair in pairs:
            raise AudioStageContractError("duplicate display and scope pronunciation entry")
        pairs.add(pair)
        note = _text(raw.get("note"), f"entries[{index}].note", allow_empty=True)
        normalized.append({
            "entry_id": entry_id,
            "display": display,
            "spoken": spoken,
            "scope": scope,
            "occurrence_policy": normalized_occurrence,
            "note": note,
        })
    return {"schema_version": "pronunciation-lexicon.v1", "release_id": release_id, "entries": normalized}


def verify_phase4_prerequisites(
    project: Path,
    release_id: str,
    *,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    root = project.expanduser().resolve()
    try:
        approval = verify_visual_approval(root, release_id)
        if not approval.approved or approval.next_stage_status != "ready_for_edge_tts":
            raise AudioStageContractError("current approved visual evidence is required")
        commit = _verify_vendor(repository_root or _default_repository_root())
    except (VisualApprovalError, RuntimeError) as error:
        if isinstance(error, AudioStageContractError):
            raise
        raise AudioStageContractError(f"Phase 4 prerequisites are invalid: {error}") from error
    approval_payload = _read_object(approval.approval_path, "visual approval")
    return {
        "release_id": release_id,
        "visual_approval_path": approval.approval_path.relative_to(root).as_posix(),
        "visual_approval_sha256": sha256_file(approval.approval_path),
        "visual_review_digest": approval_payload.get("review_digest"),
        "next_stage_status": approval.next_stage_status,
        "hbg_commit": commit,
    }
