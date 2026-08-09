from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.director_stage.compiler import DirectorStageError, compile_director_stage
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.reference_visuals.catalog import load_reference_catalog
from book_video_factory.visual_stage.asset_registry import _machine_diagnostic, _validate_source


class SceneAssetError(RuntimeError):
    """A production scene asset or its generation evidence is invalid."""


@dataclass(frozen=True)
class SceneAssetResult:
    status: str
    manifest_path: Path
    task_id: str
    asset_path: Path
    registered_count: int
    total_count: int
    next_stage_status: str


_MANIFEST = "06_visual_production/SCENE_ASSET_MANIFEST.json"
_ALLOWED_FIELDS = {
    "schema_version", "release_id", "director_stage_manifest_sha256", "provider",
    "assets", "registered_asset_count", "task_count", "last_registered_at",
    "next_stage_status",
}
_SCENE_PROVIDERS = {"host-imagegen", "gemini-web", "flow-web"}


_ASSET_FIELDS = {
    "task_id", "scene_id", "path", "sha256", "bytes", "width", "height", "mode",
    "prompt_sha256", "provider", "generation_lane", "tool_call_id", "style_reference_evidence",
    "identity_reference_evidence", "machine_diagnostic", "semantic_review_status",
    "reality_review_status", "identity_review_status", "human_review_status", "registered_at",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SceneAssetError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SceneAssetError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise SceneAssetError(f"{label} must be an object")
    return value


def _tasks(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "05_director/IMAGE_TASKS.jsonl"
    if path.is_symlink() or not path.is_file():
        raise SceneAssetError("production image task queue is missing")
    result: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError as error:
            raise SceneAssetError(f"image task line {number} is invalid") from error
        if not isinstance(task, dict) or task.get("schema_version") != "production-image-task.v1":
            raise SceneAssetError(f"image task line {number} has an invalid contract")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in result:
            raise SceneAssetError("production image task IDs are invalid")
        result[task_id] = task
    if not result:
        raise SceneAssetError("production image task queue is empty")
    return result


def _phase3_assets(root: Path) -> dict[str, dict[str, Any]]:
    manifest = _load(root / "03_images_生成图片/VISUAL_ASSET_MANIFEST.json", "Phase 3 visual asset manifest")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise SceneAssetError("Phase 3 visual assets are invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("task_id"), str):
            raise SceneAssetError("Phase 3 visual asset record is invalid")
        path = safe_project_output(root, Path(item.get("path", "")))
        if path.is_symlink() or not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise SceneAssetError(f"Phase 3 identity asset is stale: {item.get('task_id')}")
        result[item["task_id"]] = item
    return result


def _manifest(root: Path, release_id: str, director_sha: str, count: int, provider: str = "host-imagegen") -> dict[str, Any]:
    path = root / _MANIFEST
    if not path.exists():
        return {
            "schema_version": "scene-asset-manifest.v1",
            "release_id": release_id,
            "director_stage_manifest_sha256": director_sha,
            "provider": provider,
            "assets": [],
            "registered_asset_count": 0,
            "task_count": count,
            "last_registered_at": None,
            "next_stage_status": "awaiting_scene_assets",
        }
    value = _load(path, "scene asset manifest")
    if set(value) != _ALLOWED_FIELDS or value.get("schema_version") != "scene-asset-manifest.v1":
        raise SceneAssetError("scene asset manifest fields are invalid")
    if value.get("release_id") != release_id or value.get("director_stage_manifest_sha256") != director_sha:
        raise SceneAssetError("scene asset manifest belongs to different director inputs")
    manifest_provider = value.get("provider")
    if manifest_provider not in _SCENE_PROVIDERS | {"mixed"} or value.get("task_count") != count:
        raise SceneAssetError("scene asset manifest provider or task count is invalid")
    assets = value.get("assets")
    if not isinstance(assets, list) or value.get("registered_asset_count") != len(assets):
        raise SceneAssetError("scene asset manifest count is invalid")
    seen: set[str] = set(); hashes: set[str] = set()
    for item in assets:
        if not isinstance(item, dict) or set(item) != _ASSET_FIELDS:
            raise SceneAssetError("scene asset record fields are invalid")
        if item["task_id"] in seen or item["sha256"] in hashes:
            raise SceneAssetError("scene asset manifest contains duplicate task or image")
        path = safe_project_output(root, Path(item["path"]))
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise SceneAssetError(f"registered scene asset was modified: {item['task_id']}")
        if item.get("provider") not in _SCENE_PROVIDERS:
            raise SceneAssetError(f"scene asset provider is invalid: {item.get('task_id')}")
        # Final-manifest invariant (Item 6): an asset's registered provider must
        # equal the production task's generation lane. A manifest that records a
        # valid-but-mismatched provider (e.g. gemini-web under a host-imagegen
        # lane) is rejected here even though both providers are individually
        # sanctioned, so cross-lane provenance laundering cannot survive the
        # manifest boundary.
        if item.get("provider") != item.get("generation_lane"):
            raise SceneAssetError(
                f"scene asset {item.get('task_id')} provider {item.get('provider')!r} "
                f"does not match its generation_lane {item.get('generation_lane')!r}"
            )
        seen.add(item["task_id"]); hashes.add(item["sha256"])
    asset_providers = {item.get("provider") for item in assets}
    if manifest_provider == "mixed":
        if len(asset_providers) < 2:
            raise SceneAssetError("scene asset manifest provider is inconsistent")
    elif asset_providers != {manifest_provider}:
        raise SceneAssetError("scene asset manifest provider is inconsistent")
    return value


def _call_id(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) < 8:
        raise SceneAssetError("tool_call_id must be a real image-generation call identifier")
    lowered = value.casefold()
    if any(token in lowered for token in ("fake", "dummy", "placeholder", "example", "pending")):
        raise SceneAssetError("tool_call_id must not be a placeholder")
    return value


def _exact_ids(declared: list[str], expected: list[str], label: str) -> list[str]:
    if not isinstance(declared, list) or any(not isinstance(item, str) or not item or item != item.strip() for item in declared):
        raise SceneAssetError(f"{label} must be an array of trimmed identifiers")
    if declared != expected:
        raise SceneAssetError(f"{label} does not match the production task")
    return declared


def _validate_asset_provider(provider: str, generation_lane: str | None) -> None:
    # Chain 5 (#24): the asset's provider must be the exact generation lane the
    # production task was planned for. A mismatch (e.g. a gemini-web asset
    # logged under a host-imagegen lane) is rejected so provenance cannot be
    # laundered across lanes. ``generation_lane`` is required and must equal
    # the registered provider.
    if provider not in _SCENE_PROVIDERS:
        raise SceneAssetError("scene asset provider is invalid")
    if generation_lane is None:
        raise SceneAssetError("production task is missing its generation_lane; provenance cannot be verified")
    if provider != generation_lane:
        raise SceneAssetError(
            f"scene asset provider {provider!r} does not match the task generation_lane {generation_lane!r}"
        )


def register_scene_asset(
    project: Path,
    *,
    task_id: str,
    source: Path,
    tool_call_id: str,
    provider: str = "host-imagegen",
    style_reference_ids: list[str],
    identity_reference_task_ids: list[str],
) -> SceneAssetResult:
    root = project.expanduser().resolve()
    try:
        director = compile_director_stage(root)
    except DirectorStageError as error:
        raise SceneAssetError(f"director stage is not current: {error}") from error
    tasks = _tasks(root)
    task = tasks.get(task_id)
    if task is None:
        raise SceneAssetError(f"unknown production image task: {task_id}")
    _call_id(tool_call_id)
    _validate_asset_provider(provider, task.get("generation_lane"))
    _exact_ids(style_reference_ids, list(task["style_reference_ids"]), "style_reference_ids")
    _exact_ids(identity_reference_task_ids, list(task["identity_reference_task_ids"]), "identity_reference_task_ids")
    phase3 = _phase3_assets(root)
    identity_evidence: list[dict[str, Any]] = []
    for reference_id in identity_reference_task_ids:
        asset = phase3.get(reference_id)
        if asset is None:
            raise SceneAssetError(f"identity reference asset is not registered: {reference_id}")
        identity_evidence.append({
            "task_id": reference_id,
            "path": asset["path"],
            "sha256": asset["sha256"],
            "role": "identity_reference",
        })
    catalog = load_reference_catalog()
    known = catalog.by_id()
    style_evidence: list[dict[str, Any]] = []
    for reference_id in style_reference_ids:
        entry = known.get(reference_id)
        if entry is None:
            raise SceneAssetError(f"unknown style reference: {reference_id}")
        style_evidence.append({
            "reference_id": reference_id,
            "sha256": entry.sha256,
            "profile_sha256": entry.profile_sha256,
            "role": "style_only",
        })
    source_path = source.expanduser().resolve()
    try:
        source_path.relative_to(root)
    except ValueError:
        pass
    digest, media = _validate_source(
        source_path, {entry.sha256 for entry in catalog.entries}, task.get("canvas")
    )
    profile = _load(root / "03_images_生成图片/BOOK_VISUAL_PROFILE.json", "visual profile")
    envelope = None
    for palette in profile.get("palette_profiles", []):
        if isinstance(palette, dict) and palette.get("palette_id") == task["palette_id"]:
            envelope = palette.get("diagnostic_envelope")
            break
    if not isinstance(envelope, dict):
        raise SceneAssetError("task palette diagnostic envelope is missing")
    diagnostic = _machine_diagnostic(source_path, envelope)
    director_sha = sha256_file(director.manifest_path)
    manifest = _manifest(root, task["release_id"], director_sha, len(tasks), provider)
    by_task = {item["task_id"]: item for item in manifest["assets"]}
    if task_id in by_task:
        existing = by_task[task_id]
        if existing["sha256"] == digest and existing["tool_call_id"] == tool_call_id:
            return SceneAssetResult(
                "unchanged", root / _MANIFEST, task_id, root / existing["path"],
                len(manifest["assets"]), len(tasks), manifest["next_stage_status"],
            )
        raise SceneAssetError(f"scene task is already registered with different evidence: {task_id}")
    if any(item["sha256"] == digest for item in manifest["assets"]):
        raise SceneAssetError("the same generated image cannot satisfy multiple scene tasks")
    output = safe_project_output(root, Path(task["output_target"]))
    published_output = False
    if output.exists():
        if source_path != output:
            raise SceneAssetError(f"unmanaged scene output already exists: {task['output_target']}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        shutil.copyfile(source_path, temp)
        if sha256_file(temp) != digest:
            temp.unlink(missing_ok=True)
            raise SceneAssetError("scene image copy changed bytes")
        os.replace(temp, output)
        published_output = True
    record = {
        "task_id": task_id,
        "scene_id": task["scene_id"],
        "path": task["output_target"],
        "sha256": digest,
        "bytes": output.stat().st_size,
        "width": media["width"],
        "height": media["height"],
        "mode": media["mode"],
        "prompt_sha256": task["prompt_sha256"],
        "provider": provider,
        "generation_lane": task.get("generation_lane"),
        "tool_call_id": tool_call_id,
        "style_reference_evidence": style_evidence,
        "identity_reference_evidence": identity_evidence,
        "machine_diagnostic": diagnostic,
        "semantic_review_status": "pending",
        "reality_review_status": "pending",
        "identity_review_status": "pending",
        "human_review_status": "pending",
        "registered_at": _now(),
    }
    manifest["assets"].append(record)
    manifest["assets"].sort(key=lambda item: item["task_id"])
    manifest["registered_asset_count"] = len(manifest["assets"])
    manifest["last_registered_at"] = record["registered_at"]
    manifest["next_stage_status"] = "awaiting_scene_review" if len(manifest["assets"]) == len(tasks) else "awaiting_scene_assets"
    manifest_path = safe_project_output(root, Path(_MANIFEST))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    try:
        tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_manifest, manifest_path)
    except Exception as error:
        tmp_manifest.unlink(missing_ok=True)
        if published_output:
            output.unlink(missing_ok=True)
        raise SceneAssetError(f"scene asset registration transaction failed: {error}") from error
    return SceneAssetResult(
        "created", manifest_path, task_id, output, len(manifest["assets"]), len(tasks), manifest["next_stage_status"]
    )


