from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_video_factory.manifests import record_approval, safe_project_output, sha256_file


class FinalMasterApprovalError(RuntimeError):
    """Final encoded master approval evidence is missing, stale, or inconsistent."""


@dataclass(frozen=True)
class FinalMasterApprovalResult:
    status: str
    approval_path: Path
    event_path: Path
    next_stage_status: str


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FinalMasterApprovalError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalMasterApprovalError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise FinalMasterApprovalError(f"{label} must be an object")
    return value


def _project_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
        raise FinalMasterApprovalError(f"{label} path is invalid")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise FinalMasterApprovalError(f"{label} escapes the project") from error
    if path.is_symlink() or not path.is_file():
        raise FinalMasterApprovalError(f"{label} is missing or symlinked")
    return path


def _current_subjects(root: Path) -> tuple[dict[str, Any], list[Path]]:
    final_path = root / "08_render_合成/final/FINAL_RENDER_MANIFEST.json"
    final = _load(final_path, "final render manifest")
    if final.get("schema_version") != "final-render-manifest.v1":
        raise FinalMasterApprovalError("final render manifest schema is invalid")
    if final.get("next_stage_status") != "awaiting_final_master_approval":
        raise FinalMasterApprovalError("final render is not awaiting master approval")
    video = _project_file(root, final.get("video_path"), "final video")
    qa = _project_file(root, final.get("qa_report_path"), "final QA report")
    encoded_visual = _project_file(root, final.get("encoded_visual_qa_path"), "encoded visual QA report")
    final_contact = _project_file(root, final.get("final_contact_sheet_path"), "final encoded contact sheet")
    caption_parity = _project_file(root, final.get("caption_parity_path"), "caption parity report")
    render_manifest_path = root / "07_render/RENDER_MANIFEST.json"
    if render_manifest_path.is_symlink() or not render_manifest_path.is_file():
        raise FinalMasterApprovalError("render manifest is missing")
    if sha256_file(video) != final.get("video_sha256"):
        raise FinalMasterApprovalError("final video hash differs from final render evidence")
    if sha256_file(qa) != final.get("qa_report_sha256"):
        raise FinalMasterApprovalError("final QA report hash differs from final render evidence")
    if sha256_file(encoded_visual) != final.get("encoded_visual_qa_sha256"):
        raise FinalMasterApprovalError("encoded visual QA hash differs from final render evidence")
    if sha256_file(final_contact) != final.get("final_contact_sheet_sha256"):
        raise FinalMasterApprovalError("final contact sheet hash differs from final render evidence")
    if sha256_file(caption_parity) != final.get("caption_parity_sha256"):
        raise FinalMasterApprovalError("caption parity hash differs from final render evidence")
    if sha256_file(render_manifest_path) != final.get("render_manifest_sha256"):
        raise FinalMasterApprovalError("render manifest hash differs from final render evidence")
    qa_report = _load(qa, "final QA report")
    if qa_report.get("status") != "pass":
        raise FinalMasterApprovalError("final QA report is not passing")
    encoded_report = _load(encoded_visual, "encoded visual QA report")
    if encoded_report.get("status") != "pass" or encoded_report.get("machine_qa_passed") is not True:
        raise FinalMasterApprovalError("encoded visual QA machine validation has not passed")
    if (
        qa_report.get("encoded_visual_qa_sha256") != sha256_file(encoded_visual)
        or qa_report.get("final_contact_sheet_sha256") != sha256_file(final_contact)
        or qa_report.get("caption_parity_sha256") != sha256_file(caption_parity)
    ):
        raise FinalMasterApprovalError("final QA report does not bind encoded semantic evidence")
    caption_report = _load(caption_parity, "caption parity report")
    if caption_report.get("status") != "pass":
        raise FinalMasterApprovalError("encoded HTML/ASS caption parity has not passed")
    # Encoded QA is machine validation (not a human gate); no approval event
    # is required. The QA report itself is the validation record.
    expected_encoded_subjects = {
        encoded_report.get("frame_plan_path"): encoded_report.get("frame_plan_sha256"),
        encoded_report.get("decision_path"): encoded_report.get("decision_sha256"),
        encoded_report.get("video_path"): encoded_report.get("video_sha256"),
        "09_qc/final-video/contact-sheet.jpg": encoded_report.get("hbg_contact_sheet_sha256"),
    }
    for frame in encoded_report.get("frames", []):
        if isinstance(frame, dict):
            expected_encoded_subjects[frame.get("path")] = frame.get("sha256")
    # Machine validation: every frame listed in the QA report must exist and
    # match its recorded hash (no human approval event to cross-check).
    for relative, sha in expected_encoded_subjects.items():
        if not isinstance(relative, str) or not relative:
            continue
        subject_path = root / relative
        if subject_path.is_symlink() or not subject_path.is_file():
            raise FinalMasterApprovalError(f"encoded QA evidence is missing: {relative}")
        if sha and sha256_file(subject_path) != sha:
            raise FinalMasterApprovalError(f"encoded QA evidence hash mismatch: {relative}")
    return final, [final_path, render_manifest_path, video, qa, encoded_visual, final_contact, caption_parity]


