from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.manifests import sha256_file, write_stage_manifest
from book_video_factory.project import build_initial_project_spec
from book_video_factory.style_profiles import project_workflow

from .character_export import export_characters_markdown
from .contracts import HbgBridgeContractError, validate_bridge_input
from .project_spec import build_project_spec
from .provenance import HbgBridgeProvenanceError, verify_phase1_handoff
from .runner import HbgRunnerError, initialize_style, validate_storyboard
from .script_export import build_script_exports
from .storyboard_export import export_storyboard_base


class HbgBridgeCompileError(RuntimeError):
    """The Phase 2 bridge could not be compiled."""


class HbgBridgeConflict(HbgBridgeCompileError):
    """A different or user-modified bridge artifact already exists."""


@dataclass(frozen=True)
class BridgeResult:
    status: str
    bridge_digest: str
    manifest_path: Path
    stage_manifest_path: Path


_SOURCE_PLACEHOLDER = "# 冻结口播原文\n\n> 脚本批准后由适配器写入；不得在生产阶段静默改写。\n"
_SCRIPT_PLACEHOLDER = "# HBG 旁白执行稿\n\n> 由冻结的 SCRIPT_RELEASE.md 编译生成。\n"
_CHARACTERS_PLACEHOLDER = "# 人物身份与连续性\n\n> 视觉锚点批准后由 BOOK_VISUAL_PROFILE.json 导出。\n"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical_pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HbgBridgeCompileError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HbgBridgeCompileError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise HbgBridgeCompileError(f"{label} must be a JSON object")
    return value


_ALLOWED_STORYBOARD_MOTIONS = {"hold"}


def _require_hold_only_storyboard(project: Path) -> None:
    """Reject any camera motion injected into the HBG-normalized storyboard."""
    path = project / "STORYBOARD_BASE.json"
    try:
        beats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HbgBridgeCompileError(
            f"STORYBOARD_BASE.json is unreadable after HBG validation: {error}"
        ) from error
    if not isinstance(beats, list):
        raise HbgBridgeCompileError(
            "STORYBOARD_BASE.json must be an array after HBG validation"
        )
    for index, beat in enumerate(beats, start=1):
        motion = beat.get("motion") if isinstance(beat, Mapping) else None
        if motion not in _ALLOWED_STORYBOARD_MOTIONS:
            raise HbgBridgeCompileError(
                f"storyboard beat {index} motion must be 'hold' "
                f"(scene images are static stills); got {motion!r}"
            )


def _initial_project_spec(path: Path, project_contract: Mapping[str, Any]) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    book = project_contract.get("book")
    if not isinstance(book, Mapping):
        return False
    title = book.get("title")
    author = book.get("author")
    if not isinstance(title, str) or not isinstance(author, str):
        return False
    workflow = project_contract.get("workflow")
    provider_policy = (
        workflow.get("narration_provider_policy")
        if isinstance(workflow, Mapping)
        else None
    )
    provider = "minimax" if provider_policy == "minimax_required" else "edge-tts"
    return value == build_initial_project_spec(title, author, narration_provider=provider)


def _is_recognized_placeholder(
    relative: str, path: Path, project_contract: Mapping[str, Any]
) -> bool:
    if relative == "SCRIPT_SOURCE.md":
        return path.read_text(encoding="utf-8") == _SOURCE_PLACEHOLDER
    if relative == "SCRIPT.md":
        return path.read_text(encoding="utf-8") == _SCRIPT_PLACEHOLDER
    if relative == "CHARACTERS.md":
        return path.read_text(encoding="utf-8") == _CHARACTERS_PLACEHOLDER
    if relative == "STORYBOARD_BASE.json":
        try:
            return json.loads(path.read_text(encoding="utf-8")) == []
        except (OSError, json.JSONDecodeError):
            return False
    if relative == "PROJECT_SPEC.json":
        return _initial_project_spec(path, project_contract)
    return False


