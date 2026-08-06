from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import ReleaseProfile
from .narrator_essay_contracts import (
    ContractError as NarratorEssayContractError,
    validate_narrator_essay_script,
)
from .style_profiles import StyleProfile, project_workflow


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approval_is_current(project: Path, event: dict[str, Any]) -> bool:
    if event.get("decision") != "approved":
        return False
    root = project.resolve()
    if event.get("schema_version") != "1.0" or event.get("project_id") != root.name:
        return False
    if not isinstance(event.get("release_id"), str) or not event["release_id"].strip():
        return False
    subjects = event.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        return False
    for subject in subjects:
        try:
            path = (root / str(subject["path"])).resolve()
            path.relative_to(root)
        except (KeyError, ValueError):
            return False
        if not path.is_file() or _sha256(path) != subject.get("sha256"):
            return False
    return True


def load_approval_events(project: Path) -> list[dict[str, Any]]:
    root = project.resolve()
    directory = root / "logs" / "approval_events"
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == "1.0"
            and payload.get("project_id") == root.name
            and isinstance(payload.get("release_id"), str)
            and payload["release_id"].strip()
        ):
            events.append(payload)
    return events


def current_approvals(
    project: Path, release_id: str | None = None
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for event in load_approval_events(project):
        if release_id is not None and event.get("release_id") != release_id:
            continue
        gate = str(event.get("gate", ""))
        if gate:
            candidates.setdefault(gate, []).append(event)
    latest: dict[str, dict[str, Any]] = {}
    for gate, events in candidates.items():
        latest_timestamp = max(str(event.get("reviewed_at", "")) for event in events)
        newest = [
            event
            for event in events
            if str(event.get("reviewed_at", "")) == latest_timestamp
        ]
        # Old second-resolution logs can contain conflicting decisions with the
        # same timestamp. Do not use UUID filename ordering to guess intent.
        if len(newest) == 1:
            latest[gate] = newest[0]
    return {
        gate: event
        for gate, event in latest.items()
        if approval_is_current(project, event)
    }


def approval_covers_path(project: Path, event: dict[str, Any], path: Path) -> bool:
    root = project.resolve()
    try:
        expected = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    return any(
        isinstance(subject, dict) and subject.get("path") == expected
        for subject in event.get("subjects", [])
    )


def _script_path(
    project: Path, profile: ReleaseProfile | None = None
) -> Path:
    return (
        project
        / "02_story_script_故事脚本"
        / "script.narrator-essay.v1.json"
    )

def _script_has_lines(path: Path, *, expected: int | None = None) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return False
    return expected is None or len(lines) == expected


def _load_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _hashed_assets_ready(
    project: Path,
    manifest_path: Path,
    *,
    minimum: int = 1,
    maximum: int | None = None,
    require_silent: bool = False,
) -> tuple[bool, bool, bool]:
    payload = _load_object(manifest_path)
    assets = payload.get("assets") if payload is not None else None
    if not isinstance(assets, list) or len(assets) < minimum:
        return False, False, False
    if maximum is not None and len(assets) > maximum:
        return True, False, False
    present = True
    hashes_match = True
    qa_passed = True
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            return True, False, False
        asset_id = asset.get("asset_id")
        relative = asset.get("path")
        expected_hash = asset.get("sha256")
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or asset_id in seen_ids
            or not isinstance(relative, str)
            or not relative
            or not isinstance(expected_hash, str)
        ):
            return True, False, False
        seen_ids.add(asset_id)
        path = (manifest_path.parent / relative).resolve()
        try:
            path.relative_to(project.resolve())
        except ValueError:
            return True, False, False
        if not path.is_file():
            present = False
            hashes_match = False
            continue
        digest = _sha256(path)
        if digest != expected_hash or digest in seen_hashes:
            hashes_match = False
        seen_hashes.add(digest)
        if not str(asset.get("qa", "")).startswith("pass"):
            qa_passed = False
        if require_silent and asset.get("audio_stream") is not False:
            qa_passed = False
    return True, present and hashes_match, qa_passed


