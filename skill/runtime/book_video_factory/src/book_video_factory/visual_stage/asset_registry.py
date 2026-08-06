from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from book_video_factory.image_qc import analyze_image
from book_video_factory.manifests import safe_project_output, sha256_file, utc_now
from book_video_factory.reference_visuals.catalog import load_reference_catalog

from .compiler import VisualStageCompileError, VisualStageConflict, compile_visual_stage


class VisualAssetRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualAssetResult:
    status: str
    asset: dict[str, Any]
    manifest_path: Path


@dataclass(frozen=True)
class VerifiedVisualAssets:
    project: Path
    visual_stage_manifest: dict[str, Any]
    tasks: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    assets_by_task: dict[str, dict[str, Any]]


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{7,255}$")
_PLACEHOLDERS = {"fake", "todo", "pending", "unknown", "placeholder", "none", "null", "test"}
_TASK_FILES = ("ANCHOR_TASKS.jsonl", "LOOKDEV_TASKS.jsonl")
_MANIFEST_RELATIVE = "03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
_MANIFEST_FIELDS = {
    "schema_version", "release_id", "visual_stage_digest", "provider",
    "assets", "registered_asset_count", "last_registered_at",
}
_ASSET_FIELDS = {
    "task_id", "task_kind", "category", "path", "sha256", "bytes",
    "prompt_sha256", "provider", "tool_call_id", "width", "height",
    "mode", "palette_id", "lighting_id", "style_reference_evidence",
    "identity_reference_evidence", "machine_diagnostic",
    "semantic_review_status", "reality_review_status", "human_review_status",
    "registered_at",
}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualAssetRegistrationError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise VisualAssetRegistrationError(f"{label} must be a JSON object")
    return value


def _verify_visual_stage(root: Path) -> dict[str, Any]:
    input_path = root / "03_images_生成图片/VISUAL_STAGE_INPUT.json"
    if not input_path.is_file():
        raise VisualAssetRegistrationError("visual stage input is missing")
    try:
        result = compile_visual_stage(root, input_path)
    except (VisualStageCompileError, VisualStageConflict, OSError, ValueError) as error:
        raise VisualAssetRegistrationError(f"visual stage is not current: {error}") from error
    return _load_json(result.manifest_path, "visual stage manifest")


