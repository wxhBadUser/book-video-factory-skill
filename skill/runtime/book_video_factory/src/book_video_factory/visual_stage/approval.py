from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_video_factory.gates import approval_is_current
from book_video_factory.manifests import record_approval, sha256_file, utc_now
from book_video_factory.style_profiles import StyleProfileError, project_workflow

from .review import VisualReviewError, build_visual_review


class VisualApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualApprovalResult:
    status: str
    decision: str
    approval_path: Path
    event_path: Path
    next_stage_status: str


@dataclass(frozen=True)
class VerifiedVisualApproval:
    approved: bool
    decision: str
    next_stage_status: str
    approval_path: Path
    event_path: Path
    reviewer: str


_APPROVAL_RELATIVE = "03_images_生成图片/ANCHOR_APPROVAL.json"
_REQUIRED_SUBJECTS = (
    "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
    "03_images_生成图片/VISUAL_REFERENCE_MANIFEST.json",
    "03_images_生成图片/ANCHOR_TASKS.jsonl",
    "03_images_生成图片/LOOKDEV_TASKS.jsonl",
    "03_images_生成图片/VISUAL_ASSET_MANIFEST.json",
    "03_images_生成图片/VISUAL_REVIEW_REPORT.json",
    "03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg",
    "03_images_生成图片/ANCHOR_CONTACT_SHEET.jpg",
)
_DECISION_KEYS = {
    "schema_version",
    "release_id",
    "review_digest",
    "decision",
    "semantic_checks",
    "reality_checks",
    "aesthetic_checks",
}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualApprovalError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise VisualApprovalError(f"{label} must be a JSON object")
    return value


