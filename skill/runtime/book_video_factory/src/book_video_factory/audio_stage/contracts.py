from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.hbg_bridge.provenance import _default_repository_root, _verify_vendor
from book_video_factory.manifests import sha256_file
from book_video_factory.style_profiles import project_workflow
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
    "schema_version", "release_id", "provider", "provider_policy", "voice", "body_rate",
    "lead_rate", "reveal_rate", "pitch", "lead_text", "reveal_text", "caption_min_chars",
    "caption_max_chars", "caption_min_duration", "lead_start", "flash_gap_after_lead",
    "flash_duration", "reveal_hold", "body_gap", "body_mode", "bindings",
}
_PROVIDERS = {"edge-tts", "minimax"}
_PROVIDER_POLICIES = {"legacy_edge", "minimax_required"}
# Edge voice identifiers look like zh-CN-YunjianNeural; MiniMax voice ids are
# opaque cloned/system identifiers (uuid-like, vendor tokens, or China system
# names such as ``Chinese (Mandarin)_Sincere_Adult``) and must not be forced
# through the Edge regex.
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._():-]{0,127}$")
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
    payload = dict(payload)
    provider = payload.get("provider")
    if provider not in _PROVIDERS:
        raise AudioStageContractError("provider must be one of edge-tts/minimax")
    if "provider_policy" not in payload:
        # Legacy inputs predate the provider policy field. The default is
        # derived from the provider so old Edge projects keep working as
        # explicit legacy and MiniMax inputs default to minimax_required;
        # a silent Edge fallback for an expressive pipeline is still rejected.
        payload["provider_policy"] = (
            "legacy_edge" if provider == "edge-tts" else "minimax_required"
        )
    _ensure_exact_keys(payload, _INPUT_KEYS, "audio stage input")
    if payload.get("schema_version") != "audio-stage-input.v1":
        raise AudioStageContractError("audio stage input schema_version is invalid")
    release_id = _text(payload.get("release_id"), "release_id")
    provider = payload.get("provider")
    if provider not in _PROVIDERS:
        raise AudioStageContractError("provider must be one of edge-tts/minimax")
    provider_policy = payload.get("provider_policy")
    if provider_policy not in _PROVIDER_POLICIES:
        raise AudioStageContractError("provider_policy must be legacy_edge or minimax_required")
    if (provider == "edge-tts") != (provider_policy == "legacy_edge"):
        raise AudioStageContractError(
            "provider/provider_policy mismatch: edge-tts requires legacy_edge and "
            "minimax requires minimax_required; an expressive pipeline must never "
            "silently fall back to Edge-TTS"
        )
    voice = _text(payload.get("voice"), "voice")
    if provider == "edge-tts":
        if not _VOICE_RE.fullmatch(voice):
            raise AudioStageContractError("voice must be an Edge Neural voice identifier")
    elif not _VOICE_ID_RE.fullmatch(voice):
        raise AudioStageContractError("voice must be a MiniMax voice_id")
    rates: dict[str, str] = {}
    for field in ("body_rate", "lead_rate", "reveal_rate"):
        rate = _text(payload.get(field), field)
        if provider == "edge-tts" and not _RATE_RE.fullmatch(rate):
            raise AudioStageContractError(f"{field} must use strict Edge syntax like +0%")
        if provider == "minimax" and rate != "/":
            raise AudioStageContractError(f"{field} must be / for MiniMax provider control")
        rates[field] = rate
    pitch = _text(payload.get("pitch"), "pitch")
    if provider == "edge-tts" and not _PITCH_RE.fullmatch(pitch):
        raise AudioStageContractError("pitch must use strict Edge syntax like +0Hz")
    if provider == "minimax" and pitch != "/":
        raise AudioStageContractError("pitch must be / for MiniMax provider control")
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
        "provider": provider,
        "provider_policy": provider_policy,
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
        if not approval.approved or approval.next_stage_status not in {"ready_for_edge_tts", "ready_for_narration"}:
            raise AudioStageContractError(
                "current approved visual evidence is required (next_stage_status must be "
                "ready_for_edge_tts or ready_for_narration)"
            )
        commit = _verify_vendor(repository_root or _default_repository_root())
    except (VisualApprovalError, RuntimeError) as error:
        if isinstance(error, AudioStageContractError):
            raise
        raise AudioStageContractError(f"Phase 4 prerequisites are invalid: {error}") from error
    try:
        visual_foundation_policy = project_workflow(root)["visual_foundation_policy"]
    except Exception as error:
        raise AudioStageContractError(f"Phase 4 workflow policy is invalid: {error}") from error
    visual_foundation: dict[str, str] = {}
    if visual_foundation_policy == "required":
        from book_video_factory.visual_foundation.approval import (
            VisualFoundationError,
            verify_visual_foundation_approval,
        )
        try:
            foundation = verify_visual_foundation_approval(root, release_id)
        except VisualFoundationError as error:
            raise AudioStageContractError(
                f"current approved visual foundation is required before formal audio: {error}"
            ) from error
        visual_foundation = {
            "visual_foundation_approval_path": foundation.approval_path.relative_to(root).as_posix(),
            "visual_foundation_approval_sha256": sha256_file(foundation.approval_path),
        }
    approval_payload = _read_object(approval.approval_path, "visual approval")
    return {
        "release_id": release_id,
        "visual_approval_path": approval.approval_path.relative_to(root).as_posix(),
        "visual_approval_sha256": sha256_file(approval.approval_path),
        "visual_review_digest": approval_payload.get("review_digest"),
        "next_stage_status": approval.next_stage_status,
        "hbg_commit": commit,
        **visual_foundation,
    }


