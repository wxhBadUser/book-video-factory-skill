"""Visual Foundation human approval gate (gate = visual_foundation)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.gates import approval_is_current
from book_video_factory.manifests import record_approval, safe_project_output, sha256_file
from book_video_factory.visual_foundation.contracts import VisualFoundationError
from book_video_factory.visual_foundation.manifests import (
    style_master_canonical_sha,
    CHARACTER_IDENTITY_REL,
    FOUNDATION_APPROVAL_GATE,
    FOUNDATION_APPROVAL_REL,
    FOUNDATION_DIR,
    FOUNDATION_MANIFEST_REL,
    LOCATION_ANCHOR_REL,
    STYLE_MASTER_REL,
    verify_visual_foundation,
)


@dataclass(frozen=True)
class VisualFoundationApproval:
    approved: bool
    release_id: str
    approval_path: Path
    event_path: Path
    reviewer: str
    next_stage_status: str


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_json(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise VisualFoundationError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualFoundationError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise VisualFoundationError(f"{label} must be a JSON object")
    return value


def _fill_style_master_event_sha(root: Path, event_sha: str) -> bytes:
    """Rewrite STYLE_MASTER_MANIFEST with per-master approval_event_sha256 = event SHA."""
    path = safe_project_output(root, Path(STYLE_MASTER_REL))
    payload = _load_json(root, STYLE_MASTER_REL, "style master manifest")
    items = payload.get("items")
    if not isinstance(items, list):
        raise VisualFoundationError("style master manifest items are invalid")
    for item in items:
        if isinstance(item, dict) and item.get("approved") is True:
            item["approval_event_sha256"] = event_sha
    return _pretty(payload)


def approve_visual_foundation(
    project: Path, *, release_id: str, reviewer: str, note: str = ""
) -> Path:
    root = project.expanduser().resolve()
    if not reviewer.strip() or reviewer != reviewer.strip():
        raise VisualFoundationError("reviewer is required")
    verify_visual_foundation(root, release_id)
    approval_path = safe_project_output(root, Path(FOUNDATION_APPROVAL_REL))
    if approval_path.exists():
        try:
            existing = verify_visual_foundation_approval(root, release_id)
            if existing.approved:
                return approval_path
        except VisualFoundationError:
            pass
    # Stable subjects only: profile + asset manifest + foundation manifest. The three
    # individual manifests are bound by VISUAL_FOUNDATION_APPROVAL.json (not the event)
    # so the per-master approval_event_sha256 field can be filled without circularity.
    subjects = [
        safe_project_output(root, Path(f"{FOUNDATION_DIR}/BOOK_VISUAL_PROFILE.json")),
        safe_project_output(root, Path(f"{FOUNDATION_DIR}/VISUAL_ASSET_MANIFEST.json")),
        safe_project_output(root, Path(FOUNDATION_MANIFEST_REL)),
    ]
    event_path = record_approval(
        root,
        release_id=release_id,
        gate=FOUNDATION_APPROVAL_GATE,
        decision="approved",
        reviewer=reviewer,
        subjects=subjects,
        evidence_refs=[FOUNDATION_MANIFEST_REL],
        note=note,
    )
    event_sha = sha256_file(event_path)
    style_bytes = _fill_style_master_event_sha(root, event_sha)
    style_path = safe_project_output(root, Path(STYLE_MASTER_REL))
    style_path.write_bytes(style_bytes)
    import json as _json
    style_payload = _json.loads(style_path.read_text(encoding="utf-8"))
    manifests = {
        "style_master": {"path": STYLE_MASTER_REL, "sha256": style_master_canonical_sha(style_payload)},
        "character_identity": {"path": CHARACTER_IDENTITY_REL, "sha256": sha256_file(safe_project_output(root, Path(CHARACTER_IDENTITY_REL)))},
        "location_anchor": {"path": LOCATION_ANCHOR_REL, "sha256": sha256_file(safe_project_output(root, Path(LOCATION_ANCHOR_REL)))},
    }
    approval = {
        "schema_version": "visual-foundation-approval.v1",
        "release_id": release_id,
        "reviewer": reviewer,
        "note": note,
        "event": {"path": event_path.relative_to(root).as_posix(), "sha256": event_sha},
        "manifests": manifests,
        "next_stage_status": "visual_foundation_approved",
    }
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.write_bytes(_pretty(approval))
    return approval_path


def verify_visual_foundation_approval(project: Path, release_id: str) -> VisualFoundationApproval:
    root = project.expanduser().resolve()
    verify_visual_foundation(root, release_id)
    approval = _load_json(root, FOUNDATION_APPROVAL_REL, "visual foundation approval")
    if approval.get("schema_version") != "visual-foundation-approval.v1":
        raise VisualFoundationError("unsupported visual foundation approval schema")
    if approval.get("release_id") != release_id or approval.get("next_stage_status") != "visual_foundation_approved":
        raise VisualFoundationError("visual foundation approval release or status mismatch")
    event_record = approval.get("event")
    if not isinstance(event_record, Mapping) or not isinstance(event_record.get("path"), str):
        raise VisualFoundationError("visual foundation approval event is missing")
    event_path = safe_project_output(root, Path(str(event_record["path"])))
    if event_path.is_symlink() or not event_path.is_file():
        raise VisualFoundationError("visual foundation approval event is missing")
    if sha256_file(event_path) != event_record.get("sha256"):
        raise VisualFoundationError("visual foundation approval event hash mismatch")
    event = _load_json(root, str(event_record["path"]), "visual foundation approval event")
    if (
        event.get("gate") != FOUNDATION_APPROVAL_GATE
        or event.get("release_id") != release_id
        or event.get("decision") != "approved"
        or event.get("reviewer") != approval.get("reviewer")
    ):
        raise VisualFoundationError("visual foundation approval event does not match")
    if not approval_is_current(root, event):
        raise VisualFoundationError("visual foundation approval event is not current")
    bound = approval.get("manifests")
    if not isinstance(bound, Mapping):
        raise VisualFoundationError("visual foundation approval manifests binding is invalid")
    for kind, relative in (("style_master", STYLE_MASTER_REL), ("character_identity", CHARACTER_IDENTITY_REL), ("location_anchor", LOCATION_ANCHOR_REL)):
        entry = bound.get(kind)
        if not isinstance(entry, Mapping) or entry.get("path") != relative:
            raise VisualFoundationError(f"visual foundation approval {kind} binding is invalid")
        path = safe_project_output(root, Path(relative))
        if kind == "style_master":
            import json as _json2
            payload = _json2.loads(path.read_text(encoding="utf-8"))
            current = style_master_canonical_sha(payload)
        else:
            current = sha256_file(path)
        if current != entry.get("sha256"):
            raise VisualFoundationError(f"visual foundation approval {kind} manifest hash is stale")
    # Per-master event binding: every approved style master must reference this event.
    style = _load_json(root, STYLE_MASTER_REL, "style master manifest")
    for item in style.get("items", []):
        if isinstance(item, dict) and item.get("approved") is True:
            if item.get("approval_event_sha256") != event_record.get("sha256"):
                raise VisualFoundationError(
                    f"style master {item.get('style_master_id')} is not bound to the current approval event"
                )
    return VisualFoundationApproval(
        approved=True,
        release_id=release_id,
        approval_path=safe_project_output(root, Path(FOUNDATION_APPROVAL_REL)),
        event_path=event_path,
        reviewer=str(approval.get("reviewer", "")),
        next_stage_status="visual_foundation_approved",
    )


def visual_foundation_status(project: Path, release_id: str) -> str:
    root = project.expanduser().resolve()
    if not safe_project_output(root, Path(FOUNDATION_MANIFEST_REL)).is_file():
        return "blocked_by_missing_visual_foundation"
    try:
        return verify_visual_foundation_approval(root, release_id).next_stage_status
    except VisualFoundationError:
        return "blocked_by_stale_visual_foundation"
