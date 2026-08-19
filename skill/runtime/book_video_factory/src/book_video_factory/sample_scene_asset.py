"""Register one real, static sample-scene candidate without forging a full-book asset."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from book_video_factory.manifests import safe_project_output, sha256_file, write_immutable_json
from book_video_factory.sample_visual_pilot import _excerpt_sequences
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    verify_visual_asset_manifest,
)


class SampleSceneAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleSceneAssetResult:
    status: str
    path: Path
    asset_path: Path
    scene_id: str


_INPUT_SCHEMA = "sample-scene-asset-input.v2"
_OUTPUT_SCHEMA = "sample-scene-asset.v2"
_SCENE_ID = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{7,255}$")
_PROVIDERS = {"host-imagegen", "gemini-web", "flow-web"}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SampleSceneAssetError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise SampleSceneAssetError(f"{label} must be a JSON object")
    return value


def _asset_reference(asset: dict[str, Any]) -> dict[str, str]:
    return {"task_id": asset["task_id"], "path": asset["path"], "sha256": asset["sha256"]}


def _require_asset_references(
    *,
    task_ids: Any,
    assets_by_task: dict[str, dict[str, Any]],
    label: str,
) -> list[dict[str, str]]:
    if not isinstance(task_ids, list) or len(task_ids) != len(set(task_ids)):
        raise SampleSceneAssetError(f"{label} must be a duplicate-free array")
    result: list[dict[str, str]] = []
    for task_id in task_ids:
        if not isinstance(task_id, str) or not task_id:
            raise SampleSceneAssetError(f"{label} contains an invalid task id")
        asset = assets_by_task.get(task_id)
        if asset is None or asset.get("task_kind") == "lookdev":
            raise SampleSceneAssetError(f"{label} must reference a current non-LookDev asset: {task_id}")
        result.append(_asset_reference(asset))
    return result


def _validate_image(path: Path) -> tuple[str, int, int, str]:
    if not path.is_file() or path.suffix.lower() != ".png":
        raise SampleSceneAssetError("sample scene source must be a real PNG file")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height, mode = *image.size, image.mode
    except (OSError, UnidentifiedImageError) as error:
        raise SampleSceneAssetError("sample scene source is not a decodable PNG") from error
    if (width, height) != (1920, 1080) or mode not in {"RGB", "RGBA"}:
        raise SampleSceneAssetError("sample scene source must be RGB/RGBA 1920x1080 PNG")
    return sha256_file(path), width, height, mode


def register_sample_scene_asset(project: Path, input_path: Path) -> SampleSceneAssetResult:
    """Copy and bind a real Host ImageGen sample scene to its current evidence."""
    root = project.expanduser().resolve()
    payload = _load_object(input_path.expanduser().resolve(), "sample scene asset input")
    if payload.get("schema_version") != _INPUT_SCHEMA:
        raise SampleSceneAssetError("sample scene asset input schema is invalid")
    release_id = payload.get("release_id")
    scene_id = payload.get("scene_id")
    if not isinstance(release_id, str) or not release_id.strip() or not isinstance(scene_id, str) or _SCENE_ID.fullmatch(scene_id) is None:
        raise SampleSceneAssetError("sample scene release_id or scene_id is invalid")
    excerpt_relative = payload.get("sample_excerpt_path")
    expected_excerpt_hash = payload.get("sample_excerpt_sha256")
    if not isinstance(excerpt_relative, str) or not isinstance(expected_excerpt_hash, str):
        raise SampleSceneAssetError("sample scene excerpt binding is invalid")
    excerpt_path = safe_project_output(root, Path(excerpt_relative))
    if not excerpt_path.is_file() or sha256_file(excerpt_path) != expected_excerpt_hash:
        raise SampleSceneAssetError("sample scene excerpt is stale or missing")
    excerpt = _load_object(excerpt_path, "sample excerpt")
    if excerpt.get("release_id") != release_id:
        raise SampleSceneAssetError("sample scene excerpt release differs from asset release")
    source_sequence_ids = payload.get("source_sequence_ids")
    excerpt_sequences = _excerpt_sequences(excerpt)
    if (
        not isinstance(source_sequence_ids, list)
        or not source_sequence_ids
        or not all(isinstance(value, int) for value in source_sequence_ids)
        or source_sequence_ids != sorted(set(source_sequence_ids))
        or not set(source_sequence_ids).issubset(excerpt_sequences)
    ):
        raise SampleSceneAssetError("sample scene source sequence must be a non-empty monotone subset of the excerpt")
    prompt = payload.get("prompt")
    prompt_sha256 = payload.get("prompt_sha256")
    tool_call_id = payload.get("tool_call_id")
    provider = payload.get("provider")
    if (
        not isinstance(prompt, str) or not prompt.strip()
        or not isinstance(prompt_sha256, str) or prompt_sha256 != hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        or not isinstance(tool_call_id, str) or _CALL_ID.fullmatch(tool_call_id) is None
        or not isinstance(provider, str) or provider not in _PROVIDERS
    ):
        raise SampleSceneAssetError("sample scene prompt or generation call evidence is invalid")
    source_value = payload.get("source")
    if not isinstance(source_value, str) or not source_value.strip():
        raise SampleSceneAssetError("sample scene source is invalid")
    source = Path(source_value).expanduser().resolve()
    source_sha256, width, height, mode = _validate_image(source)

    try:
        verified = verify_visual_asset_manifest(root, require_complete=False)
    except VisualAssetRegistrationError as error:
        raise SampleSceneAssetError(f"sample scene reference assets are stale or invalid: {error}") from error
    if verified.visual_stage_manifest.get("release_id") != release_id:
        raise SampleSceneAssetError("sample scene visual release differs from asset release")
    identity_references = _require_asset_references(
        task_ids=payload.get("identity_reference_task_ids"),
        assets_by_task=verified.assets_by_task,
        label="identity_reference_task_ids",
    )
    location_anchors = _require_asset_references(
        task_ids=payload.get("location_anchor_task_ids"),
        assets_by_task=verified.assets_by_task,
        label="location_anchor_task_ids",
    )
    required_entities = payload.get("required_entities")
    forbidden_entities = payload.get("forbidden_entities")
    if (
        not isinstance(required_entities, list) or not required_entities
        or not isinstance(forbidden_entities, list)
        or not all(isinstance(value, str) and value.strip() for value in [*required_entities, *forbidden_entities])
    ):
        raise SampleSceneAssetError("sample scene entity contract is invalid")

    asset_relative = f"assets/generated/sample_scenes/{scene_id}.png"
    record_relative = f"03_images_生成图片/sample_scene_assets/{scene_id}.json"
    target = safe_project_output(root, Path(asset_relative))
    record_path = safe_project_output(root, Path(record_relative))
    manifest = {
        "schema_version": _OUTPUT_SCHEMA,
        "release_id": release_id,
        "sample_id": excerpt["sample_id"],
        "scene_id": scene_id,
        "sample_excerpt": {"path": excerpt_relative, "sha256": expected_excerpt_hash},
        "source_sequence_ids": source_sequence_ids,
        "asset": {"path": asset_relative, "sha256": source_sha256, "width": width, "height": height, "mode": mode},
        "generation": {"provider": provider, "tool_call_id": tool_call_id, "prompt_sha256": prompt_sha256},
        "identity_references": identity_references,
        "location_anchors": location_anchors,
        "required_entities": required_entities,
        "forbidden_entities": forbidden_entities,
        "static_image_policy": {"no_camera_motion": True},
        "human_review_status": "pending",
        "scope_note": "Sample-only scene candidate; it is not a full-book scene_visual approval.",
    }
    if record_path.exists() or target.exists():
        if not record_path.is_file() or not target.is_file():
            raise SampleSceneAssetError("sample scene asset outputs are incomplete")
        existing = _load_object(record_path, "existing sample scene asset")
        if sha256_file(target) != existing.get("asset", {}).get("sha256"):
            raise SampleSceneAssetError("existing sample scene asset file hash is stale")
        if existing != manifest:
            raise SampleSceneAssetError("existing sample scene asset is stale or differs from current evidence")
        return SampleSceneAssetResult("unchanged", record_path, target, scene_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    try:
        if sha256_file(target) != source_sha256:
            raise SampleSceneAssetError("sample scene copy hash mismatch")
        write_immutable_json(record_path, manifest)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return SampleSceneAssetResult("created", record_path, target, scene_id)