# ---------------------------------------------------------------------------
# Voice Performance Plan (Part 8) -- per-caption prosody so the narration
# carries the emotional beat instead of a flat single rate.
# ---------------------------------------------------------------------------

_VPP_KEYS = {"schema_version", "release_id", "audio_meta_sha256", "captions", "status"}
_VPP_CAPTION_REQUIRED = {"rate", "pitch", "pause_ms_before", "pause_ms_after"}
_VPP_CAPTION_OPTIONAL = {"emphasis_words", "emotion_hint", "ssml_override"}
_VPP_CAPTION_KEYS = _VPP_CAPTION_REQUIRED | _VPP_CAPTION_OPTIONAL
_VPP_STATUSES = {"draft", "approved", "applied"}


def validate_voice_performance_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AudioStageContractError("voice performance plan must be a JSON object")
    _ensure_exact_keys(payload, _VPP_KEYS, "voice performance plan")
    if payload.get("schema_version") != "voice-performance-plan.v1":
        raise AudioStageContractError("voice performance plan schema_version is invalid")
    _text(payload.get("release_id"), "release_id")
    if not _SHA256_RE.fullmatch(payload.get("audio_meta_sha256", "")):
        raise AudioStageContractError("voice performance plan audio_meta_sha256 must be a SHA-256")
    status = payload.get("status")
    if status not in _VPP_STATUSES:
        raise AudioStageContractError("voice performance plan status must be draft/approved/applied")
    captions = payload.get("captions")
    if not isinstance(captions, Mapping):
        raise AudioStageContractError("voice performance plan captions must be a JSON object")
    normalized: dict[str, dict[str, Any]] = {}
    for caption_id, raw in captions.items():
        if not isinstance(raw, Mapping):
            raise AudioStageContractError(f"voice performance plan captions[{caption_id!r}] must be an object")
        keys = set(raw)
        if not _VPP_CAPTION_REQUIRED <= keys or not keys <= _VPP_CAPTION_KEYS:
            raise AudioStageContractError(f"voice performance plan captions[{caption_id!r}] fields are invalid")
        rate = _text(raw.get("rate"), f"captions[{caption_id!r}].rate")
        if not _RATE_RE.fullmatch(rate):
            raise AudioStageContractError(f"captions[{caption_id!r}].rate must use Edge syntax like +0%")
        pitch = _text(raw.get("pitch"), f"captions[{caption_id!r}].pitch")
        if not _PITCH_RE.fullmatch(pitch):
            raise AudioStageContractError(f"captions[{caption_id!r}].pitch must use Edge syntax like +0Hz")
        pause_before = int(_number(raw.get("pause_ms_before"), f"captions[{caption_id!r}].pause_ms_before", minimum=0, maximum=10000))
        pause_after = int(_number(raw.get("pause_ms_after"), f"captions[{caption_id!r}].pause_ms_after", minimum=0, maximum=10000))
        emphasis = raw.get("emphasis_words")
        if not isinstance(emphasis, list) or not all(isinstance(word, str) for word in emphasis):
            raise AudioStageContractError(f"captions[{caption_id!r}].emphasis_words must be a list of strings")
        emotion = _text(raw.get("emotion_hint"), f"captions[{caption_id!r}].emotion_hint", allow_empty=True)
        ssml = raw.get("ssml_override")
        if ssml is not None and (not isinstance(ssml, str) or not ssml.strip()):
            raise AudioStageContractError(f"captions[{caption_id!r}].ssml_override must be a nonempty string when present")
        normalized[caption_id] = {
            "rate": rate,
            "pitch": pitch,
            "pause_ms_before": pause_before,
            "pause_ms_after": pause_after,
            "emphasis_words": list(emphasis),
            "emotion_hint": emotion,
            "ssml_override": ssml,
        }
    return {
        "schema_version": "voice-performance-plan.v1",
        "release_id": payload.get("release_id"),
        "audio_meta_sha256": payload.get("audio_meta_sha256"),
        "status": status,
        "captions": normalized,
    }


