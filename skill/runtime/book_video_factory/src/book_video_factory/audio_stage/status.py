from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.manifests import sha256_file
from book_video_factory.visual_stage.approval import visual_stage_next_status

from .contracts import (
    AudioStageContractError,
    validate_audio_generation_evidence,
    validate_audio_stage_input,
    validate_voice_foundation,
)


class AudioStageStatusError(RuntimeError):
    pass


_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_EDGE_PROVENANCE_KEYS = {
    "python", "node", "ffmpeg", "ffprobe", "edge_tts",
    "external_edge_service_exercised",
}
_MINIMAX_PROVENANCE_KEYS = {
    "python", "node", "ffmpeg", "ffprobe", "minimax_provider_exercised",
}
_EDGE_PRELIMINARY_KEYS = {
    "schema_version", "release_id", "input_digest", "display_text_sha256",
    "spoken_text_sha256", "output_hashes", "media_report", "tool_provenance",
    "external_edge_service_exercised", "stage_manifest_path",
    "stage_manifest_sha256", "next_stage_status",
}
_MINIMAX_PRELIMINARY_KEYS = (_EDGE_PRELIMINARY_KEYS - {"external_edge_service_exercised"}) | {
    "provider", "provider_policy", "voice_id", "evidence_relative",
}
_FINAL_KEYS = {
    "schema_version", "release_id", "input_digest", "preliminary_manifest_path",
    "preliminary_manifest_sha256", "storyboard_plan_path", "storyboard_plan_sha256",
    "preliminary_audio_hashes", "final_output_hashes", "caption_timeline_sha256",
    "media_report", "tool_provenance", "external_edge_service_exercised",
    "stage_manifest_path", "stage_manifest_sha256", "next_stage_status",
}
_MINIMAX_FINAL_KEYS = (_FINAL_KEYS - {"external_edge_service_exercised"}) | {
    "provider", "provider_policy", "voice_id",
}
_VOICE_FOUNDATION_RELATIVE = "04_audio/VOICE_FOUNDATION.json"


