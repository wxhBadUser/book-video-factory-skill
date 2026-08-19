"""Explicit human approval for a hash-bound sample-only visual pilot."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_video_factory.gates import approval_is_current
from book_video_factory.manifests import record_approval, safe_project_output, sha256_file, write_immutable_json
from book_video_factory.sample_visual_pilot import (
    _asset_record,
    _current_sample_scene_representative,
    _excerpt_sequences,
    minimum_sample_scene_image_count,
)
from book_video_factory.visual_stage.asset_registry import VisualAssetRegistrationError, verify_visual_asset_manifest


class SampleVisualApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleVisualApprovalResult:
    status: str
    approval_path: Path
    event_path: Path
    next_stage_status: str


@dataclass(frozen=True)
class VerifiedSampleVisualApproval:
    approved: bool
    approval_path: Path
    event_path: Path
    next_stage_status: str


_PILOT_RELATIVE = "03_images_生成图片/SAMPLE_VISUAL_PILOT_MANIFEST.json"
_APPROVAL_RELATIVE = "03_images_生成图片/SAMPLE_VISUAL_APPROVAL.json"
_DECISION_SCHEMA = "sample-visual-review-decision.v2"
_APPROVAL_SCHEMA = "sample-visual-approval.v2"
_CHECKS = {
    "semantic_checks": ["scene_signature_matches_excerpt", "required_entities_present", "forbidden_entities_absent"],
    "reality_checks": ["anatomy_tools_and_ground_contact_plausible", "no_black_border_or_embedded_text"],
    "aesthetic_checks": ["identity_and_literary_style_consistent", "subtitle_safe_composition"],
}


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SampleVisualApprovalError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise SampleVisualApprovalError(f"{label} must be a JSON object")
    return value


def _project_file(root: Path, relative: str, label: str) -> Path:
    try:
        path = safe_project_output(root, Path(relative))
    except (TypeError, ValueError) as error:
        raise SampleVisualApprovalError(f"{label} path is invalid: {error}") from error
    if path.is_symlink() or not path.is_file():
        raise SampleVisualApprovalError(f"{label} is missing or symlinked")
    return path


def _verify_pilot(root: Path, release_id: str) -> tuple[dict[str, Any], list[Path]]:
    pilot_path = _project_file(root, _PILOT_RELATIVE, "sample visual pilot")
    pilot = _load(pilot_path, "sample visual pilot")
    if (
        pilot.get("schema_version") != "sample-visual-pilot-manifest.v2"
        or pilot.get("release_id") != release_id
        or pilot.get("human_review_status") != "pending"
        or pilot.get("next_stage_status") != "blocked_by_sample_visual_review"
    ):
        raise SampleVisualApprovalError("sample visual pilot is stale or invalid")
    excerpt_ref = pilot.get("sample_excerpt")
    if not isinstance(excerpt_ref, dict) or not isinstance(excerpt_ref.get("path"), str):
        raise SampleVisualApprovalError("sample visual pilot excerpt binding is invalid")
    excerpt_path = _project_file(root, excerpt_ref["path"], "sample excerpt")
    if sha256_file(excerpt_path) != excerpt_ref.get("sha256"):
        raise SampleVisualApprovalError("sample excerpt hash is stale")
    excerpt = _load(excerpt_path, "sample excerpt")
    if excerpt.get("release_id") != release_id or excerpt.get("sample_id") != pilot.get("sample_id"):
        raise SampleVisualApprovalError("sample excerpt binding is stale or invalid")
    excerpt_sequences = _excerpt_sequences(excerpt)
    try:
        verified_assets = verify_visual_asset_manifest(root, require_complete=False)
    except VisualAssetRegistrationError as error:
        raise SampleVisualApprovalError(f"sample visual references are stale or invalid: {error}") from error
    spans = pilot.get("scene_spans")
    if not isinstance(spans, list) or not spans:
        raise SampleVisualApprovalError("sample visual pilot spans are invalid")
    required_images = minimum_sample_scene_image_count(excerpt)
    if len(spans) < required_images:
        raise SampleVisualApprovalError(
            f"sample visual pilot requires at least {required_images} scene images for this visual sample"
        )
    covered: list[int] = []
    subjects = [pilot_path, excerpt_path, _project_file(root, "03_images_生成图片/VISUAL_ASSET_MANIFEST.json", "visual asset manifest")]
    for index, span in enumerate(spans):
        if not isinstance(span, dict) or span.get("transition_in") not in {"hard_cut", "dissolve_6_8_frames"}:
            raise SampleVisualApprovalError("sample visual pilot span is invalid")
        if index == 0 and span["transition_in"] != "hard_cut":
            raise SampleVisualApprovalError("first sample span must use hard_cut")
        sequence_ids = span.get("source_sequence_ids")
        if (
            not isinstance(sequence_ids, list)
            or not sequence_ids
            or not all(isinstance(item, int) for item in sequence_ids)
            or sequence_ids != sorted(set(sequence_ids))
            or not set(sequence_ids).issubset(excerpt_sequences)
        ):
            raise SampleVisualApprovalError("sample visual pilot source sequences are invalid")
        covered.extend(sequence_ids)
        image = span.get("representative_image")
        if not isinstance(image, dict):
            raise SampleVisualApprovalError("sample visual representative is invalid")
        if image.get("kind") == "sample_scene_asset":
            source_record = image.get("source_record")
            if not isinstance(source_record, dict) or not isinstance(source_record.get("path"), str):
                raise SampleVisualApprovalError("sample scene source record is invalid")
            expected = _current_sample_scene_representative(
                root=root,
                record_relative=source_record["path"],
                release_id=release_id,
                excerpt=excerpt,
                excerpt_relative=excerpt_ref["path"],
                excerpt_sha256=excerpt_ref["sha256"],
                source_sequence_ids=sequence_ids,
                assets_by_task=verified_assets.assets_by_task,
            )
            if image != expected:
                raise SampleVisualApprovalError("sample scene representative is stale or invalid")
            subjects.extend([_project_file(root, image["path"], "sample scene image"), _project_file(root, source_record["path"], "sample scene record")])
        else:
            task_id = image.get("task_id")
            current = verified_assets.assets_by_task.get(task_id) if isinstance(task_id, str) else None
            if current is None or current.get("task_kind") != "lookdev" or image != _asset_record(current):
                raise SampleVisualApprovalError("sample LookDev representative is stale or invalid")
            subjects.append(_project_file(root, image["path"], "sample LookDev representative"))
        anchors = span.get("required_anchors")
        if not isinstance(anchors, list) or not anchors:
            raise SampleVisualApprovalError("sample visual pilot anchors are invalid")
        for anchor in anchors:
            task_id = anchor.get("task_id") if isinstance(anchor, dict) else None
            current = verified_assets.assets_by_task.get(task_id) if isinstance(task_id, str) else None
            if current is None or current.get("task_kind") == "lookdev" or anchor != _asset_record(current):
                raise SampleVisualApprovalError("sample visual pilot anchor is stale or invalid")
            subjects.append(_project_file(root, current["path"], "sample visual anchor"))
    if sorted(covered) != excerpt_sequences:
        raise SampleVisualApprovalError("sample visual pilot no longer covers the excerpt")
    deduplicated = list(dict.fromkeys(subjects))
    return pilot, deduplicated


def _validate_decision(root: Path, path: Path, pilot: dict[str, Any], release_id: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes() if path.is_file() else None
    if raw is None:
        raise SampleVisualApprovalError("sample visual decision is missing")
    decision = _load(path, "sample visual decision")
    expected_keys = {"schema_version", "release_id", "sample_id", "sample_visual_pilot_sha256", "decision", *_CHECKS}
    if set(decision) != expected_keys or decision.get("schema_version") != _DECISION_SCHEMA:
        raise SampleVisualApprovalError("sample visual decision schema is invalid")
    if (
        decision.get("release_id") != release_id
        or decision.get("sample_id") != pilot.get("sample_id")
        or decision.get("sample_visual_pilot_sha256") != sha256_file(_project_file(root, _PILOT_RELATIVE, "sample visual pilot"))
        or decision.get("decision") not in {"approved", "rejected"}
    ):
        raise SampleVisualApprovalError("sample visual decision is bound to stale evidence")
    normalized = {key: decision[key] for key in expected_keys}
    for field, expected_checks in _CHECKS.items():
        supplied = normalized[field]
        if not isinstance(supplied, list) or [item.get("check") if isinstance(item, dict) else None for item in supplied] != expected_checks:
            raise SampleVisualApprovalError(f"{field} is incomplete or out of order")
        if any(not isinstance(item, dict) or set(item) != {"check", "result"} or item["result"] not in {"pass", "fail"} for item in supplied):
            raise SampleVisualApprovalError(f"{field} has an invalid result")
    results = [item["result"] for field in _CHECKS for item in normalized[field]]
    if normalized["decision"] == "approved" and any(result != "pass" for result in results):
        raise SampleVisualApprovalError("approved sample visual decision has failed checks")
    if normalized["decision"] == "rejected" and all(result == "pass" for result in results):
        raise SampleVisualApprovalError("rejected sample visual decision has no failed checks")
    return normalized, hashlib.sha256(raw).hexdigest()


def _snapshot(root: Path, subjects: list[Path]) -> list[dict[str, Any]]:
    return [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in subjects]


def _verify_saved_checks(value: Any, decision: str) -> None:
    if not isinstance(value, dict) or set(value) != {key.removesuffix("_checks") for key in _CHECKS}:
        raise SampleVisualApprovalError("sample visual approval review checks are invalid")
    results: list[str] = []
    for field, expected_checks in _CHECKS.items():
        items = value.get(field.removesuffix("_checks"))
        if not isinstance(items, list) or [item.get("check") if isinstance(item, dict) else None for item in items] != expected_checks:
            raise SampleVisualApprovalError("sample visual approval review checks are incomplete")
        if any(not isinstance(item, dict) or set(item) != {"check", "result"} or item["result"] not in {"pass", "fail"} for item in items):
            raise SampleVisualApprovalError("sample visual approval review check result is invalid")
        results.extend(item["result"] for item in items)
    if decision == "approved" and any(item != "pass" for item in results):
        raise SampleVisualApprovalError("approved sample visual approval contains failed checks")
    if decision == "rejected" and all(item == "pass" for item in results):
        raise SampleVisualApprovalError("rejected sample visual approval contains no failed checks")


def approve_sample_visual_pilot(project: Path, *, release_id: str, reviewer: str, decision_path: Path, note: str = "") -> SampleVisualApprovalResult:
    root = project.expanduser().resolve()
    if not isinstance(reviewer, str) or reviewer != reviewer.strip() or not reviewer:
        raise SampleVisualApprovalError("reviewer is required")
    approval_path = safe_project_output(root, Path(_APPROVAL_RELATIVE))
    if approval_path.exists():
        raise SampleVisualApprovalError("refusing to overwrite existing sample visual approval")
    pilot, subjects = _verify_pilot(root, release_id)
    decision, decision_sha256 = _validate_decision(root, decision_path.expanduser().resolve(), pilot, release_id)
    event_path = record_approval(
        root,
        release_id=release_id,
        gate="sample_visual_pilot",
        decision=decision["decision"],
        reviewer=reviewer,
        subjects=subjects,
        evidence_refs=[_PILOT_RELATIVE],
        note=note,
        event_id=f"sample-visual-{sha256_file(safe_project_output(root, Path(_PILOT_RELATIVE)))[:16]}",
    )
    next_status = "ready_for_sample_audio" if decision["decision"] == "approved" else "blocked_by_sample_visual_rejection"
    payload = {
        "schema_version": _APPROVAL_SCHEMA,
        "release_id": release_id,
        "sample_id": pilot["sample_id"],
        "decision": decision["decision"],
        "reviewer": reviewer,
        "note": note,
        "sample_visual_pilot_sha256": sha256_file(safe_project_output(root, Path(_PILOT_RELATIVE))),
        "decision_input_sha256": decision_sha256,
        "review_checks": {key.removesuffix("_checks"): decision[key] for key in _CHECKS},
        "subjects": _snapshot(root, subjects),
        "approval_event": {"path": event_path.relative_to(root).as_posix(), "sha256": sha256_file(event_path)},
        "next_stage_status": next_status,
    }
    try:
        write_immutable_json(approval_path, payload)
    except Exception:
        event_path.unlink(missing_ok=True)
        raise
    return SampleVisualApprovalResult("created", approval_path, event_path, next_status)


def verify_sample_visual_approval(project: Path, release_id: str) -> VerifiedSampleVisualApproval:
    root = project.expanduser().resolve()
    pilot, subjects = _verify_pilot(root, release_id)
    approval_path = _project_file(root, _APPROVAL_RELATIVE, "sample visual approval")
    approval = _load(approval_path, "sample visual approval")
    expected_keys = {"schema_version", "release_id", "sample_id", "decision", "reviewer", "note", "sample_visual_pilot_sha256", "decision_input_sha256", "review_checks", "subjects", "approval_event", "next_stage_status"}
    if set(approval) != expected_keys or approval.get("schema_version") != _APPROVAL_SCHEMA or approval.get("release_id") != release_id or approval.get("sample_id") != pilot.get("sample_id"):
        raise SampleVisualApprovalError("sample visual approval is stale or invalid")
    decision = approval.get("decision")
    expected_status = "ready_for_sample_audio" if decision == "approved" else "blocked_by_sample_visual_rejection"
    if decision not in {"approved", "rejected"} or approval.get("next_stage_status") != expected_status:
        raise SampleVisualApprovalError("sample visual approval decision is invalid")
    _verify_saved_checks(approval.get("review_checks"), decision)
    if approval.get("sample_visual_pilot_sha256") != sha256_file(safe_project_output(root, Path(_PILOT_RELATIVE))) or approval.get("subjects") != _snapshot(root, subjects):
        raise SampleVisualApprovalError("sample visual approval subjects are stale")
    event_ref = approval.get("approval_event")
    if not isinstance(event_ref, dict) or not isinstance(event_ref.get("path"), str):
        raise SampleVisualApprovalError("sample visual approval event is invalid")
    event_path = _project_file(root, event_ref["path"], "sample visual approval event")
    if sha256_file(event_path) != event_ref.get("sha256"):
        raise SampleVisualApprovalError("sample visual approval event hash is stale")
    event = _load(event_path, "sample visual approval event")
    if event.get("gate") != "sample_visual_pilot" or event.get("decision") != decision or event.get("release_id") != release_id or event.get("reviewer") != approval.get("reviewer") or not approval_is_current(root, event):
        raise SampleVisualApprovalError("sample visual approval event is stale or invalid")
    return VerifiedSampleVisualApproval(decision == "approved", approval_path, event_path, expected_status)