def _hbg_asset_checks(project: Path, profile: ReleaseProfile) -> dict[str, bool]:
    script_contract = False
    try:
        script = json.loads(_script_path(project, profile).read_text(encoding="utf-8"))
        validate_narrator_essay_script(script)
        script_contract = True
    except (OSError, json.JSONDecodeError, NarratorEssayContractError):
        script_contract = False

    audio_meta = _load_object(project / "audio_meta.json")
    narration_path: Path | None = None
    vtt_path: Path | None = None
    if audio_meta is not None:
        narration_value = audio_meta.get("narration") or audio_meta.get("narrationPath")
        vtt_value = audio_meta.get("vtt") or audio_meta.get("vttPath")
        if isinstance(narration_value, str) and narration_value.strip():
            narration_path = (project / narration_value).resolve()
        if isinstance(vtt_value, str) and vtt_value.strip():
            vtt_path = (project / vtt_value).resolve()

    scenes = project / "assets" / "generated" / "scenes"
    scene_files = [
        path for path in scenes.glob("*")
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ] if scenes.is_dir() else []
    return {
        "script_contract": script_contract,
        "project_spec": (project / "PROJECT_SPEC.json").is_file(),
        "characters": (project / "CHARACTERS.md").is_file(),
        "storyboard_base": (project / "STORYBOARD_BASE.json").is_file(),
        "storyboard": (project / "STORYBOARD.json").is_file(),
        "prompts": (project / "PROMPTS.md").is_file(),
        "hbg_style": (project / "HBG_STYLE.json").is_file(),
        "audio_meta": audio_meta is not None,
        "narration": bool(narration_path and narration_path.is_file()),
        "vtt": bool(vtt_path and vtt_path.is_file()),
        "scene_assets": bool(scene_files),
    }


def _asset_checks(project: Path, profile: ReleaseProfile) -> dict[str, bool]:
    return _hbg_asset_checks(project, profile)


