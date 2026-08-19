"""Import a current approved content package without transferring its approval."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .content_package import (
    ContentPackageConflict,
    ContentPackageError,
    ContentPackageResult,
    _existing_manifest,
    _verify_existing_package,
    compile_content_package,
)
from .gates import approval_covers_path, current_approvals
from .manifests import sha256_file


class ApprovedScriptImportError(RuntimeError):
    """The source package is not current or the target cannot safely receive it."""


@dataclass(frozen=True)
class ApprovedScriptImportResult:
    status: str
    content_result: ContentPackageResult
    evidence_path: Path


_REQUIRED_SCRIPT_SUBJECTS = (
    "02_story_script_故事脚本/SCRIPT_RELEASE.md",
    "02_story_script_故事脚本/SCRIPT_AUDIT.md",
    "02_story_script_故事脚本/SCRIPT_METRICS.json",
    "02_story_script_故事脚本/SCRIPT_LOCK.json",
    "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
)
_RESEARCH_INPUTS = {
    "source_manifest": "01_research_资料搜集/SOURCE_MANIFEST.json",
    "research": "01_research_资料搜集/BOOK_RESEARCH.json",
    "creative_decision": "01_research_资料搜集/CREATIVE_DECISION.json",
    "fate_anchors": "01_research_资料搜集/FATE_ANCHORS.json",
    "event_cards": "01_research_资料搜集/EVENT_CARDS.json",
    "quality": "02_story_script_故事脚本/CONTENT_QUALITY_REPORT.json",
    "originality": "02_story_script_故事脚本/ORIGINALITY_REPORT.json",
    "blind_review": "02_story_script_故事脚本/BLIND_REVIEW.json",
}
_EVIDENCE_RELATIVE = "02_story_script_故事脚本/APPROVED_SCRIPT_IMPORT.json"


def _load_object(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ApprovedScriptImportError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ApprovedScriptImportError(f"{label} is unreadable: {relative}") from error
    if not isinstance(value, dict):
        raise ApprovedScriptImportError(f"{label} must be a JSON object: {relative}")
    return value


def _source_inputs(source: Path, source_release_id: str) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any], dict[str, Any]]:
    manifest = _existing_manifest(source)
    if manifest is None or manifest.get("release_id") != source_release_id:
        raise ApprovedScriptImportError("source content package is missing or belongs to another release")
    try:
        _verify_existing_package(source, manifest, str(manifest.get("package_digest", "")))
    except ContentPackageError as error:
        raise ApprovedScriptImportError(f"source content package integrity failed: {error}") from error
    approval = current_approvals(source, source_release_id).get("script")
    if approval is None or any(
        not approval_covers_path(source, approval, source / relative)
        for relative in _REQUIRED_SCRIPT_SUBJECTS
    ):
        raise ApprovedScriptImportError("source script approval is missing, stale, or does not cover the frozen package")
    inputs = {
        name: _load_object(source, relative, name)
        for name, relative in _RESEARCH_INPUTS.items()
    }
    package = _load_object(source, "02_story_script_故事脚本/SCRIPT_PACKAGE.json", "source script package")
    script = package.get("script")
    if package.get("release_id") != source_release_id or not isinstance(script, Mapping):
        raise ApprovedScriptImportError("source script package release or script payload is invalid")
    inputs["script"] = dict(script)
    lock = _load_object(source, "02_story_script_故事脚本/SCRIPT_LOCK.json", "source script lock")
    return inputs, manifest, lock


def import_approved_content_package(
    source_project: Path,
    target_project: Path,
    *,
    source_release_id: str,
    target_release_id: str,
) -> ApprovedScriptImportResult:
    """Revalidate one source-approved package against target source pixels and relock it.

    The target package gets a new release ID and an intentionally unapproved
    script lock.  Approval events never cross project or release boundaries.
    """

    source = source_project.expanduser().resolve()
    target = target_project.expanduser().resolve()
    if source == target:
        raise ApprovedScriptImportError("source and target projects must be different")
    if not source_release_id.strip() or not target_release_id.strip():
        raise ApprovedScriptImportError("source_release_id and target_release_id are required")
    evidence_path = target / _EVIDENCE_RELATIVE
    if evidence_path.exists():
        raise ApprovedScriptImportError("target already has approved-script import evidence")
    inputs, source_manifest, source_lock = _source_inputs(source, source_release_id)
    # The Phase 1 package has two release identities: its outer package record
    # and the frozen script contract consumed by the HBG bridge.  Rebinding
    # only the outer record produces a package that later stages must reject.
    # This changes no approved prose; it gives the target's copied contract
    # the target release identity before the new package is compiled.
    target_script = dict(inputs["script"])
    target_script["release_id"] = target_release_id
    inputs["script"] = target_script
    try:
        result = compile_content_package(
            target, release_id=target_release_id, source_root=target, **inputs
        )
    except ContentPackageError as error:
        raise ApprovedScriptImportError(f"target import failed: {error}") from error
    target_lock = _load_object(target, "02_story_script_故事脚本/SCRIPT_LOCK.json", "target script lock")
    if target_lock.get("human_approved") is not False:
        raise ApprovedScriptImportError("target script lock must remain unapproved after import")
    evidence = {
        "schema_version": "approved-script-import.v1",
        "source_release_id": source_release_id,
        "source_package_digest": source_manifest.get("package_digest"),
        "source_content_manifest_sha256": sha256_file(source / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"),
        "source_script_approval_event_id": current_approvals(source, source_release_id)["script"].get("event_id"),
        "source_release_text_sha256": source_lock.get("release_text_sha256"),
        "target_release_id": target_release_id,
        "target_package_digest": result.package_digest,
        "target_content_manifest_sha256": sha256_file(result.manifest_path),
        "target_release_text_sha256": target_lock.get("release_text_sha256"),
        "source_approval_transferred": False,
        "next_stage_status": "awaiting_script_approval",
    }
    if evidence["source_release_text_sha256"] != evidence["target_release_text_sha256"]:
        raise ApprovedScriptImportError("target release text diverged from the approved source package")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ApprovedScriptImportResult(result.status, result, evidence_path)
