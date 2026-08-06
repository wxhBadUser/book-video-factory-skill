from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.hbg_bridge.compiler import (
    HbgBridgeCompileError,
    HbgBridgeConflict,
    compile_hbg_bridge,
)
from book_video_factory.manifests import sha256_file, write_stage_manifest
from book_video_factory.orientation import OrientationError, validate_orientation_contract
from book_video_factory.reference_visuals.catalog import (
    ReferenceCatalogError,
    default_catalog_path,
    load_reference_catalog,
)
from book_video_factory.style_profiles import project_workflow

from .contracts import VisualStageContractError, validate_visual_stage_input
from .prompts import compile_visual_task_prompts


class VisualStageCompileError(RuntimeError):
    pass


class VisualStageConflict(VisualStageCompileError):
    pass


@dataclass(frozen=True)
class VisualStageResult:
    status: str
    visual_stage_digest: str
    manifest_path: Path
    stage_manifest_path: Path


_PROMPTS_PLACEHOLDER = "# ImageGen 提示词与调用记录\n\n> 每项任务必须绑定 beat、shot、锚点和输出 Hash。\n"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise VisualStageCompileError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualStageCompileError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise VisualStageCompileError(f"{label} must be a JSON object")
    return value


def _jsonl_bytes(items: list[dict[str, Any]]) -> bytes:
    return ("\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in items
    ) + "\n").encode("utf-8")


def _prompts_markdown(tasks: list[dict[str, Any]]) -> bytes:
    lines = [
        "# ImageGen 提示词与调用记录",
        "",
        "> 本文件由 Phase 3 视觉编译器确定性生成。图片尚未生成时不得填写工具调用 ID 或输出 Hash。",
        "",
    ]
    for task in tasks:
        lines.extend([
            f"## {task['task_id']}｜{task['task_kind']}｜{task['category']}",
            "",
            f"- 生成通道：`{task['generation_lane']}`",
            f"- 输出目标：`{task['output_target']}`",
            f"- Prompt SHA-256：`{task['prompt_sha256']}`",
            f"- 样式参考：{', '.join(task['style_reference_ids'])}",
            f"- 身份依赖任务：{', '.join(task['identity_dependency_task_ids']) or '无'}",
            f"- 其他依赖任务：{', '.join(task['depends_on_task_ids']) or '无'}",
            "",
            "```text",
            task["prompt"],
            "```",
            "",
        ])
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _reference_manifest(catalog, selected: list[str]) -> dict[str, Any]:
    selected_entries = []
    by_id = catalog.by_id()
    for reference_id in selected:
        item = by_id[reference_id]
        selected_entries.append({
            "reference_id": item.reference_id,
            "work_title": item.work_title,
            "path": item.path,
            "profile_path": item.profile_path,
            "profile_sha256": item.profile_sha256,
            "sha256": item.sha256,
            "width": item.width,
            "height": item.height,
            "shot_count": item.shot_count,
            "visual_language": item.visual_language,
            "roles": {
                "style_only": True,
                "identity_reference_allowed": False,
                "production_asset_allowed": False,
                "exact_composition_copy_allowed": False,
            },
        })
    kernel_path = catalog.kernel_path
    return {
        "schema_version": "visual-reference-manifest.v1",
        "catalog_id": catalog.catalog_id,
        "catalog_path": catalog.path.relative_to(catalog.path.parents[3]).as_posix(),
        "catalog_sha256": sha256_file(catalog.path),
        "kernel_id": catalog.kernel_id,
        "kernel_path": kernel_path.relative_to(catalog.path.parents[3]).as_posix(),
        "kernel_sha256": catalog.kernel_sha256,
        "selected_references": selected_entries,
        "usage_policy": {
            "style_only": True,
            "identity_reference_allowed": False,
            "production_asset_allowed": False,
            "exact_composition_copy_allowed": False,
        },
    }


def _required_recorded_at(stage_manifest: Mapping[str, Any]) -> str:
    recorded_at = stage_manifest.get("recorded_at")
    if not isinstance(recorded_at, str) or not recorded_at or recorded_at != recorded_at.strip():
        raise VisualStageCompileError("Phase 2 stage manifest recorded_at is missing or invalid")
    return recorded_at


