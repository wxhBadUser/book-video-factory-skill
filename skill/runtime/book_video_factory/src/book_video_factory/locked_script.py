"""V2 locked-script contract intake and verification (fail-closed)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.source_ingestion import source_rights_state


class LockedScriptError(ValueError):
    """A V2 locked script is missing, tampered, or violates project boundaries."""


LOCKED_SCRIPT_RELATIVE = "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json"


def _load_payload(project: Path) -> dict[str, Any] | None:
    root = project.expanduser().resolve()
    path = root / LOCKED_SCRIPT_RELATIVE
    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def verify_locked_script(
    project: Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute hashes and boundaries, returning an authoritative status dict."""
    root = project.expanduser().resolve()
    if payload is None:
        payload = _load_payload(root)
    if payload is None:
        return {
            "schema_version": "locked-script.v2",
            "status": "missing_locked_script",
            "locked": False,
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": "locked-script.v2",
            "status": "invalid_locked_script",
            "locked": False,
        }
    if payload.get("schema_version") != "locked-script.v2":
        return {
            "schema_version": "locked-script.v2",
            "status": "invalid_locked_script",
            "locked": False,
        }
    if payload.get("lock_status") != "locked" or payload.get("rights_state") != "cleared":
        return {
            "schema_version": "locked-script.v2",
            "status": "invalid_locked_script",
            "locked": False,
        }
    project_id = str(payload.get("project_id", ""))
    if project_id != root.name:
        return {
            "schema_version": "locked-script.v2",
            "status": "invalid_locked_script",
            "locked": False,
        }
    rights = source_rights_state(root)
    if rights != "cleared":
        return {
            "schema_version": "locked-script.v2",
            "status": "blocked_rights",
            "locked": False,
            "rights_state": rights,
        }
    script_relative = payload.get("script_path")
    if not isinstance(script_relative, str) or not script_relative:
        return {
            "schema_version": "locked-script.v2",
            "status": "blocked_by_script_integrity",
            "locked": False,
        }
    try:
        script_path = safe_project_output(root, Path(script_relative))
    except (OSError, ValueError) as error:
        return {
            "schema_version": "locked-script.v2",
            "status": "blocked_by_script_integrity",
            "locked": False,
            "reason": str(error),
        }
    if script_path.is_symlink() or not script_path.is_file():
        return {
            "schema_version": "locked-script.v2",
            "status": "blocked_by_script_integrity",
            "locked": False,
        }
    actual = sha256_file(script_path)
    expected = payload.get("script_sha256")
    script_ok = isinstance(expected, str) and actual == expected
    return {
        "schema_version": "locked-script.v2",
        "status": "script_locked" if script_ok else "blocked_by_script_integrity",
        "locked": script_ok,
        "release_id": payload.get("release_id"),
        "script_path": script_relative,
        "script_sha256": actual,
        "expected_sha256": expected if isinstance(expected, str) else None,
        "hash_match": script_ok,
    }


def load_locked_script(project: Path) -> dict[str, Any] | None:
    """Load the V2 locked script payload only when it verifies as locked."""
    payload = _load_payload(project)
    if payload is None:
        return None
    status = verify_locked_script(project, payload)
    if status.get("status") != "script_locked":
        raise LockedScriptError(
            f"locked script is not valid: {status.get('status')}"
        )
    return payload


def lock_path(project: Path) -> Path:
    root = project.expanduser().resolve()
    return root / LOCKED_SCRIPT_RELATIVE