# ---------------------------------------------------------------------------
# Audio Generation Evidence (M4A) -- binds every provider chunk SHA, the audio
# master, and the provider VTT; the only valid timing source for expressive
# production is the provider itself.
# ---------------------------------------------------------------------------

_AGE_KEYS = {
    "schema_version", "release_id", "variant", "voice_id", "model", "timing_source",
    "chunks", "master", "provider_vtt",
}
_AGE_CHUNK_KEYS = {
    "chunk_id", "provider", "model", "voice_id", "audio_path", "audio_sha256",
    "duration", "trace_id", "request_digest", "subtitle_granularity",
    "subtitle_timestamps",
}
_AGE_MASTER_KEYS = {"path", "sha256", "duration", "sample_rate", "channels", "sample_width"}
_AGE_VTT_KEYS = {"path", "sha256"}
_AGE_TIMING_SOURCES = {"provider", "edge_vtt"}


def validate_audio_generation_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AudioStageContractError("audio generation evidence must be a JSON object")
    _ensure_exact_keys(payload, _AGE_KEYS, "audio generation evidence")
    if payload.get("schema_version") != "audio-generation-evidence.v1":
        raise AudioStageContractError("audio generation evidence schema_version is invalid")
    _text(payload.get("release_id"), "release_id")
    _text(payload.get("variant"), "variant")
    _text(payload.get("voice_id"), "voice_id")
    _text(payload.get("model"), "model")
    timing_source = _text(payload.get("timing_source"), "timing_source")
    if timing_source not in _AGE_TIMING_SOURCES:
        raise AudioStageContractError("audio generation evidence timing_source is invalid")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise AudioStageContractError("audio generation evidence chunks must be a nonempty list")
    normalized_chunks: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise AudioStageContractError("audio generation evidence chunk must be an object")
        _ensure_exact_keys(chunk, _AGE_CHUNK_KEYS, "audio generation evidence chunk")
        _text(chunk.get("chunk_id"), "chunk_id")
        _text(chunk.get("provider"), "provider")
        _text(chunk.get("model"), "model")
        _text(chunk.get("voice_id"), "voice_id")
        _text(chunk.get("audio_path"), "audio_path")
        if not _SHA256_RE.fullmatch(chunk.get("audio_sha256", "")):
            raise AudioStageContractError("audio generation evidence chunk audio_sha256 must be a SHA-256")
        _number(chunk.get("duration"), "duration", minimum=0.0, maximum=3600.0)
        trace_id = chunk.get("trace_id")
        if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip()):
            raise AudioStageContractError("audio generation evidence chunk trace_id is invalid")
        if not _SHA256_RE.fullmatch(chunk.get("request_digest", "")):
            raise AudioStageContractError("audio generation evidence chunk request_digest must be a SHA-256")
        granularity = _text(chunk.get("subtitle_granularity"), "subtitle_granularity")
        if granularity not in {"word", "sentence"}:
            raise AudioStageContractError("audio generation evidence chunk subtitle_granularity must be word or sentence")
        timestamps = chunk.get("subtitle_timestamps")
        if not isinstance(timestamps, list) or not timestamps:
            raise AudioStageContractError("audio generation evidence chunk has no subtitle timestamps")
        previous_end = -1.0
        for item in timestamps:
            if not isinstance(item, Mapping):
                raise AudioStageContractError("audio generation evidence timestamp must be an object")
            start = _number(item.get("start"), "start", minimum=0.0, maximum=3600.0)
            end = _number(item.get("end"), "end", minimum=0.0, maximum=3600.0)
            if end <= start or start + 1e-9 < previous_end:
                raise AudioStageContractError("audio generation evidence timestamps are non-monotonic")
            _text(item.get("text"), "text")
            previous_end = end
        normalized_chunks.append({
            "chunk_id": chunk.get("chunk_id"),
            "provider": chunk.get("provider"),
            "model": chunk.get("model"),
            "voice_id": chunk.get("voice_id"),
            "audio_path": chunk.get("audio_path"),
            "audio_sha256": chunk.get("audio_sha256"),
            "duration": chunk.get("duration"),
            "trace_id": trace_id,
            "request_digest": chunk.get("request_digest"),
            "subtitle_granularity": granularity,
            "subtitle_timestamps": list(timestamps),
        })
    master = payload.get("master")
    if not isinstance(master, Mapping):
        raise AudioStageContractError("audio generation evidence master must be an object")
    _ensure_exact_keys(master, _AGE_MASTER_KEYS, "audio generation evidence master")
    _text(master.get("path"), "master path")
    if not _SHA256_RE.fullmatch(master.get("sha256", "")):
        raise AudioStageContractError("audio generation evidence master sha256 must be a SHA-256")
    _number(master.get("duration"), "master duration", minimum=0.0, maximum=36000.0)
    vtt = payload.get("provider_vtt")
    if not isinstance(vtt, Mapping):
        raise AudioStageContractError("audio generation evidence provider_vtt must be an object")
    _ensure_exact_keys(vtt, _AGE_VTT_KEYS, "audio generation evidence provider_vtt")
    _text(vtt.get("path"), "provider_vtt path")
    if not _SHA256_RE.fullmatch(vtt.get("sha256", "")):
        raise AudioStageContractError("audio generation evidence provider_vtt sha256 must be a SHA-256")
    return {
        "schema_version": "audio-generation-evidence.v1",
        "release_id": payload.get("release_id"),
        "variant": payload.get("variant"),
        "voice_id": payload.get("voice_id"),
        "model": payload.get("model"),
        "timing_source": timing_source,
        "chunks": normalized_chunks,
        "master": dict(master),
        "provider_vtt": dict(vtt),
    }