def _delivery_manifests(project: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    directory = project / "10_delivery_交付"
    for path in sorted(directory.glob("**/delivery-manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            results.append(payload)
    return results


def _delivery_manifest_for_release(
    project: Path, release_id: str | None
) -> dict[str, Any] | None:
    if release_id is None:
        return None
    matches = [
        payload
        for payload in _delivery_manifests(project)
        if payload.get("release_id") == release_id
    ]
    return matches[-1] if matches else None


def _qc_passed(
    project: Path, profile: ReleaseProfile, release_id: str | None
) -> bool:
    if release_id is None:
        return False
    for name in ("release-gate.json", "qc-report.json"):
        path = project / "09_qc_质检" / release_id / name
        if not path.is_file():
            continue
        try:
            qc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if qc.get("release_id") == release_id and qc.get(
            "local_master_status"
        ) == "pass":
            return True

    delivery = _delivery_manifest_for_release(project, release_id)
    if delivery is None:
        return False
    qc = delivery.get("qc")
    local_master = delivery.get("local_master")
    if not isinstance(qc, dict) or not str(qc.get("status", "")).startswith("pass"):
        return False
    if not isinstance(local_master, dict) or not isinstance(local_master.get("path"), str):
        return False
    master_path = (project / local_master["path"]).resolve()
    try:
        master_path.relative_to(project.resolve())
    except ValueError:
        return False
    return bool(
        master_path.is_file()
        and _sha256(master_path) == local_master.get("sha256")
    )


def _english_is_delivered(project: Path) -> bool:
    path = _script_path(project)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        lines = payload.get("lines", [])
        if isinstance(lines, list) and any(
            isinstance(line, dict) and str(line.get("en", "")).strip()
            for line in lines
        ):
            return True
    return any((project / "10_delivery_交付").glob("**/*en*.srt"))


def _real_cover_is_used(project: Path) -> bool:
    cover_manifest = (
        project / "01_research_资料搜集/sources/cover/cover_manifest.json"
    )
    if cover_manifest.is_file():
        return True
    return any(
        path.is_file()
        for path in (project / "03_images_生成图片").glob("**/*book-cover*")
    )


def _conditional_approval_status(
    project: Path, style: StyleProfile, approvals: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for gate, condition in style.conditional_publish_approvals.items():
        triggered = False
        if gate == "cover_rights":
            triggered = _real_cover_is_used(project)
        elif gate == "english_native":
            triggered = _english_is_delivered(project)
        elif gate == "showcase_publish":
            # A repository showcase is a separate derivative decision and must
            # never block an otherwise valid production release.
            triggered = False
        results[gate] = {
            "condition": condition,
            "triggered": triggered,
            "approved": gate in approvals,
        }
    return results


def evaluate_workflow_state(
    project: Path,
    profile: ReleaseProfile,
    release_id: str | None = None,
) -> dict[str, Any]:
    root = project.resolve()
    project_contract = root / "project.json"
    workflow = project_workflow(root)
    mode = str(workflow["mode"])
    style = workflow["style_profile"]
    profile_aligned = workflow["release_profile_id"] == profile.profile_id
    mode_valid = mode == "single-book"
    asset_checks = _asset_checks(root, profile)
    content_status = {"required": False, "errors": []}
    approval_events = load_approval_events(root)
    available_release_ids = sorted(
        {
            str(event["release_id"])
            for event in approval_events
            if isinstance(event.get("release_id"), str) and event["release_id"].strip()
        }
    )
    traced_release_id = None
    if release_id is not None and not release_id.strip():
        raise ValueError("release_id cannot be empty")
    active_release_id = release_id or traced_release_id
    ambiguous_release_scope = active_release_id is None and len(available_release_ids) > 1
    if active_release_id is None and len(available_release_ids) == 1:
        active_release_id = available_release_ids[0]
    release_scope_valid = not ambiguous_release_scope and not (
        release_id is not None
        and traced_release_id is not None
        and release_id != traced_release_id
    )
    approvals = (
        current_approvals(root, active_release_id)
        if active_release_id is not None and release_scope_valid
        else {}
    )
    qc_passed = _qc_passed(root, profile, active_release_id)
    conditional = _conditional_approval_status(root, style, approvals)
    required_publish_approvals = list(style.required_publish_approvals)
    required_publish_approvals.extend(
        gate
        for gate, status in conditional.items()
        if status["triggered"] and gate != "showcase_publish"
    )
    required_publish_approvals = list(dict.fromkeys(required_publish_approvals))
    missing_publish_approvals = [
        gate for gate in required_publish_approvals if gate not in approvals
    ]

    state = "draft"
    if (
        not project_contract.is_file()
        or not mode_valid
        or not release_scope_valid
        or not profile_aligned
    ):
        state = "invalid"
    elif "topic" in approvals:
        state = "topic_approved"
        source_gate = (
            "source_audit"
            if "source_audit" in style.required_publish_approvals
            else "source"
        )
        source_ready = source_gate in approvals
        if source_ready:
            state = "source_audited"
            script_path = _script_path(root, profile)
            script_ready = "script" in approvals and approval_covers_path(
                root, approvals["script"], script_path
            )
            if script_ready:
                state = "script_reviewed"
                content_assets_ready = True
                style_asset_approvals_ready = all(
                    gate in approvals for gate in style.asset_ready_approvals
                )
                if (
                    all(asset_checks.values())
                    and content_assets_ready
                    and style_asset_approvals_ready
                ):
                    state = "assets_ready"
                    if style.timeline_approval_gate in approvals:
                        state = "timeline_verified"
                        if qc_passed:
                            state = "qc_passed"
                            if not missing_publish_approvals:
                                state = "ready_to_publish"
    return {
        "schema_version": "1.0",
        "project_id": root.name,
        "workflow_mode": mode,
        "style_profile_id": style.style_id,
        "style_display_name": style.display_name_zh,
        "execution_mode": style.execution_mode,
        "generation_lane": workflow["generation_lane"],
        "release_id": active_release_id,
        "available_release_ids": available_release_ids,
        "release_scope_valid": release_scope_valid,
        "release_profile_id": profile.profile_id,
        "release_profile_aligned": profile_aligned,
        "derived_state": state,
        "ready_to_publish": state == "ready_to_publish",
        "asset_checks": asset_checks,
        "content_system": content_status,
        "current_approval_gates": sorted(approvals),
        "required_publish_approvals": required_publish_approvals,
        "conditional_publish_approvals": conditional,
        "missing_publish_approvals": missing_publish_approvals,
        "qc_passed": qc_passed,
    }
