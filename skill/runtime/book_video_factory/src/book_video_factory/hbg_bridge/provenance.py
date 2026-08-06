from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.content_package import render_script_markdown
from book_video_factory.gates import approval_covers_path, current_approvals
from book_video_factory.manifests import sha256_file


class HbgBridgeProvenanceError(RuntimeError):
    """The approved Phase 1 handoff cannot be trusted."""


@dataclass(frozen=True)
class VerifiedHandoff:
    project: Path
    release_id: str
    package_digest: str
    release_text: str
    release_text_sha256: str
    script_package: dict[str, Any]
    content_manifest: dict[str, Any]
    approval_event: dict[str, Any]
    hbg_commit: str


_REQUIRED_APPROVAL_PATHS = (
    "02_story_script_故事脚本/SCRIPT_RELEASE.md",
    "02_story_script_故事脚本/SCRIPT_AUDIT.md",
    "02_story_script_故事脚本/SCRIPT_METRICS.json",
    "02_story_script_故事脚本/SCRIPT_LOCK.json",
    "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HbgBridgeProvenanceError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HbgBridgeProvenanceError(f"{label} is unreadable: {path}: {error}") from error
    if not isinstance(value, dict):
        raise HbgBridgeProvenanceError(f"{label} must be a JSON object: {path}")
    return value


def _safe_project_path(project: Path, relative_value: Any, label: str) -> Path:
    if not isinstance(relative_value, str) or not relative_value.strip():
        raise HbgBridgeProvenanceError(f"{label} must be a project-relative path")
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise HbgBridgeProvenanceError(f"{label} escapes the project")
    path = (project / relative).resolve()
    try:
        path.relative_to(project)
    except ValueError as error:
        raise HbgBridgeProvenanceError(f"{label} escapes the project") from error
    return path


def _default_repository_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json").is_file():
            return parent
    raise HbgBridgeProvenanceError("repository root containing the HBG vendor cannot be found")


def _verify_vendor(repository_root: Path) -> str:
    vendor = repository_root.resolve() / "vendor/hbg-life-simulation"
    lock = _load_object(vendor / "UPSTREAM_LOCK.json", "HBG vendor lock")
    if lock.get("repository") != "Mr-funny/hbg-life-simulation":
        raise HbgBridgeProvenanceError("HBG vendor lock names an unexpected repository")
    commit = lock.get("commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise HbgBridgeProvenanceError("HBG vendor lock has an invalid commit")
    files = lock.get("files")
    if not isinstance(files, Mapping) or not files:
        raise HbgBridgeProvenanceError("HBG vendor lock has no file hashes")
    for relative_value, expected in files.items():
        if not isinstance(relative_value, str) or not isinstance(expected, str):
            raise HbgBridgeProvenanceError("HBG vendor lock contains an invalid file record")
        path = _safe_project_path(vendor, relative_value, "HBG vendor file")
        if not path.is_file():
            raise HbgBridgeProvenanceError(f"HBG vendor locked file is missing: {relative_value}")
        if sha256_file(path) != expected:
            raise HbgBridgeProvenanceError(f"HBG vendor locked file hash mismatch: {relative_value}")
    actual = {
        path.relative_to(vendor).as_posix()
        for path in vendor.rglob("*")
        if path.is_file() and path.name != "UPSTREAM_LOCK.json"
    }
    if actual != set(files):
        raise HbgBridgeProvenanceError("HBG vendor file set differs from the upstream lock")
    return commit


def verify_phase1_handoff(
    project: Path,
    release_id: str,
    *,
    repository_root: Path | None = None,
) -> VerifiedHandoff:
    root = project.expanduser().resolve()
    if not (root / "project.json").is_file():
        raise HbgBridgeProvenanceError(f"not an initialized project: {root}")
    if not release_id.strip():
        raise HbgBridgeProvenanceError("release_id is required")

    manifest_path = root / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
    manifest = _load_object(manifest_path, "content package manifest")
    if manifest.get("schema_version") != "content-package-manifest.v1":
        raise HbgBridgeProvenanceError("content package manifest schema is invalid")
    if manifest.get("release_id") != release_id:
        raise HbgBridgeProvenanceError("content package release does not match requested release")
    package_digest = manifest.get("package_digest")
    if not isinstance(package_digest, str) or len(package_digest) != 64:
        raise HbgBridgeProvenanceError("content package digest is invalid")

    outputs = manifest.get("output_hashes")
    if not isinstance(outputs, Mapping) or not outputs:
        raise HbgBridgeProvenanceError("content package manifest has no output hashes")
    for relative_value, expected in outputs.items():
        path = _safe_project_path(root, relative_value, "content package output")
        if not path.is_file() or not isinstance(expected, str) or sha256_file(path) != expected:
            raise HbgBridgeProvenanceError(f"content package output hash mismatch: {relative_value}")

    stage_path = _safe_project_path(root, manifest.get("stage_manifest_path"), "stage manifest")
    expected_stage = manifest.get("stage_manifest_sha256")
    if not stage_path.is_file() or not isinstance(expected_stage, str) or sha256_file(stage_path) != expected_stage:
        raise HbgBridgeProvenanceError("stage manifest hash mismatch")
    stage_manifest = _load_object(stage_path, "Phase 1 stage manifest")
    if (
        stage_manifest.get("stage") != "phase1_content_brain"
        or stage_manifest.get("release_id") != release_id
        or stage_manifest.get("status") != "success"
    ):
        raise HbgBridgeProvenanceError("Phase 1 stage manifest identity or status is invalid")
    stage_outputs = stage_manifest.get("outputs")
    if not isinstance(stage_outputs, list):
        raise HbgBridgeProvenanceError("Phase 1 stage manifest has no outputs")
    stage_hashes: dict[str, str] = {}
    for item in stage_outputs:
        if not isinstance(item, Mapping):
            raise HbgBridgeProvenanceError("Phase 1 stage manifest output record is invalid")
        relative = item.get("path")
        digest = item.get("sha256")
        byte_count = item.get("bytes")
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
            or relative in stage_hashes
        ):
            raise HbgBridgeProvenanceError("Phase 1 stage manifest output record is invalid")
        output_path = _safe_project_path(root, relative, "Phase 1 stage output")
        if not output_path.is_file() or output_path.stat().st_size != byte_count:
            raise HbgBridgeProvenanceError(f"Phase 1 stage manifest byte count mismatch: {relative}")
        stage_hashes[relative] = digest
    if stage_hashes != dict(outputs):
        raise HbgBridgeProvenanceError("Phase 1 stage manifest outputs do not match the content manifest")

    lock_path = root / "02_story_script_故事脚本/SCRIPT_LOCK.json"
    script_lock = _load_object(lock_path, "script lock")
    if script_lock.get("release_id") != release_id or script_lock.get("package_digest") != package_digest:
        raise HbgBridgeProvenanceError("script lock does not match the content package")
    if script_lock.get("machine_locked") is not True:
        raise HbgBridgeProvenanceError("script lock is not machine locked")

    script_package_path = root / "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
    script_package = _load_object(script_package_path, "script package")
    if script_package.get("release_id") != release_id or script_package.get("package_digest") != package_digest:
        raise HbgBridgeProvenanceError("script package does not match the content package")
    script = script_package.get("script")
    if not isinstance(script, Mapping):
        raise HbgBridgeProvenanceError("script package has no script object")
    release = script.get("release_version")
    release_text = release.get("text") if isinstance(release, Mapping) else None
    if not isinstance(release_text, str) or not release_text:
        raise HbgBridgeProvenanceError("script package has no frozen release text")
    release_hash = hashlib.sha256(release_text.encode("utf-8")).hexdigest()
    if script_lock.get("release_text_sha256") != release_hash:
        raise HbgBridgeProvenanceError("release text hash does not match the script lock")
    release_artifact = root / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    try:
        release_artifact_text = release_artifact.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise HbgBridgeProvenanceError("frozen release artifact is unreadable") from error
    expected_release_artifact = render_script_markdown(
        "单主播口播发布稿", release, mode="release"
    )
    if release_artifact_text != expected_release_artifact:
        raise HbgBridgeProvenanceError("frozen release artifact differs from the script package")

    approval = current_approvals(root, release_id).get("script")
    if approval is None:
        raise HbgBridgeProvenanceError("a current script approval is required")
    for relative in _REQUIRED_APPROVAL_PATHS:
        if not approval_covers_path(root, approval, root / relative):
            raise HbgBridgeProvenanceError(f"script approval does not cover required artifact: {relative}")

    hbg_commit = _verify_vendor(repository_root or _default_repository_root())
    return VerifiedHandoff(
        project=root,
        release_id=release_id,
        package_digest=package_digest,
        release_text=release_text,
        release_text_sha256=release_hash,
        script_package=dict(script_package),
        content_manifest=dict(manifest),
        approval_event=dict(approval),
        hbg_commit=hbg_commit,
    )