def _verify_existing(
    root: Path,
    manifest: Mapping[str, Any],
    digest: str,
    *,
    expected_output_hashes: Mapping[str, str],
    expected_fields: Mapping[str, Any],
) -> BridgeResult:
    if manifest.get("bridge_digest") != digest:
        raise HbgBridgeConflict("a different bridge package already exists")
    for key, expected in expected_fields.items():
        if manifest.get(key) != expected:
            raise HbgBridgeConflict(f"existing bridge manifest field mismatch: {key}")
    hashes = manifest.get("output_hashes")
    if not isinstance(hashes, Mapping) or dict(hashes) != dict(expected_output_hashes):
        raise HbgBridgeConflict("existing bridge manifest output hashes do not match deterministic bridge outputs")
    for relative, expected in expected_output_hashes.items():
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise HbgBridgeConflict("existing bridge output escapes project") from error
        if not path.is_file() or sha256_file(path) != expected:
            raise HbgBridgeConflict(f"existing bridge output hash mismatch: {relative}")
    stage_relative = manifest.get("stage_manifest_path")
    if not isinstance(stage_relative, str):
        raise HbgBridgeConflict("existing bridge stage manifest path is invalid")
    stage = (root / stage_relative).resolve()
    try:
        stage.relative_to(root)
    except ValueError as error:
        raise HbgBridgeConflict("existing bridge stage manifest escapes project") from error
    if not stage.is_file() or sha256_file(stage) != manifest.get("stage_manifest_sha256"):
        raise HbgBridgeConflict("existing bridge stage manifest hash mismatch")
    try:
        stage_payload = json.loads(stage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HbgBridgeConflict("existing bridge stage manifest is unreadable") from error
    if not isinstance(stage_payload, Mapping):
        raise HbgBridgeConflict("existing bridge stage manifest must be an object")
    if stage_payload.get("stage") != "hbg_bridge" or stage_payload.get("manifest_id") != f"hbg-bridge-{digest[:16]}":
        raise HbgBridgeConflict("existing bridge stage manifest identity mismatch")
    stage_outputs = stage_payload.get("outputs")
    if not isinstance(stage_outputs, list):
        raise HbgBridgeConflict("existing bridge stage manifest has no outputs")
    stage_hashes = {
        item.get("path"): item.get("sha256")
        for item in stage_outputs
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if stage_hashes != dict(expected_output_hashes):
        raise HbgBridgeConflict("existing stage manifest does not bind deterministic bridge outputs")
    return BridgeResult(
        status="unchanged",
        bridge_digest=digest,
        manifest_path=root / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json",
        stage_manifest_path=stage,
    )

def _snapshot_files(root: Path, relatives: list[str]) -> dict[str, bytes | None]:
    return {
        relative: (root / relative).read_bytes() if (root / relative).is_file() else None
        for relative in relatives
    }


def _restore_snapshot(root: Path, originals: Mapping[str, bytes | None]) -> None:
    for relative, original in originals.items():
        target = root / relative
        if original is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        restore = target.with_name(f".{target.name}.restore-{os.getpid()}.tmp")
        restore.write_bytes(original)
        os.replace(restore, target)


def _stage_manifest_relative(recorded_at: str, manifest_id: str) -> str:
    safe_time = recorded_at.replace(":", "-").replace("+", "_")
    return f"manifests/stages/hbg_bridge/{safe_time}-{manifest_id}.json"


def _publish_with_rollback(root: Path, payloads: Mapping[str, bytes]) -> None:
    originals: dict[str, bytes | None] = {}
    temp_paths: dict[str, Path] = {}
    for relative, content in payloads.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = target.read_bytes() if target.is_file() else None
        temp = target.with_name(f".{target.name}.phase2-{os.getpid()}.tmp")
        temp.write_bytes(content)
        temp_paths[relative] = temp
    replaced: list[str] = []
    try:
        for relative, temp in temp_paths.items():
            os.replace(temp, root / relative)
            replaced.append(relative)
    except Exception:
        for relative in reversed(replaced):
            target = root / relative
            original = originals[relative]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                restore = target.with_name(f".{target.name}.restore-{os.getpid()}.tmp")
                restore.write_bytes(original)
                os.replace(restore, target)
        raise
    finally:
        for temp in temp_paths.values():
            temp.unlink(missing_ok=True)


def compile_hbg_bridge(project: Path, bridge_input_path: Path) -> BridgeResult:
    root = project.expanduser().resolve()
    input_path = bridge_input_path.expanduser().resolve()
    try:
        raw_input = _load_object(input_path, "HBG bridge input")
        release_id = raw_input.get("release_id")
        if not isinstance(release_id, str) or not release_id.strip():
            raise HbgBridgeCompileError("bridge input release_id is required")
        handoff = verify_phase1_handoff(root, release_id)
        script = handoff.script_package.get("script")
        if not isinstance(script, Mapping):
            raise HbgBridgeCompileError("verified handoff has no script")
        normalized = validate_bridge_input(
            raw_input,
            script=script,
            package_digest=handoff.package_digest,
            release_text_sha256=handoff.release_text_sha256,
        )
        bridge_digest = _sha_bytes(_canonical_bytes({
            "package_digest": handoff.package_digest,
            "release_text_sha256": handoff.release_text_sha256,
            "bridge_input": normalized,
            "hbg_commit": handoff.hbg_commit,
        }))
        manifest_path = root / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json"
        project_contract = _load_object(root / "project.json", "project contract")
        workflow_contract = project_workflow(root)
        if normalized["orientation"] != workflow_contract["orientation"]:
            raise HbgBridgeCompileError(
                "bridge orientation contradicts the project workflow orientation"
            )
        exports = build_script_exports(project_contract["book"]["title"], script, normalized["chapters"])
        project_spec = build_project_spec(project_contract, handoff, normalized, exports)
        characters = export_characters_markdown(normalized["characters"])
        storyboard = export_storyboard_base(normalized, exports)
    except (HbgBridgeCompileError, HbgBridgeConflict):
        raise
    except (HbgBridgeProvenanceError, HbgBridgeContractError, ValueError, KeyError, TypeError) as error:
        raise HbgBridgeCompileError(str(error)) from error

    staging = Path(tempfile.mkdtemp(prefix=".bridge-staging-", dir=root))
    try:
        (staging / "PROJECT_SPEC.json").write_bytes(_pretty_bytes(project_spec))
        (staging / "SCRIPT_SOURCE.md").write_bytes(exports.source_bytes)
        (staging / "SCRIPT.md").write_text(exports.script_markdown, encoding="utf-8")
        (staging / "CHARACTERS.md").write_text(characters, encoding="utf-8")
        (staging / "STORYBOARD_BASE.json").write_bytes(_pretty_bytes(storyboard))
        initialize_style(staging, normalized["orientation"])
        validate_storyboard(staging)
        _require_hold_only_storyboard(staging)

        payloads: dict[str, bytes] = {
            "SCRIPT_SOURCE.md": (staging / "SCRIPT_SOURCE.md").read_bytes(),
            "SCRIPT.md": (staging / "SCRIPT.md").read_bytes(),
            "CHARACTERS.md": (staging / "CHARACTERS.md").read_bytes(),
            "PROJECT_SPEC.json": (staging / "PROJECT_SPEC.json").read_bytes(),
            "STORYBOARD_BASE.json": (staging / "STORYBOARD_BASE.json").read_bytes(),
            "HBG_STYLE.json": (staging / "HBG_STYLE.json").read_bytes(),
            "02_story_script_故事脚本/script.narrator-essay.v1.json": _pretty_bytes(exports.script_json),
            "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json": _canonical_pretty_bytes(normalized),
        }
        expected_output_hashes = {relative: _sha_bytes(content) for relative, content in payloads.items()}
        manifest_id = f"hbg-bridge-{bridge_digest[:16]}"
        recorded_at = str(handoff.approval_event.get("reviewed_at"))
        stage_relative = _stage_manifest_relative(recorded_at, manifest_id)
        expected_manifest_fields = {
            "release_id": release_id,
            "content_package_digest": handoff.package_digest,
            "release_text_sha256": handoff.release_text_sha256,
            "hbg_upstream_commit": handoff.hbg_commit,
            "approval_event_id": handoff.approval_event.get("event_id"),
            "input_sha256": _sha_bytes(_canonical_pretty_bytes(normalized)),
            "stage_manifest_path": stage_relative,
            "next_stage_status": "ready_for_visual_anchor_generation",
        }
        if manifest_path.is_file():
            return _verify_existing(
                root,
                _load_object(manifest_path, "HBG bridge manifest"),
                bridge_digest,
                expected_output_hashes=expected_output_hashes,
                expected_fields=expected_manifest_fields,
            )

        for relative in payloads:
            target = root / relative
            canonical_input_source = (
                relative == "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json"
                and target.resolve() == input_path
                and raw_input == normalized
            )
            if (
                target.is_file()
                and not canonical_input_source
                and not _is_recognized_placeholder(relative, target, project_contract)
            ):
                raise HbgBridgeConflict(f"refusing to overwrite user-modified or unknown output: {relative}")
            if target.exists() and not target.is_file():
                raise HbgBridgeConflict(f"bridge output is not a regular file: {relative}")

        release_profile = str(workflow_contract["release_profile_id"])
        transaction_relatives = [
            *payloads,
            stage_relative,
            "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json",
        ]
        originals = _snapshot_files(root, transaction_relatives)
        _publish_with_rollback(root, payloads)
        stage_path: Path | None = None
        temp_manifest: Path | None = None
        try:
            stage_path = write_stage_manifest(
                root,
                stage="hbg_bridge",
                release_id=release_id,
                release_profile_id=release_profile,
                inputs=[
                    ("content_package_manifest", root / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"),
                    ("script_lock", root / "02_story_script_故事脚本/SCRIPT_LOCK.json"),
                    ("hbg_bridge_input", root / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json"),
                ],
                outputs=[(Path(relative).name, root / relative) for relative in payloads],
                checks=[
                    {"id": "phase1_handoff", "result": "pass", "severity": "error"},
                    {"id": "hbg_style_init", "result": "pass", "severity": "error"},
                    {"id": "hbg_storyboard_validation", "result": "pass", "severity": "error"},
                ],
                producer="book-video-factory-hbg-bridge",
                approval_event_ids=[str(handoff.approval_event.get("event_id"))],
                manifest_id=manifest_id,
                recorded_at=recorded_at,
            )
            bridge_manifest = {
                "schema_version": "hbg-bridge-manifest.v1",
                "release_id": release_id,
                "bridge_digest": bridge_digest,
                "content_package_digest": handoff.package_digest,
                "release_text_sha256": handoff.release_text_sha256,
                "hbg_upstream_commit": handoff.hbg_commit,
                "approval_event_id": handoff.approval_event.get("event_id"),
                "input_sha256": _sha_bytes(_canonical_pretty_bytes(normalized)),
                "output_hashes": {relative: sha256_file(root / relative) for relative in payloads},
                "stage_manifest_path": stage_path.relative_to(root).as_posix(),
                "stage_manifest_sha256": sha256_file(stage_path),
                "next_stage_status": "ready_for_visual_anchor_generation",
            }
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            temp_manifest = manifest_path.with_name(f".{manifest_path.name}.tmp")
            temp_manifest.write_bytes(_pretty_bytes(bridge_manifest))
            os.replace(temp_manifest, manifest_path)
        except Exception:
            _restore_snapshot(root, originals)
            raise
        finally:
            if temp_manifest is not None:
                temp_manifest.unlink(missing_ok=True)
        return BridgeResult(
            status="created",
            bridge_digest=bridge_digest,
            manifest_path=manifest_path,
            stage_manifest_path=stage_path,
        )
    except HbgBridgeConflict:
        raise
    except (HbgRunnerError, OSError, ValueError, KeyError, TypeError) as error:
        raise HbgBridgeCompileError(str(error)) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