def _profile(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "book-visual-profile.v1",
        "release_id": normalized["release_id"],
        "bound_hbg_bridge_digest": normalized["hbg_bridge_digest"],
        "bound_release_text_sha256": normalized["release_text_sha256"],
        "global_kernel_id": normalized["global_kernel_id"],
        "style_reference_ids": normalized["style_reference_ids"],
        "book_look": normalized["book_look"],
        "palette_profiles": normalized["palette_profiles"],
        "lighting_profiles": normalized["lighting_profiles"],
        "material_profiles": normalized["material_profiles"],
        "composition_rules": normalized["composition_rules"],
        "repeated_motifs": normalized["repeated_motifs"],
        "forbidden_traits": normalized["forbidden_traits"],
        "character_anchors": normalized["character_anchors"],
        "scene_anchors": normalized["scene_anchors"],
        "object_anchors": normalized["object_anchors"],
        "machine_diagnostics_are_aesthetic_approval": False,
        "human_review_status": "pending",
    }


def _snapshot(root: Path, relatives: list[str]) -> dict[str, bytes | None]:
    return {
        relative: (root / relative).read_bytes() if (root / relative).is_file() else None
        for relative in relatives
    }


def _restore(root: Path, originals: Mapping[str, bytes | None]) -> None:
    for relative, content in originals.items():
        target = root / relative
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.restore-{os.getpid()}.tmp")
        temp.write_bytes(content)
        os.replace(temp, target)


def _publish(root: Path, payloads: Mapping[str, bytes]) -> None:
    originals = _snapshot(root, list(payloads))
    temps: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for relative, content in payloads.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.phase3-{os.getpid()}.tmp")
            temp.write_bytes(content)
            temps[relative] = temp
        for relative, temp in temps.items():
            os.replace(temp, root / relative)
            replaced.append(relative)
    except Exception:
        _restore(root, {relative: originals[relative] for relative in replaced})
        raise
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)