def _load_tasks(root: Path) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for filename in _TASK_FILES:
        path = root / "03_images_生成图片" / filename
        if not path.is_file():
            raise VisualAssetRegistrationError(f"visual task file is missing: {filename}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                task = json.loads(line)
            except json.JSONDecodeError as error:
                raise VisualAssetRegistrationError(
                    f"visual task is invalid JSON: {filename}:{line_number}"
                ) from error
            if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
                raise VisualAssetRegistrationError(f"visual task is invalid: {filename}:{line_number}")
            task_id = task["task_id"]
            if task_id in tasks:
                raise VisualAssetRegistrationError(f"duplicate visual task: {task_id}")
            tasks[task_id] = task
    return tasks


def _load_manifest(root: Path, visual_stage_digest: str, release_id: str) -> dict[str, Any]:
    path = root / _MANIFEST_RELATIVE
    if not path.exists():
        return {
            "schema_version": "visual-asset-manifest.v1",
            "release_id": release_id,
            "visual_stage_digest": visual_stage_digest,
            "provider": "host-imagegen",
            "assets": [],
            "registered_asset_count": 0,
            "last_registered_at": None,
        }
    value = _load_json(path, "visual asset manifest")
    if set(value) != _MANIFEST_FIELDS:
        raise VisualAssetRegistrationError("visual asset manifest fields are invalid")
    if value.get("schema_version") != "visual-asset-manifest.v1":
        raise VisualAssetRegistrationError("unsupported visual asset manifest schema")
    if value.get("provider") != "host-imagegen":
        raise VisualAssetRegistrationError("visual asset manifest provider is invalid")
    if value.get("release_id") != release_id or value.get("visual_stage_digest") != visual_stage_digest:
        raise VisualAssetRegistrationError("visual asset manifest is bound to a different visual stage")
    assets = value.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise VisualAssetRegistrationError("visual asset manifest assets must be an array")
    return value


def _validate_call_id(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or _CALL_ID.fullmatch(value) is None:
        raise VisualAssetRegistrationError("tool_call_id must be a real host ImageGen call identifier")
    lowered = value.casefold()
    if lowered in _PLACEHOLDERS or any(token in lowered for token in ("placeholder", "dummy", "example")):
        raise VisualAssetRegistrationError("tool_call_id must not be a placeholder")
    return value


def _validate_declared_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise VisualAssetRegistrationError(f"{label} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item or item != item.strip():
            raise VisualAssetRegistrationError(f"{label}[{index}] must be a trimmed string")
        if item in result:
            raise VisualAssetRegistrationError(f"{label} contains duplicate identifiers")
        result.append(item)
    return result


def _valid_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise VisualAssetRegistrationError(f"{label} is invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise VisualAssetRegistrationError(f"{label} is invalid") from error
    return value


def _style_reference_evidence(catalog, reference_ids: list[str]) -> list[dict[str, Any]]:
    known = catalog.by_id()
    evidence: list[dict[str, Any]] = []
    for reference_id in reference_ids:
        entry = known.get(reference_id)
        if entry is None:
            raise VisualAssetRegistrationError(f"unknown style reference: {reference_id}")
        evidence.append({
            "reference_id": reference_id,
            "sha256": entry.sha256,
            "profile_sha256": entry.profile_sha256,
            "role": "style_only",
        })
    return evidence


def _identity_reference_evidence(
    reference_task_ids: list[str],
    assets_by_task: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for task_id in reference_task_ids:
        asset = assets_by_task.get(task_id)
        if asset is None:
            raise VisualAssetRegistrationError(f"identity reference task is not registered: {task_id}")
        evidence.append({
            "task_id": task_id,
            "path": asset["path"],
            "sha256": asset["sha256"],
            "role": "identity_reference",
        })
    return evidence


def _validate_source(
    source: Path,
    catalog_hashes: set[str],
    expected_canvas: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    path = source.expanduser().resolve()
    if not path.is_file():
        raise VisualAssetRegistrationError(f"source image is missing: {path}")
    digest = sha256_file(path)
    if digest in catalog_hashes:
        raise VisualAssetRegistrationError("gold reference images are style evidence and cannot be production assets")
    if path.suffix.lower() != ".png":
        raise VisualAssetRegistrationError("production visual assets must be PNG files")
    try:
        with Image.open(path) as opened:
            opened.verify()
        with Image.open(path) as opened:
            width, height = opened.size
            mode = opened.mode
    except (OSError, UnidentifiedImageError) as error:
        raise VisualAssetRegistrationError("source PNG must be a decodable image") from error
    canvas = expected_canvas or {"width": 1920, "height": 1080}
    expected_size = (canvas.get("width"), canvas.get("height"))
    if (width, height) != expected_size:
        raise VisualAssetRegistrationError(
            f"source image must be exactly {expected_size[0]}x{expected_size[1]}"
        )
    if mode not in {"RGB", "RGBA"}:
        raise VisualAssetRegistrationError("source PNG must use RGB or RGBA color mode")
    return digest, {"width": width, "height": height, "mode": mode}


def _palette_envelope(profile: dict[str, Any], palette_id: str) -> dict[str, list[float]]:
    for palette in profile.get("palette_profiles", []):
        if isinstance(palette, dict) and palette.get("palette_id") == palette_id:
            envelope = palette.get("diagnostic_envelope")
            if isinstance(envelope, dict):
                return envelope
    raise VisualAssetRegistrationError(f"palette profile is missing for task: {palette_id}")


def _machine_diagnostic(source: Path, envelope: dict[str, list[float]]) -> dict[str, Any]:
    metrics = analyze_image(source)
    metrics.pop("path", None)
    warnings: list[dict[str, Any]] = []
    for key, limits in envelope.items():
        if not isinstance(limits, list) or len(limits) != 2:
            raise VisualAssetRegistrationError(f"invalid diagnostic envelope: {key}")
        observed = metrics.get(key)
        if not isinstance(observed, (int, float)):
            raise VisualAssetRegistrationError(f"image diagnostic is missing metric: {key}")
        if not float(limits[0]) <= float(observed) <= float(limits[1]):
            warnings.append({
                "metric": key,
                "observed": observed,
                "reference_min": limits[0],
                "reference_max": limits[1],
            })
    return {
        "status": "warn" if warnings else "pass",
        "note": "Machine diagnostics are measurements only and never aesthetic or human approval.",
        "metrics": metrics,
        "envelope": envelope,
        "warnings": warnings,
    }


def _verify_manifest_records(
    root: Path,
    *,
    stage_manifest: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    require_complete: bool,
    recompute_diagnostics: bool = True,
    deep_image_validation: bool = True,
) -> VerifiedVisualAssets:
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise VisualAssetRegistrationError("visual asset manifest assets must be an array")
    profile = _load_json(root / "03_images_生成图片/BOOK_VISUAL_PROFILE.json", "book visual profile")
    catalog = load_reference_catalog()
    by_task: dict[str, dict[str, Any]] = {}
    seen_hashes: set[str] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or set(asset) != _ASSET_FIELDS:
            raise VisualAssetRegistrationError(f"visual asset manifest record fields are invalid: {index}")
        task_id = asset.get("task_id")
        if not isinstance(task_id, str) or task_id in by_task or task_id not in tasks:
            raise VisualAssetRegistrationError(f"visual asset manifest has unknown or duplicate task: {task_id}")
        task = tasks[task_id]
        if asset.get("prompt_sha256") != task.get("prompt_sha256"):
            raise VisualAssetRegistrationError(f"visual asset prompt_sha256 mismatch: {task_id}")
        if asset.get("path") != task.get("output_target"):
            raise VisualAssetRegistrationError(f"visual asset path does not match task output: {task_id}")
        if asset.get("provider") != "host-imagegen":
            raise VisualAssetRegistrationError(f"visual asset provider is invalid: {task_id}")
        _validate_call_id(str(asset.get("tool_call_id", "")))
        expected_style = _style_reference_evidence(catalog, list(task.get("style_reference_ids", [])))
        if asset.get("style_reference_evidence") != expected_style:
            raise VisualAssetRegistrationError(f"visual asset style reference evidence mismatch: {task_id}")
        expected_identity = _identity_reference_evidence(
            list(task.get("identity_dependency_task_ids", [])), by_task
        )
        if asset.get("identity_reference_evidence") != expected_identity:
            raise VisualAssetRegistrationError(f"visual asset identity reference evidence mismatch: {task_id}")
        digest = asset.get("sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None or digest in seen_hashes:
            raise VisualAssetRegistrationError(f"visual asset hash is invalid or duplicate: {task_id}")
        try:
            target = safe_project_output(root, Path(str(asset.get("path", ""))))
        except ValueError as error:
            raise VisualAssetRegistrationError(str(error)) from error
        if not target.is_file() or sha256_file(target) != digest:
            raise VisualAssetRegistrationError(f"visual asset file hash mismatch: {task_id}")
        if target.stat().st_size != asset.get("bytes"):
            raise VisualAssetRegistrationError(f"visual asset byte count mismatch: {task_id}")
        if deep_image_validation:
            actual_digest, info = _validate_source(target, set(), task.get("canvas"))
            if actual_digest != digest or asset.get("width") != info["width"] or asset.get("height") != info["height"] or asset.get("mode") != info["mode"]:
                raise VisualAssetRegistrationError(f"visual asset image metadata mismatch: {task_id}")
        elif (
            asset.get("width") != task.get("canvas", {}).get("width")
            or asset.get("height") != task.get("canvas", {}).get("height")
            or asset.get("mode") not in {"RGB", "RGBA"}
        ):
            raise VisualAssetRegistrationError(f"visual asset image metadata is invalid: {task_id}")
        if recompute_diagnostics:
            envelope = _palette_envelope(profile, str(task.get("palette_id", "")))
            diagnostic = _machine_diagnostic(target, envelope)
            if asset.get("machine_diagnostic") != diagnostic:
                raise VisualAssetRegistrationError(f"visual asset machine diagnostic mismatch: {task_id}")
        elif not isinstance(asset.get("machine_diagnostic"), dict):
            raise VisualAssetRegistrationError(f"visual asset machine diagnostic is missing: {task_id}")
        _valid_timestamp(asset.get("registered_at"), f"visual asset registered_at: {task_id}")
        for field in ("semantic_review_status", "reality_review_status", "human_review_status"):
            if asset.get(field) != "pending":
                raise VisualAssetRegistrationError(f"visual asset cannot self-approve {field}: {task_id}")
        by_task[task_id] = asset
        seen_hashes.add(digest)
    if manifest.get("registered_asset_count") != len(assets):
        raise VisualAssetRegistrationError("visual asset manifest registered count mismatch")
    expected_last = assets[-1]["registered_at"] if assets else None
    if manifest.get("last_registered_at") != expected_last:
        raise VisualAssetRegistrationError("visual asset manifest last_registered_at mismatch")
    missing = [task_id for task_id in tasks if task_id not in by_task]
    if require_complete and missing:
        raise VisualAssetRegistrationError(f"missing registered assets: {', '.join(missing)}")
    return VerifiedVisualAssets(
        project=root,
        visual_stage_manifest=stage_manifest,
        tasks=tuple(tasks.values()),
        manifest=manifest,
        assets_by_task=by_task,
    )


def verify_visual_asset_manifest(project: Path, *, require_complete: bool = False) -> VerifiedVisualAssets:
    root = project.expanduser().resolve()
    stage_manifest = _verify_visual_stage(root)
    tasks = _load_tasks(root)
    manifest = _load_manifest(
        root,
        str(stage_manifest.get("visual_stage_digest", "")),
        str(stage_manifest.get("release_id", "")),
    )
    return _verify_manifest_records(
        root, stage_manifest=stage_manifest, tasks=tasks, manifest=manifest,
        require_complete=require_complete, recompute_diagnostics=True, deep_image_validation=True
    )


def _pretty_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _transactional_publish(
    *,
    source: Path,
    target: Path,
    manifest_path: Path,
    manifest_bytes: bytes,
) -> None:
    originals = {
        target: target.read_bytes() if target.is_file() else None,
        manifest_path: manifest_path.read_bytes() if manifest_path.is_file() else None,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_asset: Path | None = None
    temp_manifest: Path | None = None
    replaced: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as handle:
            temp_asset = Path(handle.name)
        shutil.copyfile(source, temp_asset)
        with tempfile.NamedTemporaryFile(prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent, delete=False) as handle:
            temp_manifest = Path(handle.name)
            handle.write(manifest_bytes)
        os.replace(temp_asset, target)
        temp_asset = None
        replaced.append(target)
        os.replace(temp_manifest, manifest_path)
        temp_manifest = None
        replaced.append(manifest_path)
    except Exception as error:
        for path in reversed(replaced):
            previous = originals[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        raise VisualAssetRegistrationError(f"visual asset registration failed: {error}") from error
    finally:
        if temp_asset is not None:
            temp_asset.unlink(missing_ok=True)
        if temp_manifest is not None:
            temp_manifest.unlink(missing_ok=True)


def register_visual_asset(
    project: Path,
    *,
    task_id: str,
    source: Path,
    prompt_sha256: str,
    tool_call_id: str,
    style_reference_ids: list[str],
    identity_reference_task_ids: list[str],
) -> VisualAssetResult:
    root = project.expanduser().resolve()
    if not root.is_dir():
        raise VisualAssetRegistrationError(f"project directory is missing: {root}")
    stage_manifest = _verify_visual_stage(root)
    tasks = _load_tasks(root)
    task = tasks.get(task_id)
    if task is None:
        raise VisualAssetRegistrationError(f"unknown task: {task_id}")
    if not isinstance(prompt_sha256, str) or _SHA256.fullmatch(prompt_sha256) is None:
        raise VisualAssetRegistrationError("prompt_sha256 is invalid")
    if prompt_sha256 != task.get("prompt_sha256"):
        raise VisualAssetRegistrationError("prompt_sha256 does not match the visual task")
    call_id = _validate_call_id(tool_call_id)
    declared_style_refs = _validate_declared_ids(style_reference_ids, "style_reference_ids")
    expected_style_refs = list(task.get("style_reference_ids", []))
    if declared_style_refs != expected_style_refs:
        raise VisualAssetRegistrationError("style reference declaration does not match the visual task")
    declared_identity_refs = _validate_declared_ids(
        identity_reference_task_ids, "identity_reference_task_ids"
    )
    expected_identity_refs = list(task.get("identity_dependency_task_ids", []))
    if declared_identity_refs != expected_identity_refs:
        raise VisualAssetRegistrationError("identity reference declaration does not match the visual task")

    manifest = _load_manifest(
        root,
        str(stage_manifest.get("visual_stage_digest", "")),
        str(stage_manifest.get("release_id", "")),
    )
    _verify_manifest_records(
        root, stage_manifest=stage_manifest, tasks=tasks, manifest=manifest,
        require_complete=False, recompute_diagnostics=False, deep_image_validation=False
    )
    assets = manifest["assets"]
    if any(item.get("task_id") == task_id for item in assets):
        raise VisualAssetRegistrationError(f"task is already registered: {task_id}")
    registered_ids = {str(item.get("task_id")) for item in assets}
    missing_dependencies = [
        dependency for dependency in task.get("depends_on_task_ids", []) if dependency not in registered_ids
    ]
    if missing_dependencies:
        raise VisualAssetRegistrationError(
            f"task dependency must be registered first: {', '.join(missing_dependencies)}"
        )

    catalog = load_reference_catalog()
    existing_by_task = {str(item["task_id"]): item for item in assets}
    style_evidence = _style_reference_evidence(catalog, declared_style_refs)
    identity_evidence = _identity_reference_evidence(declared_identity_refs, existing_by_task)
    digest, image_info = _validate_source(
        source, {entry.sha256 for entry in catalog.entries}, task.get("canvas")
    )
    if any(item.get("sha256") == digest for item in assets):
        raise VisualAssetRegistrationError("duplicate image content cannot be registered for another task")

    profile = _load_json(root / "03_images_生成图片/BOOK_VISUAL_PROFILE.json", "book visual profile")
    envelope = _palette_envelope(profile, str(task.get("palette_id", "")))
    diagnostic = _machine_diagnostic(source.expanduser().resolve(), envelope)

    output_target = task.get("output_target")
    if not isinstance(output_target, str):
        raise VisualAssetRegistrationError("visual task output_target is invalid")
    try:
        target = safe_project_output(root, Path(output_target))
    except ValueError as error:
        raise VisualAssetRegistrationError(str(error)) from error
    if target.exists():
        raise VisualAssetRegistrationError(f"visual task output already exists: {output_target}")

    asset = {
        "task_id": task_id,
        "task_kind": task["task_kind"],
        "category": task["category"],
        "path": target.relative_to(root).as_posix(),
        "sha256": digest,
        "bytes": source.expanduser().resolve().stat().st_size,
        "prompt_sha256": prompt_sha256,
        "provider": "host-imagegen",
        "tool_call_id": call_id,
        "width": image_info["width"],
        "height": image_info["height"],
        "mode": image_info["mode"],
        "palette_id": task["palette_id"],
        "lighting_id": task["lighting_id"],
        "style_reference_evidence": style_evidence,
        "identity_reference_evidence": identity_evidence,
        "machine_diagnostic": diagnostic,
        "semantic_review_status": "pending",
        "reality_review_status": "pending",
        "human_review_status": "pending",
        "registered_at": utc_now(),
    }
    next_manifest = dict(manifest)
    next_manifest["assets"] = [*assets, asset]
    next_manifest["registered_asset_count"] = len(next_manifest["assets"])
    next_manifest["last_registered_at"] = asset["registered_at"]
    manifest_path = root / _MANIFEST_RELATIVE
    _transactional_publish(
        source=source.expanduser().resolve(),
        target=target,
        manifest_path=manifest_path,
        manifest_bytes=_pretty_bytes(next_manifest),
    )
    return VisualAssetResult("created", asset, manifest_path)
