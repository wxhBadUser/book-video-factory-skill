"""V2 low-density Director contracts and the resolved EDIT_TIMELINE (fail-closed)."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_covenant import (
    VisualCovenantError,
    covenant_production_assets,
    verify_asset_catalog,
)


class DirectorStageV2Error(RuntimeError):
    """A V2 Director input or resolved timeline is missing, tampered, or out of bounds."""


VISUAL_PARAGRAPHS_REL = "04_director/VISUAL_PARAGRAPHS.json"
EDIT_DECISIONS_REL = "04_director/EDIT_DECISIONS.v2.json"
EDIT_TIMELINE_REL = "04_director/EDIT_TIMELINE.v2.json"
AUDIO_TIMELINE_REL = "04_audio/AUDIO_TIMELINE.v2.json"


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_object(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise DirectorStageV2Error(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DirectorStageV2Error(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise DirectorStageV2Error(f"{label} must be a JSON object")
    return value


def _to_seconds(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DirectorStageV2Error(f"{label} is not a finite number")
    number = float(value)
    if number < 0.0:
        raise DirectorStageV2Error(f"{label} must not be negative")
    return number


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_bytes(_pretty(payload))
    os.replace(staging, path)


def validate_visual_paragraphs(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the Visual Paragraph partition and its audio-timeline hash binding."""
    schema_version = payload.get("schema_version")
    if schema_version not in {"visual-paragraphs.v1", "visual-paragraphs.v2"}:
        raise DirectorStageV2Error("visual paragraphs schema_version is invalid")
    for field in ("release_id", "project_id"):
        if not isinstance(payload.get(field), str) or not payload.get(field):
            raise DirectorStageV2Error(f"visual paragraphs {field} is required")
    audio_sha = payload.get("audio_timeline_sha256")
    if not isinstance(audio_sha, str) or not audio_sha:
        raise DirectorStageV2Error("visual paragraphs audio_timeline_sha256 is required")
    duration = _to_seconds(payload.get("narration_duration_seconds"), "visual paragraphs narration_duration_seconds")
    paragraphs = payload.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise DirectorStageV2Error("visual paragraphs paragraphs must be a nonempty list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    previous_end = 0.0
    for index, raw in enumerate(paragraphs):
        if not isinstance(raw, dict):
            raise DirectorStageV2Error(f"visual paragraph {index} is not an object")
        paragraph_id = raw.get("paragraph_id")
        if not isinstance(paragraph_id, str) or not paragraph_id:
            raise DirectorStageV2Error(f"visual paragraph {index} requires paragraph_id")
        if paragraph_id in seen_ids:
            raise DirectorStageV2Error(f"duplicate visual paragraph id: {paragraph_id}")
        seen_ids.add(paragraph_id)
        start = _to_seconds(raw.get("start"), f"paragraph {paragraph_id} start")
        end = _to_seconds(raw.get("end"), f"paragraph {paragraph_id} end")
        if end <= start:
            raise DirectorStageV2Error(f"paragraph {paragraph_id} must have end > start")
        if start + 1e-3 < previous_end:
            raise DirectorStageV2Error(f"visual paragraphs overlap at {paragraph_id}")
        if start - 1e-3 > previous_end:
            raise DirectorStageV2Error(f"visual paragraphs leave an uncovered gap before {paragraph_id}")
        caption_ids = raw.get("source_caption_ids")
        if not isinstance(caption_ids, list) or not caption_ids or not all(
            isinstance(item, str) and item for item in caption_ids
        ):
            raise DirectorStageV2Error(f"paragraph {paragraph_id} requires source_caption_ids")
        for field in ("visual_intent", "mood"):
            if not isinstance(raw.get(field), str) or not raw.get(field).strip():
                raise DirectorStageV2Error(f"paragraph {paragraph_id} requires {field}")
        item = {
            "paragraph_id": paragraph_id,
            "start": start,
            "end": end,
            "source_caption_ids": [str(item) for item in caption_ids],
            "visual_intent": str(raw.get("visual_intent")).strip(),
            "mood": str(raw.get("mood")).strip(),
        }
        if schema_version == "visual-paragraphs.v2":
            for field in ("narrative_focus", "visual_function", "visual_center", "rationale"):
                if not isinstance(raw.get(field), str) or not raw[field].strip():
                    raise DirectorStageV2Error(f"paragraph {paragraph_id} requires {field}")
                item[field] = raw[field].strip()
        normalized.append(item)
        previous_end = end
    if abs(previous_end - duration) > 1e-3:
        raise DirectorStageV2Error(
            "visual paragraphs do not cover the full narration duration "
            f"({previous_end:.6g} != {duration:.6g})"
        )
    return {
        "schema_version": schema_version,
        "release_id": payload["release_id"],
        "project_id": payload["project_id"],
        "audio_timeline_sha256": audio_sha,
        "narration_duration_seconds": duration,
        "paragraphs": normalized,
    }


def validate_edit_decisions(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate Edit Decisions: one per paragraph, hold_current never carries an asset."""
    if payload.get("schema_version") != "edit-decisions.v2":
        raise DirectorStageV2Error("edit decisions schema_version is invalid")
    for field in ("release_id", "project_id"):
        if not isinstance(payload.get(field), str) or not payload.get(field):
            raise DirectorStageV2Error(f"edit decisions {field} is required")
    vp_sha = payload.get("visual_paragraphs_sha256")
    if not isinstance(vp_sha, str) or not vp_sha:
        raise DirectorStageV2Error("edit decisions visual_paragraphs_sha256 is required")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise DirectorStageV2Error("edit decisions decisions must be a nonempty list")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(decisions):
        if not isinstance(raw, dict):
            raise DirectorStageV2Error(f"edit decision {index} is not an object")
        paragraph_id = raw.get("paragraph_id")
        if not isinstance(paragraph_id, str) or not paragraph_id:
            raise DirectorStageV2Error(f"edit decision {index} requires paragraph_id")
        if paragraph_id in seen:
            raise DirectorStageV2Error(f"duplicate edit decision for {paragraph_id}")
        seen.add(paragraph_id)
        decision = raw.get("decision")
        if decision not in {"generate_new", "reuse_asset", "hold_current"}:
            raise DirectorStageV2Error(f"edit decision {paragraph_id} has invalid decision: {decision!r}")
        asset_id = raw.get("asset_id")
        if decision == "hold_current":
            if asset_id is not None:
                raise DirectorStageV2Error(f"hold_current {paragraph_id} must not carry an asset_id")
        else:
            if not isinstance(asset_id, str) or not asset_id:
                raise DirectorStageV2Error(f"{decision} {paragraph_id} requires asset_id")
        duration = raw.get("duration")
        if duration is not None:
            if not isinstance(duration, dict):
                raise DirectorStageV2Error(f"edit decision {paragraph_id} duration must be an object")
            start = _to_seconds(duration.get("start"), f"edit decision {paragraph_id} duration.start")
            end = _to_seconds(duration.get("end"), f"edit decision {paragraph_id} duration.end")
            if end <= start:
                raise DirectorStageV2Error(f"edit decision {paragraph_id} duration must have end > start")
            duration_norm = {"start": start, "end": end}
        else:
            duration_norm = None
        normalized.append({
            "paragraph_id": paragraph_id,
            "decision": decision,
            "asset_id": asset_id,
            "duration": duration_norm,
        })
    return {
        "schema_version": "edit-decisions.v2",
        "release_id": payload["release_id"],
        "project_id": payload["project_id"],
        "visual_paragraphs_sha256": vp_sha,
        "decisions": normalized,
    }


def _assert_catalog_hash(root: Path) -> str:
    status = verify_asset_catalog(root)
    if status.get("status") != "asset_catalog_verified":
        raise DirectorStageV2Error(f"asset catalog is not verified: {status.get('status')}")
    catalog_sha = status.get("catalog_sha256")
    if not isinstance(catalog_sha, str) or not catalog_sha:
        raise DirectorStageV2Error("asset catalog sha256 is missing")
    return catalog_sha


def _asset_map(project: Path) -> dict[str, dict[str, Any]]:
    try:
        assets = covenant_production_assets(project)
    except VisualCovenantError as error:
        raise DirectorStageV2Error(f"asset catalog is unavailable: {error}") from error
    return {str(item.get("asset_id")): item for item in assets if isinstance(item, dict)}


def resolve_edit_timeline(project: Path) -> dict[str, Any]:
    """Deterministically resolve Visual Paragraphs + Edit Decisions into EDIT_TIMELINE.v2.

    Unidirectional and inference-free: hold_current resolves to the previous
    resolved event's asset; every event carries an explicit asset_id. Fails
    closed on any missing/symlinked/stale input, overlap, gap, or stale asset hash.
    """

    root = project.expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise DirectorStageV2Error("project root must be a real directory")

    audio = _load_object(root, AUDIO_TIMELINE_REL, "audio timeline")
    if audio.get("schema_version") != "audio-timeline.v2":
        raise DirectorStageV2Error("audio timeline schema_version is invalid")
    audio_sha = sha256_file(safe_project_output(root, Path(AUDIO_TIMELINE_REL)))
    narration_duration = _to_seconds(audio.get("narration_duration_seconds"), "audio timeline duration")

    vp_payload = _load_object(root, VISUAL_PARAGRAPHS_REL, "visual paragraphs")
    vp = validate_visual_paragraphs(vp_payload)
    vp_sha = sha256_file(safe_project_output(root, Path(VISUAL_PARAGRAPHS_REL)))
    if vp["audio_timeline_sha256"] != audio_sha:
        raise DirectorStageV2Error("visual paragraphs audio_timeline_sha256 does not match the audio timeline")
    if str(vp["release_id"]) != str(audio.get("release_id")):
        raise DirectorStageV2Error("visual paragraphs release_id differs from the audio timeline")

    decisions_payload = _load_object(root, EDIT_DECISIONS_REL, "edit decisions")
    decisions = validate_edit_decisions(decisions_payload)
    decisions_sha = sha256_file(safe_project_output(root, Path(EDIT_DECISIONS_REL)))
    if decisions["visual_paragraphs_sha256"] != vp_sha:
        raise DirectorStageV2Error("edit decisions visual_paragraphs_sha256 does not match the visual paragraphs")
    if str(decisions["release_id"]) != str(vp["release_id"]):
        raise DirectorStageV2Error("edit decisions release_id differs from the visual paragraphs")

    paragraph_ids = [item["paragraph_id"] for item in vp["paragraphs"]]
    decision_by_paragraph = {item["paragraph_id"]: item for item in decisions["decisions"]}
    if set(decision_by_paragraph) != set(paragraph_ids):
        missing = sorted(set(paragraph_ids) - set(decision_by_paragraph))
        extra = sorted(set(decision_by_paragraph) - set(paragraph_ids))
        reason = "edit decisions must cover exactly every visual paragraph"
        if missing:
            reason += f" (missing: {','.join(missing)})"
        if extra:
            reason += f" (extra: {','.join(extra)})"
        raise DirectorStageV2Error(reason)

    catalog_sha = _assert_catalog_hash(root)
    assets = _asset_map(root)

    previous_asset_id: str | None = None
    events: list[dict[str, Any]] = []
    for index, paragraph in enumerate(vp["paragraphs"]):
        paragraph_id = paragraph["paragraph_id"]
        start = paragraph["start"]
        end = paragraph["end"]
        decision = decision_by_paragraph[paragraph_id]
        edit_id = f"EDIT_{index + 1:03d}"

        if decision["decision"] == "hold_current":
            if previous_asset_id is None:
                raise DirectorStageV2Error("the first event cannot hold_current (no previous asset)")
            asset_id = previous_asset_id
        else:
            asset_id = decision["asset_id"]
            if asset_id not in assets:
                raise DirectorStageV2Error(f"{decision['decision']} references unknown asset: {asset_id}")
            entry = assets[asset_id]
            asset_path_value = entry.get("path")
            if not isinstance(asset_path_value, str) or not asset_path_value:
                raise DirectorStageV2Error(f"catalog asset {asset_id} has no path")
            try:
                asset_path = safe_project_output(root, Path(asset_path_value))
            except ValueError as error:
                raise DirectorStageV2Error(f"catalog asset {asset_id} path is invalid: {error}") from error
            if asset_path.is_symlink() or not asset_path.is_file() or asset_path.stat().st_size < 1:
                raise DirectorStageV2Error(f"asset {asset_id} is missing, empty, or symlinked: {asset_path_value}")
            recomputed = sha256_file(asset_path)
            recorded = entry.get("file_sha256")
            if not isinstance(recorded, str) or not recorded:
                raise DirectorStageV2Error(f"catalog asset {asset_id} has no file_sha256")
            if recomputed != recorded:
                raise DirectorStageV2Error(f"asset {asset_id} file hash is stale")
        previous_asset_id = asset_id

        if events and events[-1]["asset_id"] == asset_id:
            events[-1]["end"] = end
            events[-1]["source_paragraph_ids"].append(paragraph_id)
        else:
            entry = assets.get(asset_id, {})
            asset_sha = entry.get("file_sha256") if entry else None
            if not isinstance(asset_sha, str) or not asset_sha:
                raise DirectorStageV2Error(f"asset {asset_id} has no bound file sha256")
            events.append({
                "edit_id": edit_id,
                "start": start,
                "end": end,
                "asset_id": asset_id,
                "asset_sha256": asset_sha,
                "source_paragraph_ids": [paragraph_id],
                "transition": "cut",
                "motion": "none",
            })

    if not events:
        raise DirectorStageV2Error("edit timeline resolved to no events")
    if abs(events[0]["start"] - 0.0) > 1e-3:
        raise DirectorStageV2Error("edit timeline must start at 0.0")
    if abs(events[-1]["end"] - narration_duration) > 1e-3:
        raise DirectorStageV2Error("edit timeline does not cover the full narration duration")
    for current, following in zip(events, events[1:]):
        if current["end"] + 1e-3 < following["start"] or current["end"] - 1e-3 > following["start"]:
            raise DirectorStageV2Error("edit timeline has a gap or overlap between events")

    timeline = {
        "schema_version": "edit-timeline.v2",
        "release_id": vp["release_id"],
        "project_id": vp["project_id"],
        "audio_timeline_sha256": audio_sha,
        "visual_paragraphs_sha256": vp_sha,
        "edit_decisions_sha256": decisions_sha,
        "asset_catalog_sha256": catalog_sha,
        "narration_duration_seconds": narration_duration,
        "events": events,
    }
    timeline_path = safe_project_output(root, Path(EDIT_TIMELINE_REL))
    _atomic_json(timeline_path, timeline)
    return {
        "status": "edit_timeline_resolved",
        "release_id": vp["release_id"],
        "timeline_path": EDIT_TIMELINE_REL,
        "event_count": len(events),
        "asset_catalog_sha256": catalog_sha,
    }