def _verify_existing(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    digest: str,
    expected_output_hashes: Mapping[str, str],
    expected_fields: Mapping[str, Any],
) -> VisualStageResult:
    recorded_hashes = manifest.get("output_hashes")
    if not isinstance(recorded_hashes, Mapping):
        raise VisualStageConflict("existing visual stage manifest has invalid output hashes")
    for relative, recorded_hash in recorded_hashes.items():
        if not isinstance(relative, str) or not isinstance(recorded_hash, str):
            raise VisualStageConflict("existing visual stage manifest has invalid output hash entry")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise VisualStageConflict("visual stage output escapes project") from error
        if not target.is_file() or sha256_file(target) != recorded_hash:
            raise VisualStageConflict(f"existing visual stage output hash mismatch: {relative}")
    if manifest.get("visual_stage_digest") != digest:
        raise VisualStageConflict("a different visual stage package already exists")
    for key, expected in expected_fields.items():
        if manifest.get(key) != expected:
            raise VisualStageConflict(f"existing visual stage manifest field mismatch: {key}")
    if dict(recorded_hashes) != dict(expected_output_hashes):
        raise VisualStageConflict("existing visual stage manifest output hashes do not match deterministic outputs")
    for relative, expected_hash in expected_output_hashes.items():
        target = (root / relative).resolve()
        if not target.is_file() or sha256_file(target) != expected_hash:
            raise VisualStageConflict(f"existing visual stage output hash mismatch: {relative}")
    stage_relative = manifest.get("stage_manifest_path")
    if not isinstance(stage_relative, str):
        raise VisualStageConflict("visual stage manifest has invalid stage_manifest_path")
    stage_path = (root / stage_relative).resolve()
    try:
        stage_path.relative_to(root)
    except ValueError as error:
        raise VisualStageConflict("visual stage stage manifest escapes project") from error
    if not stage_path.is_file() or sha256_file(stage_path) != manifest.get("stage_manifest_sha256"):
        raise VisualStageConflict("visual stage stage manifest hash mismatch")
    return VisualStageResult("unchanged", digest, root / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json", stage_path)


def compile_visual_stage(project: Path, visual_input_path: Path) -> VisualStageResult:
    root = project.expanduser().resolve()
    staging: Path | None = None
    try:
        stored_bridge_input = root / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json"
        bridge_result = compile_hbg_bridge(root, stored_bridge_input)
        bridge_manifest = _load_object(bridge_result.manifest_path, "HBG bridge manifest")
        bridge_input = _load_object(stored_bridge_input, "stored HBG bridge input")
        raw = _load_object(visual_input_path.expanduser().resolve(), "visual stage input")
        if raw.get("hbg_bridge_digest") != bridge_manifest.get("bridge_digest"):
            raise VisualStageCompileError("hbg_bridge_digest does not match current Phase 2 bridge")
        if raw.get("release_text_sha256") != bridge_manifest.get("release_text_sha256"):
            raise VisualStageCompileError("release_text_sha256 does not match current Phase 2 bridge")
        if raw.get("release_id") != bridge_manifest.get("release_id"):
            raise VisualStageCompileError("release_id does not match current Phase 2 bridge")
        characters = bridge_input.get("characters")
        if not isinstance(characters, list):
            raise VisualStageCompileError("stored HBG bridge input has no characters")
        catalog = load_reference_catalog()
        normalized = validate_visual_stage_input(raw, phase2_characters=characters, catalog=catalog)
        workflow = project_workflow(root)
        if normalized["orientation"] != workflow["orientation"]:
            raise VisualStageCompileError(
                "visual input orientation contradicts the project workflow orientation"
            )
        hbg_style = _load_object(root / "HBG_STYLE.json", "HBG style")
        try:
            validate_orientation_contract(
                normalized["orientation"],
                {
                    **(hbg_style.get("canvas") if isinstance(hbg_style.get("canvas"), dict) else {}),
                    "orientation": hbg_style.get("orientation"),
                },
            )
        except OrientationError as error:
            raise VisualStageCompileError(f"HBG style orientation is invalid: {error}") from error
        tasks = compile_visual_task_prompts(normalized, catalog)
    except (VisualStageCompileError, VisualStageConflict):
        raise
    except (HbgBridgeCompileError, HbgBridgeConflict, ReferenceCatalogError, VisualStageContractError, OSError, ValueError, KeyError, TypeError) as error:
        raise VisualStageCompileError(str(error)) from error

    reference_manifest = _reference_manifest(catalog, normalized["style_reference_ids"])
    profile = _profile(normalized)
    anchors = [task for task in tasks if task["task_kind"] != "lookdev"]
    lookdev = [task for task in tasks if task["task_kind"] == "lookdev"]
    payloads: dict[str, bytes] = {
        "03_images_生成图片/BOOK_VISUAL_PROFILE.json": _pretty_bytes(profile),
        "03_images_生成图片/VISUAL_REFERENCE_MANIFEST.json": _pretty_bytes(reference_manifest),
        "03_images_生成图片/ANCHOR_TASKS.jsonl": _jsonl_bytes(anchors),
        "03_images_生成图片/LOOKDEV_TASKS.jsonl": _jsonl_bytes(lookdev),
        "03_images_生成图片/VISUAL_STAGE_INPUT.json": _pretty_bytes(normalized),
        "PROMPTS.md": _prompts_markdown(tasks),
    }
    digest_payload = {
        "visual_input": normalized,
        "hbg_bridge_digest": bridge_manifest["bridge_digest"],
        "release_text_sha256": bridge_manifest["release_text_sha256"],
        "catalog_sha256": reference_manifest["catalog_sha256"],
        "kernel_sha256": reference_manifest["kernel_sha256"],
        "tasks": tasks,
    }
    visual_digest = _sha_bytes(_canonical_bytes(digest_payload))
    output_hashes = {relative: _sha_bytes(content) for relative, content in payloads.items()}
    manifest_path = root / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json"
    manifest_id = f"visual-stage-{visual_digest[:16]}"
    bridge_approval_id = bridge_manifest.get("approval_event_id")
    recorded_at = _required_recorded_at(
        _load_object(root / bridge_manifest["stage_manifest_path"], "HBG bridge stage manifest")
    )
    stage_relative = f"manifests/stages/visual_stage/{recorded_at.replace(':', '-').replace('+', '_')}-{manifest_id}.json"
    expected_fields = {
        "release_id": normalized["release_id"],
        "hbg_bridge_digest": bridge_manifest["bridge_digest"],
        "release_text_sha256": bridge_manifest["release_text_sha256"],
        "input_sha256": _sha_bytes(_pretty_bytes(normalized)),
        "reference_catalog_sha256": reference_manifest["catalog_sha256"],
        "kernel_sha256": reference_manifest["kernel_sha256"],
        "stage_manifest_path": stage_relative,
        "next_stage_status": "waiting_for_host_imagegen",
    }
    if manifest_path.is_file():
        return _verify_existing(
            root,
            _load_object(manifest_path, "visual stage manifest"),
            digest=visual_digest,
            expected_output_hashes=output_hashes,
            expected_fields=expected_fields,
        )

    for relative in payloads:
        target = root / relative
        if target.is_file():
            if relative == "PROMPTS.md" and target.read_text(encoding="utf-8") == _PROMPTS_PLACEHOLDER:
                continue
            raise VisualStageConflict(f"refusing to overwrite user-modified or unknown output: {relative}")
        if target.exists():
            raise VisualStageConflict(f"visual stage output is not a regular file: {relative}")

    transaction_relatives = [*payloads, stage_relative, "03_images_生成图片/VISUAL_STAGE_MANIFEST.json"]
    originals = _snapshot(root, transaction_relatives)
    try:
        _publish(root, payloads)
        stage_path = write_stage_manifest(
            root,
            stage="visual_stage",
            release_id=normalized["release_id"],
            release_profile_id=str(workflow["release_profile_id"]),
            inputs=[
                ("hbg_bridge_manifest", bridge_result.manifest_path),
                ("hbg_bridge_stage_manifest", bridge_result.stage_manifest_path),
                ("visual_stage_input", root / "03_images_生成图片/VISUAL_STAGE_INPUT.json"),
            ],
            outputs=[(Path(relative).name, root / relative) for relative in payloads],
            checks=[
                {"id": "phase2_bridge_current", "result": "pass", "severity": "error"},
                {"id": "reference_catalog_integrity", "result": "pass", "severity": "error"},
                {"id": "visual_contract", "result": "pass", "severity": "error"},
                {"id": "lookdev_count_12", "result": "pass", "severity": "error"},
            ],
            producer="book-video-factory-visual-stage",
            approval_event_ids=[str(bridge_approval_id)] if bridge_approval_id else [],
            manifest_id=manifest_id,
            recorded_at=recorded_at,
        )
        manifest = {
            "schema_version": "visual-stage-manifest.v1",
            "release_id": normalized["release_id"],
            "visual_stage_digest": visual_digest,
            "hbg_bridge_digest": bridge_manifest["bridge_digest"],
            "release_text_sha256": bridge_manifest["release_text_sha256"],
            "input_sha256": _sha_bytes(_pretty_bytes(normalized)),
            "reference_catalog_sha256": reference_manifest["catalog_sha256"],
            "kernel_sha256": reference_manifest["kernel_sha256"],
            "task_counts": {"anchors": len(anchors), "lookdev": len(lookdev), "total": len(tasks)},
            "output_hashes": {relative: sha256_file(root / relative) for relative in payloads},
            "stage_manifest_path": stage_path.relative_to(root).as_posix(),
            "stage_manifest_sha256": sha256_file(stage_path),
            "next_stage_status": "waiting_for_host_imagegen",
        }
        temp_manifest = manifest_path.with_name(f".{manifest_path.name}.tmp")
        temp_manifest.write_bytes(_pretty_bytes(manifest))
        os.replace(temp_manifest, manifest_path)
        return VisualStageResult("created", visual_digest, manifest_path, stage_path)
    except Exception:
        _restore(root, originals)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