def _object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AudioStageStatusError(f"missing or unsafe evidence: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioStageStatusError(f"unreadable evidence: {path}") from error
    if not isinstance(value, dict):
        raise AudioStageStatusError(f"evidence must be a JSON object: {path}")
    return value


def _safe_path(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
        raise AudioStageStatusError(f"{label} path is invalid")
    lexical = root / relative
    if lexical.is_symlink():
        raise AudioStageStatusError(f"{label} is an unsafe symlink")
    try:
        target = lexical.resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as error:
        raise AudioStageStatusError(f"{label} is missing or escapes the project") from error
    if not target.is_file():
        raise AudioStageStatusError(f"{label} is not a regular file")
    return target


def _hash_table(root: Path, table: Any, *, skip: set[str] | None = None) -> dict[str, str]:
    if not isinstance(table, Mapping) or not table:
        raise AudioStageStatusError("hash table is missing")
    skip = skip or set()
    normalized: dict[str, str] = {}
    for relative, expected in table.items():
        if not isinstance(relative, str) or not isinstance(expected, str) or not _SHA_RE.fullmatch(expected):
            raise AudioStageStatusError("hash table entry is invalid")
        target = _safe_path(root, relative, "output")
        if relative not in skip and sha256_file(target) != expected:
            raise AudioStageStatusError(f"stale output: {relative}")
        normalized[relative] = expected
    return normalized


def _provenance_keys(provider: str) -> set[str]:
    return _MINIMAX_PROVENANCE_KEYS if provider == "minimax" else _EDGE_PROVENANCE_KEYS


def _verify_provenance(manifest: Mapping[str, Any], provider: str) -> None:
    provenance = manifest.get("tool_provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != _provenance_keys(provider):
        raise AudioStageStatusError("audio tool provenance is invalid")
    if provider == "minimax":
        exercised = provenance.get("minimax_provider_exercised")
        if not isinstance(exercised, bool) or exercised is not True:
            raise AudioStageStatusError("MiniMax provider provenance is not exercised")
    else:
        external = manifest.get("external_edge_service_exercised")
        if not isinstance(external, bool) or provenance.get("external_edge_service_exercised") is not external:
            raise AudioStageStatusError("audio tool provenance disagrees with the Edge service flag")


def _verify_stage_outputs(
    root: Path,
    stage: Mapping[str, Any],
    output_hashes: Mapping[str, str],
    stage_relative: str,
    *,
    skip: set[str] | None = None,
) -> None:
    records = stage.get("outputs")
    if not isinstance(records, list):
        raise AudioStageStatusError("stage output records are missing")
    parsed: dict[str, tuple[int, str]] = {}
    for item in records:
        if not isinstance(item, Mapping) or set(item) != {"path", "bytes", "sha256"}:
            raise AudioStageStatusError("stage output record is invalid")
        relative, size, digest = item.get("path"), item.get("bytes"), item.get("sha256")
        if (
            not isinstance(relative, str) or relative in parsed
            or isinstance(size, bool) or not isinstance(size, int) or size <= 0
            or not isinstance(digest, str) or not _SHA_RE.fullmatch(digest)
        ):
            raise AudioStageStatusError("stage output record values are invalid")
        parsed[relative] = (size, digest)
    expected = {path: digest for path, digest in output_hashes.items() if path != stage_relative}
    if set(parsed) != set(expected):
        raise AudioStageStatusError("stage output set disagrees with the manifest")
    skip = skip or set()
    for relative, digest in expected.items():
        target = _safe_path(root, relative, "stage output")
        size, recorded_digest = parsed[relative]
        if recorded_digest != digest:
            raise AudioStageStatusError(f"stage output digest mismatch: {relative}")
        if relative not in skip and size != target.stat().st_size:
            raise AudioStageStatusError(f"stage output size mismatch: {relative}")


def _verify_preliminary_structure(
    root: Path,
    release_id: str,
    *,
    provider: str,
    final_exists: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = root / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    manifest = _object(manifest_path)
    expected_keys = _MINIMAX_PRELIMINARY_KEYS if provider == "minimax" else _EDGE_PRELIMINARY_KEYS
    if set(manifest) != expected_keys:
        raise AudioStageStatusError("preliminary audio manifest fields are invalid")
    if (
        manifest.get("schema_version") != "audio-preliminary-manifest.v1"
        or manifest.get("release_id") != release_id
        or manifest.get("next_stage_status") != "awaiting_audio_storyboard_plan"
    ):
        raise AudioStageStatusError("preliminary audio manifest identity is invalid")
    for field in ("input_digest", "display_text_sha256", "spoken_text_sha256", "stage_manifest_sha256"):
        if not isinstance(manifest.get(field), str) or not _SHA_RE.fullmatch(manifest[field]):
            raise AudioStageStatusError(f"preliminary {field} is invalid")
    if not isinstance(manifest.get("media_report"), Mapping):
        raise AudioStageStatusError("preliminary media report is invalid")
    _verify_provenance(manifest, provider)
    if provider == "minimax":
        if (
            manifest.get("provider") != "minimax"
            or manifest.get("provider_policy") != "minimax_required"
            or not isinstance(manifest.get("voice_id"), str)
            or not manifest["voice_id"]
            or not isinstance(manifest.get("evidence_relative"), str)
            or not manifest["evidence_relative"]
        ):
            raise AudioStageStatusError("preliminary MiniMax provider identity is invalid")
    superseded = {"audio_meta.json", "STORYBOARD.json"} if final_exists else set()
    hashes = _hash_table(root, manifest.get("output_hashes"), skip=superseded)
    if provider == "minimax":
        evidence_relative = manifest["evidence_relative"]
        if hashes.get(evidence_relative) is None:
            raise AudioStageStatusError("preliminary MiniMax evidence is not hash-bound")
        evidence_path = _safe_path(root, evidence_relative, "MiniMax provider evidence")
        try:
            evidence = validate_audio_generation_evidence(_object(evidence_path))
        except AudioStageContractError as error:
            raise AudioStageStatusError(f"MiniMax provider evidence is invalid: {error}") from error
        if evidence.get("timing_source") != "provider":
            raise AudioStageStatusError("MiniMax provider evidence has no provider timing authority")
        for chunk in evidence["chunks"]:
            chunk_path = _safe_path(root, chunk["audio_path"], "MiniMax provider chunk")
            if sha256_file(chunk_path) != chunk["audio_sha256"]:
                raise AudioStageStatusError("MiniMax provider chunk hash is stale")
        for label, record in (
            ("MiniMax master", evidence["master"]),
            ("MiniMax provider VTT", evidence["provider_vtt"]),
        ):
            target = _safe_path(root, record["path"], label)
            if sha256_file(target) != record["sha256"]:
                raise AudioStageStatusError(f"{label} hash is stale")
    stage_relative = manifest.get("stage_manifest_path")
    if not isinstance(stage_relative, str) or not stage_relative.startswith("manifests/stages/audio_preliminary/"):
        raise AudioStageStatusError("preliminary stage manifest path is invalid")
    if hashes.get(stage_relative) != manifest.get("stage_manifest_sha256"):
        raise AudioStageStatusError("preliminary stage binding disagrees with the output table")
    stage_path = _safe_path(root, stage_relative, "preliminary stage manifest")
    stage = _object(stage_path)
    if set(stage) != {"schema_version","manifest_id","project_id","stage","release_id","producer","status","inputs","outputs","checks"}:
        raise AudioStageStatusError("preliminary stage manifest fields are invalid")
    if (
        stage.get("schema_version") != "1.0"
        or stage.get("project_id") != root.name
        or stage.get("stage") != "audio_preliminary"
        or stage.get("release_id") != release_id
        or stage.get("producer") != {"tool":"book-video-factory-audio-stage"}
        or stage.get("status") != "success"
        or stage.get("inputs") != {"digest":manifest["input_digest"]}
    ):
        raise AudioStageStatusError("preliminary stage manifest identity is invalid")
    _verify_stage_outputs(root, stage, hashes, stage_relative, skip=superseded)
    checks = stage.get("checks")
    if provider == "minimax":
        expected_checks = {
            ("provider_evidence_bound", "pass", "error"),
            ("display_caption_restoration", "pass", "error"),
        }
    else:
        expected_checks = {
            ("real_vtt_master", "pass", "error"),
            ("display_caption_restoration", "pass", "error"),
        }
    if (
        not isinstance(checks, list)
        or len(checks) != len(expected_checks)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"id", "result", "severity"}
            for item in checks
        )
        or {
            (item.get("id"), item.get("result"), item.get("severity"))
            for item in checks
        } != expected_checks
    ):
        raise AudioStageStatusError("preliminary stage checks are invalid")
    return manifest, hashes


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_final(root: Path, release_id: str, provider: str) -> None:
    manifest_path = root / "04_audio/AUDIO_STAGE_MANIFEST.json"
    manifest = _object(manifest_path)
    expected_keys = _MINIMAX_FINAL_KEYS if provider == "minimax" else _FINAL_KEYS
    if set(manifest) != expected_keys:
        raise AudioStageStatusError("final audio manifest fields are invalid")
    if (
        manifest.get("schema_version") != "audio-stage-manifest.v1"
        or manifest.get("release_id") != release_id
        or manifest.get("next_stage_status") != "ready_for_image_task_planning"
        or manifest.get("preliminary_manifest_path") != "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
        or manifest.get("storyboard_plan_path") != "04_audio/STORYBOARD_AUDIO_PLAN.json"
    ):
        raise AudioStageStatusError("final audio manifest identity is invalid")
    for field in ("input_digest", "preliminary_manifest_sha256", "storyboard_plan_sha256", "caption_timeline_sha256", "stage_manifest_sha256"):
        if not isinstance(manifest.get(field), str) or not _SHA_RE.fullmatch(manifest[field]):
            raise AudioStageStatusError(f"final {field} is invalid")
    if not isinstance(manifest.get("media_report"), Mapping):
        raise AudioStageStatusError("final media report is invalid")
    _verify_provenance(manifest, provider)
    if provider == "minimax":
        if (
            manifest.get("provider") != "minimax"
            or manifest.get("provider_policy") != "minimax_required"
            or not isinstance(manifest.get("voice_id"), str)
            or not manifest["voice_id"]
        ):
            raise AudioStageStatusError("final MiniMax provider identity is invalid")
    preliminary_path = _safe_path(root, "04_audio/AUDIO_PRELIMINARY_MANIFEST.json", "preliminary manifest")
    if sha256_file(preliminary_path) != manifest["preliminary_manifest_sha256"]:
        raise AudioStageStatusError("final manifest has a stale preliminary binding")
    preliminary, preliminary_hashes = _verify_preliminary_structure(root, release_id, provider=provider, final_exists=True)
    del preliminary
    expected_audio = {
        path: digest for path, digest in preliminary_hashes.items() if path.startswith("assets/audio/")
    }
    if manifest.get("preliminary_audio_hashes") != expected_audio:
        raise AudioStageStatusError("final preliminary audio binding is incomplete or inconsistent")
    _hash_table(root, manifest.get("preliminary_audio_hashes"))

    plan_path = _safe_path(root, "04_audio/STORYBOARD_AUDIO_PLAN.json", "storyboard audio plan")
    if sha256_file(plan_path) != manifest["storyboard_plan_sha256"]:
        raise AudioStageStatusError("final manifest plan binding is stale")
    final_hashes = _hash_table(root, manifest.get("final_output_hashes"))

    audio_meta = _object(_safe_path(root, "audio_meta.json", "final audio metadata"))
    if provider == "minimax" and audio_meta.get("timing_source") != "provider":
        raise AudioStageStatusError("final MiniMax audio metadata has no provider timing authority")
    if _canonical_sha(audio_meta.get("captions")) != manifest["caption_timeline_sha256"]:
        raise AudioStageStatusError("final caption timeline fingerprint is stale")

    stage_relative = manifest.get("stage_manifest_path")
    if not isinstance(stage_relative, str) or not stage_relative.startswith("manifests/stages/audio_final/"):
        raise AudioStageStatusError("final stage manifest path is invalid")
    if final_hashes.get(stage_relative) != manifest["stage_manifest_sha256"]:
        raise AudioStageStatusError("final stage binding disagrees with the output table")
    stage_path = _safe_path(root, stage_relative, "final stage manifest")
    stage = _object(stage_path)
    if set(stage) != {"schema_version","manifest_id","project_id","stage","release_id","producer","status","inputs","outputs","checks"}:
        raise AudioStageStatusError("final stage manifest fields are invalid")
    expected_inputs = {
        "digest": manifest["input_digest"],
        "preliminary_manifest_sha256": manifest["preliminary_manifest_sha256"],
        "storyboard_plan_sha256": manifest["storyboard_plan_sha256"],
    }
    if (
        stage.get("schema_version") != "1.0"
        or stage.get("project_id") != root.name
        or stage.get("stage") != "audio_final"
        or stage.get("release_id") != release_id
        or stage.get("producer") != {"tool":"book-video-factory-audio-stage"}
        or stage.get("status") != "success"
        or stage.get("inputs") != expected_inputs
    ):
        raise AudioStageStatusError("final stage manifest identity is invalid")
    _verify_stage_outputs(root, stage, final_hashes, stage_relative)
    checks = stage.get("checks")
    expected_check_ids = {
        "all_phase2_beats_disposed", "all_display_captions_bound", "audio_hashes_unchanged",
        "density_contract",
    }
    expected_check_ids.add("provider_audio_timing_only" if provider == "minimax" else "real_audio_timing_only")
    if (
        not isinstance(checks, list)
        or len(checks) != len(expected_check_ids)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"id", "result"}
            or item.get("result") != "pass"
            for item in checks
        )
        or {item.get("id") for item in checks} != expected_check_ids
    ):
        raise AudioStageStatusError("final stage checks are invalid")