# ---------------------------------------------------------------------------
# Voice Foundation (M4A) -- pins the production voice identity (cloned or
# system) before any narration is synthesized.
# ---------------------------------------------------------------------------

_VF_KEYS = {
    "schema_version", "release_id", "provider", "voice_strategy", "voice_id",
    "model_family", "cloned_at", "last_used_at", "unused_activation_window_hours",
    "source_audio_sha256", "prompt_audio_sha256", "evidence",
}
_VF_STRATEGIES = {"cloned_voice", "system_voice"}


def validate_voice_foundation(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AudioStageContractError("voice foundation must be a JSON object")
    _ensure_exact_keys(payload, _VF_KEYS, "voice foundation")
    if payload.get("schema_version") != "voice-foundation.v1":
        raise AudioStageContractError("voice foundation schema_version is invalid")
    _text(payload.get("release_id"), "release_id")
    provider = payload.get("provider")
    if provider != "minimax":
        raise AudioStageContractError("voice foundation provider must be minimax")
    strategy = _text(payload.get("voice_strategy"), "voice_strategy")
    if strategy not in _VF_STRATEGIES:
        raise AudioStageContractError("voice foundation voice_strategy is invalid")
    voice_id = _text(payload.get("voice_id"), "voice_id")
    if not _VOICE_ID_RE.fullmatch(voice_id):
        raise AudioStageContractError("voice foundation voice_id is invalid")
    _text(payload.get("model_family"), "model_family")
    cloned_at = payload.get("cloned_at")
    if cloned_at is not None and (not isinstance(cloned_at, str) or not cloned_at.strip()):
        raise AudioStageContractError("voice foundation cloned_at must be an ISO string or null")
    if strategy == "cloned_voice" and not cloned_at:
        raise AudioStageContractError("cloned_voice foundation requires cloned_at")
    last_used_at = payload.get("last_used_at")
    if last_used_at is not None and (not isinstance(last_used_at, str) or not last_used_at.strip()):
        raise AudioStageContractError("voice foundation last_used_at must be an ISO string or null")
    window = int(_number(
        payload.get("unused_activation_window_hours"),
        "unused_activation_window_hours",
        minimum=1,
        maximum=24 * 90,
    ))
    for field in ("source_audio_sha256", "prompt_audio_sha256"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or not _SHA256_RE.fullmatch(value)):
            raise AudioStageContractError(f"voice foundation {field} must be a SHA-256 or null")
    evidence = payload.get("evidence")
    if not isinstance(evidence, Mapping):
        raise AudioStageContractError("voice foundation evidence must be an object")
    return {
        "schema_version": "voice-foundation.v1",
        "release_id": payload.get("release_id"),
        "provider": provider,
        "voice_strategy": strategy,
        "voice_id": voice_id,
        "model_family": payload.get("model_family"),
        "cloned_at": cloned_at,
        "last_used_at": last_used_at,
        "unused_activation_window_hours": window,
        "source_audio_sha256": payload.get("source_audio_sha256"),
        "prompt_audio_sha256": payload.get("prompt_audio_sha256"),
        "evidence": dict(evidence),
    }


# ---------------------------------------------------------------------------
# Narration Performance Plan (M4A) -- expressive performance segments, never
# per-caption, produced by the Narration Performance Director.
# ---------------------------------------------------------------------------

_NPP_KEYS = {
    "schema_version", "release_id", "variant", "variant_label", "intensity_factor",
    "audio_meta_sha256", "voice_profile", "segments",
}
_NPP_VARIANTS = {"A", "B", "C"}
_NPP_SEGMENT_REQUIRED = {
    "segment_id", "source_unit_ids", "source_text", "spoken_text",
    "narrative_function", "delivery_mode", "intensity", "speed", "volume", "pitch",
    "pause_before_ms", "pause_after_ms", "sound_tags", "performance_note",
    "estimated_seconds",
}
_NPP_SEGMENT_OPTIONAL = {"emotion"}
_NPP_SEGMENT_KEYS = _NPP_SEGMENT_REQUIRED | _NPP_SEGMENT_OPTIONAL
_NPP_MODES = {"storytelling", "warmth", "tension", "grief", "impact", "reflection"}
_NPP_EMOTIONS = {"neutral", "calm", "sad", "warm", "tense", "impactful", "reflective"}
_NPP_SOUND_TAGS = {"sighs", "breath", "inhale", "exhale", "crying"}


def validate_narration_performance_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ``narration-performance-plan.v1`` document (M4A)."""

    if not isinstance(payload, Mapping):
        raise AudioStageContractError("narration performance plan must be a JSON object")
    _ensure_exact_keys(payload, _NPP_KEYS, "narration performance plan")
    if payload.get("schema_version") != "narration-performance-plan.v1":
        raise AudioStageContractError("narration performance plan schema_version is invalid")
    _text(payload.get("release_id"), "release_id")
    variant = payload.get("variant")
    if variant not in _NPP_VARIANTS:
        raise AudioStageContractError("narration performance plan variant must be A/B/C")
    _text(payload.get("variant_label"), "variant_label")
    factor = _number(payload.get("intensity_factor"), "intensity_factor", minimum=0.0, maximum=2.0)
    audio_meta = payload.get("audio_meta_sha256")
    if audio_meta is not None and (not isinstance(audio_meta, str) or not _SHA256_RE.fullmatch(audio_meta)):
        raise AudioStageContractError("narration performance plan audio_meta_sha256 must be a SHA-256 or null")
    voice_profile = payload.get("voice_profile")
    if not isinstance(voice_profile, Mapping):
        raise AudioStageContractError("narration performance plan voice_profile must be an object")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AudioStageContractError("narration performance plan segments must be a nonempty list")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in segments:
        if not isinstance(raw, Mapping):
            raise AudioStageContractError("narration performance plan segments must be objects")
        keys = set(raw)
        if not _NPP_SEGMENT_REQUIRED <= keys or not keys <= _NPP_SEGMENT_KEYS:
            raise AudioStageContractError("narration performance plan segment fields are invalid")
        segment_id = _text(raw.get("segment_id"), "segment_id")
        if segment_id in seen_ids:
            raise AudioStageContractError(f"narration performance plan segment {segment_id!r} is duplicated")
        seen_ids.add(segment_id)
        unit_ids = raw.get("source_unit_ids")
        if not isinstance(unit_ids, list) or not unit_ids or not all(isinstance(x, str) and x for x in unit_ids):
            raise AudioStageContractError(f"segment {segment_id!r} source_unit_ids must be a nonempty list of strings")
        _text(raw.get("source_text"), f"segment {segment_id!r} source_text")
        _text(raw.get("spoken_text"), f"segment {segment_id!r} spoken_text")
        _text(raw.get("narrative_function"), f"segment {segment_id!r} narrative_function", allow_empty=True)
        mode = _text(raw.get("delivery_mode"), f"segment {segment_id!r} delivery_mode")
        if mode not in _NPP_MODES:
            raise AudioStageContractError(f"segment {segment_id!r} delivery_mode is invalid")
        emotion = raw.get("emotion")
        if emotion is not None:
            emotion = _text(emotion, f"segment {segment_id!r} emotion")
            if emotion not in _NPP_EMOTIONS:
                raise AudioStageContractError(f"segment {segment_id!r} emotion is invalid")
        intensity = _number(raw.get("intensity"), f"segment {segment_id!r} intensity", minimum=0.0, maximum=1.0)
        speed = _number(raw.get("speed"), f"segment {segment_id!r} speed", minimum=0.5, maximum=1.5)
        volume = _number(raw.get("volume"), f"segment {segment_id!r} volume", minimum=0.0, maximum=2.0)
        pitch = _number(raw.get("pitch"), f"segment {segment_id!r} pitch", minimum=-12.0, maximum=12.0)
        pause_before = int(_number(raw.get("pause_before_ms"), f"segment {segment_id!r} pause_before_ms", minimum=0, maximum=10000))
        pause_after = int(_number(raw.get("pause_after_ms"), f"segment {segment_id!r} pause_after_ms", minimum=0, maximum=10000))
        tags = raw.get("sound_tags")
        if not isinstance(tags, list) or not all(isinstance(t, str) and t in _NPP_SOUND_TAGS for t in tags):
            raise AudioStageContractError(f"segment {segment_id!r} sound_tags are invalid")
        if len(tags) > 2:
            raise AudioStageContractError(f"segment {segment_id!r} uses more than 2 sound tags")
        _text(raw.get("performance_note"), f"segment {segment_id!r} performance_note", allow_empty=True)
        _number(raw.get("estimated_seconds"), f"segment {segment_id!r} estimated_seconds", minimum=0.0, maximum=120.0)
        normalized.append({
            "segment_id": segment_id,
            "source_unit_ids": list(unit_ids),
            "source_text": raw.get("source_text"),
            "spoken_text": raw.get("spoken_text"),
            "narrative_function": raw.get("narrative_function"),
            "delivery_mode": mode,
            "emotion": emotion,
            "intensity": intensity,
            "speed": speed,
            "volume": volume,
            "pitch": pitch,
            "pause_before_ms": pause_before,
            "pause_after_ms": pause_after,
            "sound_tags": list(tags),
            "performance_note": raw.get("performance_note"),
            "estimated_seconds": raw.get("estimated_seconds"),
        })
    return {
        "schema_version": "narration-performance-plan.v1",
        "release_id": payload.get("release_id"),
        "variant": variant,
        "variant_label": payload.get("variant_label"),
        "intensity_factor": factor,
        "audio_meta_sha256": audio_meta,
        "voice_profile": dict(voice_profile),
        "segments": normalized,
    }


_VPA_KEYS = {"schema_version", "release_id", "voice_performance_plan_sha256", "reviewer", "status", "note"}


def validate_voice_audition_approval(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Gate: a human must approve the auditioned voice performance before full TTS.

    The pipeline fails closed -- an unapproved or missing audition blocks the
    full narration render, so a never-listened-to performance can never ship.
    """

    if not isinstance(payload, Mapping):
        raise AudioStageContractError("voice audition approval must be a JSON object")
    _ensure_exact_keys(payload, _VPA_KEYS, "voice audition approval")
    if payload.get("schema_version") != "voice-audition-approval.v1":
        raise AudioStageContractError("voice audition approval schema_version is invalid")
    _text(payload.get("release_id"), "release_id")
    if not _SHA256_RE.fullmatch(payload.get("voice_performance_plan_sha256", "")):
        raise AudioStageContractError("voice audition approval plan_sha256 must be a SHA-256")
    reviewer = _text(payload.get("reviewer"), "reviewer")
    status = payload.get("status")
    if status != "approved":
        raise AudioStageContractError(
            f"voice audition is not approved (status={status!r}); full TTS is blocked"
        )
    note = _text(payload.get("note"), "note", allow_empty=True)
    return {
        "schema_version": "voice-audition-approval.v1",
        "release_id": payload.get("release_id"),
        "voice_performance_plan_sha256": payload.get("voice_performance_plan_sha256"),
        "reviewer": reviewer,
        "status": status,
        "note": note,
    }