def verify_final_master_approval(project: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    final, subjects = _current_subjects(root)
    approval_path = root / "10_delivery_交付/FINAL_MASTER_APPROVAL.json"
    approval = _load(approval_path, "final master approval")
    required = {
        "schema_version", "release_id", "human_approved", "reviewer", "note",
        "subjects", "approval_event_path", "approval_event_sha256", "next_stage_status",
    }
    if set(approval) != required or approval.get("schema_version") != "final-master-approval.v1":
        raise FinalMasterApprovalError("final master approval fields are invalid")
    if approval.get("release_id") != final.get("release_id") or approval.get("human_approved") is not True:
        raise FinalMasterApprovalError("final master approval release or decision is invalid")
    if approval.get("next_stage_status") != "complete":
        raise FinalMasterApprovalError("final master approval status is invalid")
    reviewer = approval.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise FinalMasterApprovalError("final master approval reviewer is invalid")
    expected_subjects = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in subjects
    }
    if approval.get("subjects") != expected_subjects:
        raise FinalMasterApprovalError("final master approval subjects are incomplete or stale")
    event = _project_file(root, approval.get("approval_event_path"), "final master approval event")
    if sha256_file(event) != approval.get("approval_event_sha256"):
        raise FinalMasterApprovalError("final master approval event hash is stale")
    event_payload = _load(event, "final master approval event")
    if (
        event_payload.get("gate") != "local_master_review"
        or event_payload.get("decision") != "approved"
        or event_payload.get("release_id") != final.get("release_id")
        or event_payload.get("reviewer") != reviewer
        or event_payload.get("note") != approval.get("note")
    ):
        raise FinalMasterApprovalError("final master approval event does not match approval record")
    event_subjects = {
        item.get("path"): item.get("sha256")
        for item in event_payload.get("subjects", [])
        if isinstance(item, dict)
    }
    if event_subjects != expected_subjects:
        raise FinalMasterApprovalError("final master approval event subjects are incomplete or stale")
    return approval


def approve_final_master(project: Path, *, reviewer: str, note: str) -> FinalMasterApprovalResult:
    root = project.expanduser().resolve()
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise FinalMasterApprovalError("reviewer must be a nonempty trimmed human identity")
    if not isinstance(note, str) or not note.strip() or note != note.strip():
        raise FinalMasterApprovalError("approval note must be a nonempty trimmed statement")
    approval_path = safe_project_output(root, Path("10_delivery_交付/FINAL_MASTER_APPROVAL.json"))
    if approval_path.exists():
        approval = verify_final_master_approval(root)
        event_path = _project_file(root, approval["approval_event_path"], "final master approval event")
        if approval["reviewer"] != reviewer or approval["note"] != note:
            raise FinalMasterApprovalError("existing final master approval belongs to another decision")
        return FinalMasterApprovalResult("unchanged", approval_path, event_path, "complete")
    final, subjects = _current_subjects(root)
    event_path: Path | None = None
    try:
        event_path = record_approval(
            root,
            release_id=str(final["release_id"]),
            gate="local_master_review",
            decision="approved",
            reviewer=reviewer,
            subjects=subjects,
            evidence_refs=["encoded-final-video", "hbg-final-qa"],
            note=note,
        )
        payload = {
            "schema_version": "final-master-approval.v1",
            "release_id": final["release_id"],
            "human_approved": True,
            "reviewer": reviewer,
            "note": note,
            "subjects": {path.relative_to(root).as_posix(): sha256_file(path) for path in subjects},
            "approval_event_path": event_path.relative_to(root).as_posix(),
            "approval_event_sha256": sha256_file(event_path),
            "next_stage_status": "complete",
        }
        approval_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".final-master-", suffix=".json", dir=approval_path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(_pretty(payload))
        os.replace(temp_path, approval_path)
        verify_final_master_approval(root)
        return FinalMasterApprovalResult("created", approval_path, event_path, "complete")
    except Exception as error:
        approval_path.unlink(missing_ok=True)
        if event_path is not None:
            event_path.unlink(missing_ok=True)
        if isinstance(error, FinalMasterApprovalError):
            raise
        raise FinalMasterApprovalError(f"final master approval transaction failed: {error}") from error
