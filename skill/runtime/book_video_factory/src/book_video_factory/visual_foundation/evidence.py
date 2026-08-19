"""Scene reference evidence: per-task proof that a generated scene consumed the exact
concrete reference inputs from its task reference contract."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_foundation.contracts import VisualFoundationError
from book_video_factory.visual_foundation.runner import verify_reference_inputs_match_pack

SCENE_REFERENCE_EVIDENCE_REL = "06_visual_production/SCENE_REFERENCE_EVIDENCE.json"
_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{7,255}$")
_PLACEHOLDERS = {"fake", "todo", "pending", "unknown", "placeholder", "none", "null", "test"}


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_scene_reference_evidence(root: Path) -> dict[str, dict[str, Any]]:
    root = root.expanduser().resolve()
    path = safe_project_output(root, Path(SCENE_REFERENCE_EVIDENCE_REL))
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualFoundationError(f"scene reference evidence is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise VisualFoundationError("scene reference evidence must be an object")
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def _validate_reference_inputs(root: Path, inputs: Sequence[Mapping[str, Any]], label: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(inputs):
        if not isinstance(item, Mapping):
            raise VisualFoundationError(f"{label}[{index}] must be an object")
        role = item.get("role")
        image_path = item.get("image_path")
        image_sha256 = item.get("image_sha256")
        reference_id = item.get("reference_id", "")
        if not isinstance(role, str) or not role.strip():
            raise VisualFoundationError(f"{label}[{index}] role is required")
        if not isinstance(image_path, str) or not image_path.strip():
            raise VisualFoundationError(f"{label}[{index}] image_path is required")
        if not isinstance(image_sha256, str) or len(image_sha256) != 64:
            raise VisualFoundationError(f"{label}[{index}] image_sha256 is required")
        target = safe_project_output(root, Path(image_path))
        if target.is_symlink() or not target.is_file():
            raise VisualFoundationError(f"{label}[{index}] reference image is missing: {image_path}")
        if sha256_file(target) != image_sha256:
            raise VisualFoundationError(f"{label}[{index}] reference image hash is stale: {image_path}")
        entry = {
            "role": str(role),
            "reference_id": str(reference_id or ""),
            "image_path": str(image_path),
            "image_sha256": str(image_sha256),
        }
        meta = item.get("meta")
        if isinstance(meta, Mapping):
            entry["meta"] = {str(k): str(v) for k, v in meta.items()}
        normalized.append(entry)
    return normalized


def _validate_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or _CALL_ID.fullmatch(value) is None:
        raise VisualFoundationError("generation_attempt_id must be a real identifier")
    lowered = value.casefold()
    if lowered in _PLACEHOLDERS or any(token in lowered for token in ("placeholder", "dummy", "example")):
        raise VisualFoundationError("generation_attempt_id must not be a placeholder")
    return value


def _validate_provider_receipt(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or _CALL_ID.fullmatch(value) is None:
        raise VisualFoundationError("provider_receipt must be a real provider identifier")
    lowered = value.casefold()
    if lowered in _PLACEHOLDERS or any(token in lowered for token in ("placeholder", "dummy", "example")):
        raise VisualFoundationError("provider_receipt must not be a placeholder")
    return value


def write_scene_reference_evidence(
    root: Path,
    *,
    release_id: str,
    task_id: str,
    reference_pack: Mapping[str, Any] | None,
    reference_inputs: Sequence[Mapping[str, Any]],
    generation_attempt_id: str,
    generation_mode: str,
    provider: str,
    provider_receipt: str,
    prompt_sha256: str,
    output_image_sha256: str,
) -> Path:
    """Transactional append of one task's reference evidence. Fails closed on mismatch."""
    root = root.expanduser().resolve()
    normalized = _validate_reference_inputs(root, reference_inputs, "reference_inputs")
    attempt_id = _validate_attempt_id(generation_attempt_id)
    receipt = _validate_provider_receipt(provider_receipt)
    if reference_pack is not None:
        try:
            verify_reference_inputs_match_pack(normalized, reference_pack)
        except Exception as error:
            raise VisualFoundationError(str(error)) from error
    evidence = load_scene_reference_evidence(root)
    if task_id in evidence:
        raise VisualFoundationError(f"scene reference evidence already exists for task {task_id}")
    record = {
        "schema_version": "scene-reference-evidence.v1",
        "release_id": release_id,
        "task_id": task_id,
        "generation_attempt_id": attempt_id,
        "provider": provider,
        "provider_receipt": receipt,
        "generation_mode": generation_mode,
        "prompt_sha256": prompt_sha256,
        "reference_inputs": normalized,
        "output_image_sha256": output_image_sha256,
    }
    evidence[task_id] = record
    path = safe_project_output(root, Path(SCENE_REFERENCE_EVIDENCE_REL))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_bytes(_pretty(evidence))
        os.replace(tmp, path)
    except Exception as error:
        tmp.unlink(missing_ok=True)
        raise VisualFoundationError(f"scene reference evidence transaction failed: {error}") from error
    return path


def verify_scene_reference_evidence(root: Path, jobs: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Re-hash every recorded reference input and ensure it still matches the current task pack."""
    root = root.expanduser().resolve()
    evidence = load_scene_reference_evidence(root)
    task_to_pack: dict[str, dict[str, Any]] = {}
    for job in jobs:
        packs = job.get("reference_packs")
        if not isinstance(packs, dict):
            continue
        for task_id, pack in packs.items():
            if isinstance(pack, dict):
                task_to_pack[str(task_id)] = pack
    for task_id, record in evidence.items():
        if task_id not in task_to_pack:
            continue
        _validate_reference_inputs(root, record.get("reference_inputs", []), "reference_inputs")
        _validate_provider_receipt(record.get("provider_receipt"))
        verify_reference_inputs_match_pack(record.get("reference_inputs", []), task_to_pack[task_id])
    return evidence