def _project_path(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise VisualApprovalError(f"{label} must be a confined project-relative path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise VisualApprovalError(f"{label} escapes the project") from error
    return path


def _validate_check_group(
    decision: dict[str, Any], report: dict[str, Any], field: str, report_field: str
) -> list[dict[str, str]]:
    supplied = decision.get(field)
    expected_section = report.get(report_field)
    expected = expected_section.get("required_checks") if isinstance(expected_section, dict) else None
    if not isinstance(expected, list) or not all(isinstance(item, str) and item for item in expected):
        raise VisualApprovalError(f"visual review report has invalid {report_field} checks")
    if not isinstance(supplied, list) or len(supplied) != len(expected):
        raise VisualApprovalError(f"{field} must contain the complete review checklist")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(supplied):
        if not isinstance(item, dict) or set(item) != {"check", "result"}:
            raise VisualApprovalError(f"{field}[{index}] must contain only check and result")
        check = item.get("check")
        result = item.get("result")
        if check != expected[index] or result not in {"pass", "fail"}:
            raise VisualApprovalError(f"{field} does not exactly match the review checklist")
        normalized.append({"check": check, "result": result})
    return normalized


def _validate_decision(
    path: Path, *, release_id: str, report: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    raw_bytes = path.expanduser().resolve().read_bytes() if path.expanduser().resolve().is_file() else None
    if raw_bytes is None:
        raise VisualApprovalError(f"visual review decision is missing: {path}")
    decision = _load_json(path.expanduser().resolve(), "visual review decision")
    if set(decision) != _DECISION_KEYS or decision.get("schema_version") != "visual-review-decision.v1":
        raise VisualApprovalError("visual review decision schema or fields are invalid")
    if decision.get("release_id") != release_id or report.get("release_id") != release_id:
        raise VisualApprovalError("visual review decision release does not match the current release")
    if decision.get("review_digest") != report.get("review_digest"):
        raise VisualApprovalError("visual review decision is bound to a different review")
    choice = decision.get("decision")
    if choice not in {"approved", "rejected"}:
        raise VisualApprovalError("visual review decision must explicitly be approved or rejected")
    normalized = {
        "schema_version": "visual-review-decision.v1",
        "release_id": release_id,
        "review_digest": report["review_digest"],
        "decision": choice,
        "semantic_checks": _validate_check_group(decision, report, "semantic_checks", "semantic_review"),
        "reality_checks": _validate_check_group(decision, report, "reality_checks", "reality_review"),
        "aesthetic_checks": _validate_check_group(decision, report, "aesthetic_checks", "aesthetic_review"),
    }
    results = [item["result"] for field in ("semantic_checks", "reality_checks", "aesthetic_checks") for item in normalized[field]]
    if choice == "approved" and any(result != "pass" for result in results):
        raise VisualApprovalError("all review checks must pass before visual approval")
    if choice == "rejected" and all(result == "pass" for result in results):
        raise VisualApprovalError("a rejected visual decision must record at least one failed review check")
    return normalized, hashlib.sha256(raw_bytes).hexdigest()


def _subject_snapshot(root: Path) -> list[dict[str, Any]]:
    subjects=[]
    for relative in _REQUIRED_SUBJECTS:
        path=root/relative
        if not path.is_file():
            raise VisualApprovalError(f"visual approval subject is missing: {relative}")
        subjects.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    visual_manifest = _load_json(
        root / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json",
        "visual stage manifest",
    )
    stage_relative = visual_manifest.get("stage_manifest_path")
    stage_path = _project_path(root, stage_relative, "visual stage stage manifest")
    if not stage_path.is_file():
        raise VisualApprovalError("visual stage stage manifest is missing")
    subjects.append({
        "path": stage_path.relative_to(root).as_posix(),
        "bytes": stage_path.stat().st_size,
        "sha256": sha256_file(stage_path),
    })
    return subjects


def _pretty_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode("utf-8")


def _narration_next_status(root: Path) -> str:
    """Return the audio-stage status released by an approved visual stage."""

    try:
        workflow = project_workflow(root)
    except (StyleProfileError, OSError, ValueError) as error:
        raise VisualApprovalError(
            f"project workflow is required to choose the narration provider: {error}"
        ) from error
    policy = workflow.get("narration_provider_policy", "legacy_edge")
    if policy == "minimax_required":
        return "ready_for_narration"
    if policy == "legacy_edge":
        return "ready_for_edge_tts"
    raise VisualApprovalError(f"unsupported narration_provider_policy: {policy!r}")


def approve_visual_stage(
    project: Path,
    *,
    release_id: str,
    reviewer: str,
    decision_path: Path,
    note: str = "",
) -> VisualApprovalResult:
    root=project.expanduser().resolve()
    if not release_id.strip():
        raise VisualApprovalError("release_id is required")
    if not isinstance(reviewer,str) or reviewer != reviewer.strip() or not reviewer:
        raise VisualApprovalError("reviewer is required and cannot contain surrounding whitespace")
    approval_path=root/_APPROVAL_RELATIVE
    if approval_path.exists():
        raise VisualApprovalError("refusing to overwrite a pre-existing visual approval")
    narration_status = _narration_next_status(root)
    try:
        review_result=build_visual_review(root, verify_contact_render=True)
    except VisualReviewError as error:
        raise VisualApprovalError(f"current visual review is required: {error}") from error
    report=_load_json(review_result.report_path,"visual review report")
    normalized, decision_hash=_validate_decision(decision_path,release_id=release_id,report=report)
    subjects=_subject_snapshot(root)
    reviewed_at=utc_now()
    event_id=f"visual-anchor-{report['review_digest'][:16]}"
    event_path: Path|None=None
    temp_path: Path|None=None
    try:
        event_path=record_approval(
            root,
            release_id=release_id,
            gate="visual_anchor_lookdev",
            decision=normalized["decision"],
            reviewer=reviewer,
            subjects=[root/item["path"] for item in subjects],
            evidence_refs=[
                "03_images_生成图片/VISUAL_REVIEW_REPORT.json",
                "03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg",
                "03_images_生成图片/ANCHOR_CONTACT_SHEET.jpg",
            ],
            note=note,
            event_id=event_id,
            reviewed_at=reviewed_at,
        )
        event=_load_json(event_path,"visual approval event")
        next_status=(narration_status if normalized["decision"]=="approved" else "blocked_by_visual_rejection")
        payload={
            "schema_version":"visual-anchor-approval.v1",
            "release_id":release_id,
            "review_digest":report["review_digest"],
            "visual_stage_digest":report["visual_stage_digest"],
            "decision":normalized["decision"],
            "reviewer":reviewer,
            "reviewed_at":reviewed_at,
            "note":note,
            "decision_input_sha256":decision_hash,
            "review_checks":{
                "semantic":normalized["semantic_checks"],
                "reality":normalized["reality_checks"],
                "aesthetic":normalized["aesthetic_checks"],
            },
            "subjects":subjects,
            "approval_event":{
                "path":event_path.relative_to(root).as_posix(),
                "sha256":sha256_file(event_path),
                "event_id":event["event_id"],
            },
            "next_stage_status":next_status,
        }
        approval_path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{approval_path.name}.",suffix=".tmp",dir=approval_path.parent,delete=False) as handle:
            temp_path=Path(handle.name); handle.write(_pretty_bytes(payload))
        os.replace(temp_path,approval_path); temp_path=None
        return VisualApprovalResult("created",normalized["decision"],approval_path,event_path,next_status)
    except Exception as error:
        if temp_path is not None: temp_path.unlink(missing_ok=True)
        approval_path.unlink(missing_ok=True)
        if event_path is not None: event_path.unlink(missing_ok=True)
        if isinstance(error,VisualApprovalError): raise
        raise VisualApprovalError(f"visual approval transaction failed: {error}") from error


def _verify_checks(approval: dict[str,Any], decision: str) -> None:
    groups=approval.get("review_checks")
    if not isinstance(groups,dict) or set(groups)!={"semantic","reality","aesthetic"}:
        raise VisualApprovalError("visual approval review checks are invalid")
    results=[]
    for name in ("semantic","reality","aesthetic"):
        items=groups.get(name)
        if not isinstance(items,list) or not items:
            raise VisualApprovalError("visual approval review checks are incomplete")
        for item in items:
            if not isinstance(item,dict) or set(item)!={"check","result"} or item.get("result") not in {"pass","fail"}:
                raise VisualApprovalError("visual approval review check record is invalid")
            results.append(item["result"])
    if decision=="approved" and any(item!="pass" for item in results):
        raise VisualApprovalError("approved visual event contains failed checks")
    if decision=="rejected" and all(item=="pass" for item in results):
        raise VisualApprovalError("rejected visual event contains no failed check")


def verify_visual_approval(project: Path, release_id: str) -> VerifiedVisualApproval:
    root=project.expanduser().resolve(); approval_path=root/_APPROVAL_RELATIVE
    approval=_load_json(approval_path,"visual anchor approval")
    if approval.get("schema_version")!="visual-anchor-approval.v1" or approval.get("release_id")!=release_id:
        raise VisualApprovalError("visual anchor approval release or schema is invalid")
    decision=approval.get("decision")
    if decision not in {"approved","rejected"}:
        raise VisualApprovalError("visual anchor approval decision is invalid")
    _verify_checks(approval,decision)

    # Fail on cheap immutable-evidence checks before rebuilding the full visual
    # review. This prevents an obvious hash mismatch from triggering expensive
    # image diagnostics and HBG contact-sheet verification.
    subjects=approval.get("subjects")
    current=_subject_snapshot(root)
    if subjects!=current:
        raise VisualApprovalError("visual anchor approval subjects no longer match current evidence")
    event_record=approval.get("approval_event")
    if not isinstance(event_record,dict):
        raise VisualApprovalError("visual anchor approval event record is missing")
    event_path=_project_path(root,event_record.get("path"),"visual approval event")
    if not event_path.is_file() or sha256_file(event_path)!=event_record.get("sha256"):
        raise VisualApprovalError("visual approval event hash mismatch")
    event=_load_json(event_path,"visual approval event")
    if (
        event.get("event_id")!=event_record.get("event_id")
        or event.get("gate")!="visual_anchor_lookdev"
        or event.get("release_id")!=release_id
        or event.get("decision")!=decision
        or event.get("reviewer")!=approval.get("reviewer")
        or event.get("note")!=approval.get("note")
    ):
        raise VisualApprovalError("visual approval event does not match ANCHOR_APPROVAL.json")
    event_subjects=[{"path":item.get("path"),"bytes":item.get("bytes"),"sha256":item.get("sha256")} for item in event.get("subjects",[]) if isinstance(item,dict)]
    if event_subjects!=current:
        raise VisualApprovalError("visual approval event subjects are incomplete or stale")

    try:
        review_result=build_visual_review(root)
    except VisualReviewError as error:
        raise VisualApprovalError(f"visual review is stale: {error}") from error
    report=_load_json(review_result.report_path,"visual review report")
    if approval.get("review_digest")!=report.get("review_digest") or approval.get("visual_stage_digest")!=report.get("visual_stage_digest"):
        raise VisualApprovalError("visual anchor approval is bound to a stale review")

    approved=decision=="approved"
    if approved and not approval_is_current(root,event):
        raise VisualApprovalError("visual approval event is not current")
    expected_status=_narration_next_status(root) if approved else "blocked_by_visual_rejection"
    if approval.get("next_stage_status")!=expected_status:
        raise VisualApprovalError("visual approval next stage status is invalid")
    return VerifiedVisualApproval(approved,decision,expected_status,approval_path,event_path,str(approval.get("reviewer","")))


def visual_stage_next_status(project: Path, release_id: str) -> str:
    path=project.expanduser().resolve()/_APPROVAL_RELATIVE
    if not path.is_file(): return "blocked_by_visual_approval"
    try:
        return verify_visual_approval(project,release_id).next_stage_status
    except VisualApprovalError:
        return "blocked_by_stale_visual_approval"