def _verify_preliminary(root: Path, release_id: str, provider: str) -> None:
    _verify_preliminary_structure(root, release_id, provider=provider, final_exists=False)


def audio_stage_status(project: Path, release_id: str) -> str:
    root = project.expanduser().resolve()
    visual = visual_stage_next_status(root, release_id)
    if visual not in {"ready_for_edge_tts", "ready_for_narration"}:
        return visual
    input_path = root / "04_audio/AUDIO_STAGE_INPUT.json"
    if not input_path.is_file() or input_path.is_symlink():
        return "blocked_by_missing_audio_stage_input"
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "blocked_by_missing_audio_stage_input"
    if not isinstance(input_payload, dict):
        return "blocked_by_missing_audio_stage_input"
    try:
        input_data = validate_audio_stage_input(root, input_payload)
    except AudioStageContractError:
        return "blocked_by_invalid_audio_stage_input"
    if input_data["release_id"] != release_id:
        return "blocked_by_invalid_audio_stage_input"
    provider = input_data["provider"]
    if provider == "minimax":
        if input_data["provider_policy"] != "minimax_required":
            return "blocked_by_invalid_audio_stage_input"
        foundation_path = root / _VOICE_FOUNDATION_RELATIVE
        if not foundation_path.is_file() or foundation_path.is_symlink():
            return "blocked_by_missing_voice_foundation"
        try:
            foundation = validate_voice_foundation(json.loads(foundation_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AudioStageContractError):
            return "blocked_by_invalid_voice_foundation"
        if foundation["release_id"] != release_id or foundation["voice_id"] != input_data["voice"]:
            return "blocked_by_invalid_voice_foundation"
    final_path = root / "04_audio/AUDIO_STAGE_MANIFEST.json"
    if final_path.is_file():
        try:
            _verify_final(root, release_id, provider)
        except (AudioStageStatusError, OSError):
            return "blocked_by_audio_manifest_integrity"
        return "ready_for_image_task_planning"
    preliminary_path = root / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    if preliminary_path.is_file():
        try:
            _verify_preliminary(root, release_id, provider)
        except (AudioStageStatusError, OSError):
            return "blocked_by_audio_manifest_integrity"
        return "awaiting_audio_storyboard_plan"
    return visual
