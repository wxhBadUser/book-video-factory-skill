"""Host contract and Runtime materializer for the V2 Literary Director."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from book_video_factory.host_orchestration import register_action, register_host_event
from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_covenant import verify_asset_catalog, verify_visual_covenant_approval


class LiteraryDirectorError(RuntimeError):
    """Director result or materialized artifacts are invalid."""


RESULT_SCHEMA = "literary-director-result.v2"
RESULT_REL = "manifests/host_orchestration/LITERARY_DIRECTOR_RESULT.v2.json"
POLICY_DEFAULT_REL = "config/DIRECTOR_POLICY.json"
VP_REL = "04_director/VISUAL_PARAGRAPHS.json"
DECISIONS_REL = "04_director/EDIT_DECISIONS.v2.json"
PLAN_REL = "04_director/PRODUCTION_IMAGE_PLAN.v2.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def _required_sha(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LiteraryDirectorError(f"{field} must be a sha256")
    return value


def _input(root: Path, relative: str) -> dict[str, str]:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise LiteraryDirectorError(f"director input is missing or symlinked: {relative}")
    return {"kind": "artifact", "path": relative, "sha256": sha256_file(path)}


def register_literary_director_action(project: Path, *, policy_path: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    lock = verify_locked_script(root)
    if lock.get("status") != "script_locked":
        raise LiteraryDirectorError(f"locked script is not verified: {lock.get('status')}")
    approval = verify_visual_covenant_approval(root)
    if approval.get("status") != "visual_covenant_approval_verified":
        raise LiteraryDirectorError(f"visual covenant approval is not verified: {approval.get('status')}")
    catalog = verify_asset_catalog(root)
    if catalog.get("status") != "asset_catalog_verified":
        raise LiteraryDirectorError(f"asset catalog is not verified: {catalog.get('status')}")
    policy = policy_path.expanduser().resolve()
    package_policy = Path(__file__).resolve().parents[2] / "config" / "DIRECTOR_POLICY.json"
    if policy.is_symlink() or not policy.is_file() or (root not in policy.parents and policy != package_policy.resolve()):
        raise LiteraryDirectorError("director policy must be a project-local or packaged real file")
    inputs = [
        _input(root, str(lock["script_path"])),
        _input(root, "04_audio/AUDIO_TIMELINE.v2.json"),
        _input(root, "04_audio/CAPTION_TIMELINE.json"),
        _input(root, "04_visual_covenant_视觉契约/VISUAL_COVENANT_APPROVAL.v2.json"),
        _input(root, "manifests/asset_catalog/ASSET_CATALOG.v2.json"),
        {"kind": "director_policy", "path": policy.relative_to(root).as_posix(), "sha256": sha256_file(policy)},
    ]
    action = {
        "schema_version": "host-agent-action.v2",
        "action_id": f"DIRECTOR_{lock['release_id']}",
        "release_id": str(lock["release_id"]),
        "project_id": root.name,
        "action_type": "plan_literary_director",
        "idempotency_key": f"{lock['release_id']}:LITERARY_DIRECTOR:attempt-1",
        "attempt": 1,
        "inputs": inputs,
        "expected_output": {"schema_version": RESULT_SCHEMA, "result_path": RESULT_REL},
        "max_runtime_seconds": 900,
    }
    register_action(root, action)
    return action


def validate_literary_director_result(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    root = project.expanduser().resolve()
    if payload.get("schema_version") != RESULT_SCHEMA:
        raise LiteraryDirectorError("literary director result schema_version is invalid")
    if payload.get("project_id") != root.name:
        raise LiteraryDirectorError("literary director result project_id is invalid")
    lock = verify_locked_script(root)
    if payload.get("release_id") != lock.get("release_id"):
        raise LiteraryDirectorError("literary director result release_id is stale")
    for field in (
        "locked_script_sha256",
        "audio_timeline_sha256",
        "caption_timeline_sha256",
        "visual_covenant_approval_sha256",
        "asset_catalog_sha256",
        "director_policy_sha256",
    ):
        _required_sha(payload, field)
    paragraphs = payload.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise LiteraryDirectorError("literary director result paragraphs must be nonempty")
    previous_end = 0.0
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = ("paragraph_id", "start", "end", "source_caption_ids", "narrative_focus", "visual_function", "visual_center", "mood", "rationale", "decision")
    for raw in paragraphs:
        if not isinstance(raw, dict):
            raise LiteraryDirectorError("literary director paragraph must be an object")
        if any(not isinstance(raw.get(field), str) or not raw[field].strip() for field in required if field not in {"start", "end", "source_caption_ids", "decision"}):
            raise LiteraryDirectorError("literary director paragraph is missing required reasoning fields")
        paragraph_id = raw.get("paragraph_id")
        if not isinstance(paragraph_id, str) or not paragraph_id or paragraph_id in seen:
            raise LiteraryDirectorError("literary director paragraph_id is invalid or duplicated")
        start, end = raw.get("start"), raw.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start or float(start) < previous_end - 1e-3:
            raise LiteraryDirectorError(f"literary director timing is invalid: {paragraph_id}")
        captions = raw.get("source_caption_ids")
        if not isinstance(captions, list) or not captions or not all(isinstance(item, str) and item for item in captions):
            raise LiteraryDirectorError(f"literary director captions are invalid: {paragraph_id}")
        decision = raw.get("decision")
        if decision not in {"hold_current", "reuse_asset", "generate_new"}:
            raise LiteraryDirectorError(f"literary director decision is invalid: {paragraph_id}")
        if decision == "reuse_asset" and (not isinstance(raw.get("asset_id"), str) or not raw["asset_id"]):
            raise LiteraryDirectorError(f"reuse_asset requires asset_id: {paragraph_id}")
        if decision == "generate_new":
            for field in ("asset_family", "minimal_generation_intent"):
                if not isinstance(raw.get(field), str) or not raw[field].strip():
                    raise LiteraryDirectorError(f"generate_new requires {field}: {paragraph_id}")
            refs = raw.get("recommended_reference_asset_ids")
            if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
                raise LiteraryDirectorError(f"generate_new references are invalid: {paragraph_id}")
        normalized.append(dict(raw))
        seen.add(paragraph_id)
        previous_end = float(end)
    audio = json.loads(safe_project_output(root, Path("04_audio/AUDIO_TIMELINE.v2.json")).read_text(encoding="utf-8"))
    duration = float(audio.get("narration_duration_seconds", 0.0))
    if abs(previous_end - duration) > 1e-3:
        raise LiteraryDirectorError("literary director paragraphs do not cover narration duration")
    return {**payload, "paragraphs": normalized}


def materialize_literary_director_result(project: Path, result_path: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    path = result_path.expanduser().resolve()
    if root not in path.parents or path.is_symlink() or not path.is_file():
        raise LiteraryDirectorError("director result must be a project-local real file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LiteraryDirectorError(f"director result is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise LiteraryDirectorError("director result must be an object")
    result = validate_literary_director_result(root, payload)
    lock = verify_locked_script(root)
    if result["locked_script_sha256"] != lock["script_sha256"]:
        raise LiteraryDirectorError("director result locked script hash is stale")
    audio_path = safe_project_output(root, Path("04_audio/AUDIO_TIMELINE.v2.json"))
    caption_path = safe_project_output(root, Path("04_audio/CAPTION_TIMELINE.json"))
    if result["audio_timeline_sha256"] != sha256_file(audio_path) or result["caption_timeline_sha256"] != sha256_file(caption_path):
        raise LiteraryDirectorError("director result audio/caption hash is stale")
    approval = verify_visual_covenant_approval(root)
    catalog = verify_asset_catalog(root)
    if result["visual_covenant_approval_sha256"] != approval.get("visual_covenant_approval_sha256"):
        raise LiteraryDirectorError("director result covenant approval hash is stale")
    if result["asset_catalog_sha256"] != catalog.get("catalog_sha256"):
        raise LiteraryDirectorError("director result asset catalog hash is stale")
    policy_candidates = [
        root / POLICY_DEFAULT_REL,
        Path(__file__).resolve().parents[2] / "config" / "DIRECTOR_POLICY.json",
    ]
    if not any(path.is_file() and not path.is_symlink() and sha256_file(path) == result["director_policy_sha256"] for path in policy_candidates):
        raise LiteraryDirectorError("director policy hash is stale or unavailable")
    paragraphs = []
    decisions = []
    plan_assets = []
    for item in result["paragraphs"]:
        decision = item["decision"]
        paragraph = {
            **{field: item[field] for field in ("paragraph_id", "start", "end", "source_caption_ids", "mood")},
            "visual_intent": item["visual_function"],
            "narrative_focus": item["narrative_focus"],
            "visual_function": item["visual_function"],
            "visual_center": item["visual_center"],
            "rationale": item["rationale"],
        }
        paragraphs.append(paragraph)
        edit = {"paragraph_id": item["paragraph_id"], "decision": decision}
        if decision == "reuse_asset":
            edit["asset_id"] = item["asset_id"]
        elif decision == "generate_new":
            asset_id = f"PROD_{item['paragraph_id']}"
            edit["asset_id"] = asset_id
            plan_assets.append({
                "asset_id": asset_id,
                "asset_family": item["asset_family"],
                "visual_function": item["visual_function"],
                "bound_paragraph_id": item["paragraph_id"],
                "minimal_generation_intent": item["minimal_generation_intent"],
                "recommended_reference_asset_ids": item["recommended_reference_asset_ids"],
            })
        decisions.append(edit)
    vp = {
        "schema_version": "visual-paragraphs.v2",
        "release_id": result["release_id"],
        "project_id": result["project_id"],
        "audio_timeline_sha256": result["audio_timeline_sha256"],
        "narration_duration_seconds": float(json.loads(audio_path.read_text(encoding="utf-8"))["narration_duration_seconds"]),
        "paragraphs": paragraphs,
    }
    _atomic_json(safe_project_output(root, Path(VP_REL)), vp)
    decisions_payload = {
        "schema_version": "edit-decisions.v2",
        "release_id": result["release_id"],
        "project_id": result["project_id"],
        "visual_paragraphs_sha256": sha256_file(safe_project_output(root, Path(VP_REL))),
        "decisions": decisions,
    }
    _atomic_json(safe_project_output(root, Path(DECISIONS_REL)), decisions_payload)
    _atomic_json(safe_project_output(root, Path(PLAN_REL)), {
        "schema_version": "production-image-plan.v2",
        "release_id": result["release_id"],
        "project_id": result["project_id"],
        "literary_director_result_sha256": sha256_file(path),
        "assets": plan_assets,
    })
    return {"status": "literary_director_materialized", "visual_paragraphs_path": VP_REL, "asset_count": len(plan_assets)}


def complete_literary_director_from_host_event(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Record one validated Host result event, then let Runtime materialize it."""
    root = project.expanduser().resolve()
    register_host_event(root, event)
    result_path = safe_project_output(root, Path(str(event.get("output_path", ""))))
    return materialize_literary_director_result(root, result_path)
