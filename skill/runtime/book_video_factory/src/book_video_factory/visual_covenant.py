"""V2 Visual Covenant contract, verification, and asset promotion (fail-closed)."""
from __future__ import annotations

import hashlib
import json
import copy
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import safe_project_output, sha256_file


class VisualCovenantError(ValueError):
    """A V2 visual covenant or asset catalog is missing, tampered, or out of bounds."""


COVENANT_REL = "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json"
CATALOG_REL = "manifests/asset_catalog/ASSET_CATALOG.v2.json"
APPROVAL_REL = "04_visual_covenant_视觉契约/VISUAL_COVENANT_APPROVAL.v2.json"
PROMOTION_EVENTS_REL = "manifests/asset_catalog/ASSET_PROMOTION.v2.jsonl"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def covenant_canonical_sha(payload: dict[str, Any]) -> str:
    """Stable SHA over a visual covenant with its self-referential sha field normalized
    to the empty string, so re-hashing the manifest is not invalidated by its own field."""
    import copy

    normalized = copy.deepcopy(payload)
    normalized.pop("visual_covenant_sha256", None)
    return _sha_bytes(_canonical(normalized))


def _load_payload(root: Path, relative: str) -> dict[str, Any] | None:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _verify_asset_file(root: Path, item: dict[str, Any], label: str) -> None:
    path_value = item.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise VisualCovenantError(f"{label} requires a nonempty path")
    target = safe_project_output(root, Path(path_value))
    if target.is_symlink() or not target.is_file():
        raise VisualCovenantError(f"{label} is missing or symlinked: {path_value}")
    expected = item.get("file_sha256")
    if not isinstance(expected, str) or not expected:
        raise VisualCovenantError(f"{label} requires file_sha256")
    if sha256_file(target) != expected:
        raise VisualCovenantError(f"{label} file hash is stale: {path_value}")


def verify_visual_covenant(
    project: Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute boundaries and hashes, returning an authoritative fail-closed status."""
    root = project.expanduser().resolve()
    lock_status = verify_locked_script(root)
    if lock_status.get("status") != "script_locked":
        return {
            "schema_version": "visual-covenant.v2",
            "status": lock_status.get("status", "blocked_by_script_integrity"),
            "verified": False,
        }
    if payload is None:
        payload = _load_payload(root, COVENANT_REL)
    if payload is None:
        return {
            "schema_version": "visual-covenant.v2",
            "status": "missing_visual_covenant",
            "verified": False,
        }
    if payload.get("schema_version") != "visual-covenant.v2":
        return {
            "schema_version": "visual-covenant.v2",
            "status": "invalid_visual_covenant",
            "verified": False,
        }
    if str(payload.get("project_id", "")) != root.name:
        return {
            "schema_version": "visual-covenant.v2",
            "status": "invalid_visual_covenant",
            "verified": False,
        }
    if payload.get("release_id") != lock_status.get("release_id"):
        return {
            "schema_version": "visual-covenant.v2",
            "status": "invalid_visual_covenant",
            "verified": False,
        }
    expected_self = covenant_canonical_sha(payload)
    if payload.get("visual_covenant_sha256") != expected_self:
        return {
            "schema_version": "visual-covenant.v2",
            "status": "invalid_visual_covenant",
            "verified": False,
        }
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return {
            "schema_version": "visual-covenant.v2",
            "status": "invalid_visual_covenant",
            "verified": False,
        }
    for item in assets:
        if not isinstance(item, dict):
            return {
                "schema_version": "visual-covenant.v2",
                "status": "invalid_visual_covenant",
                "verified": False,
            }
        for field in ("asset_id", "asset_family", "visual_function"):
            if not isinstance(item.get(field), str) or not item.get(field):
                return {
                    "schema_version": "visual-covenant.v2",
                    "status": "invalid_visual_covenant",
                    "verified": False,
                }
        if item.get("source") != "visual_covenant" or not isinstance(item.get("production_eligible"), bool):
            return {
                "schema_version": "visual-covenant.v2",
                "status": "invalid_visual_covenant",
                "verified": False,
            }
        if item.get("technical_status") != "verified":
            return {
                "schema_version": "visual-covenant.v2",
                "status": "invalid_visual_covenant",
                "verified": False,
            }
        try:
            _verify_asset_file(root, item, "covenant asset")
        except VisualCovenantError as error:
            return {
                "schema_version": "visual-covenant.v2",
                "status": "blocked_by_covenant_integrity",
                "verified": False,
                "reason": str(error),
                "asset_id": item.get("asset_id"),
            }
    return {
        "schema_version": "visual-covenant.v2",
        "status": "visual_covenant_verified",
        "verified": True,
        "release_id": payload.get("release_id"),
        "project_id": payload.get("project_id"),
        "visual_covenant_sha256": payload["visual_covenant_sha256"],
        "asset_count": len(assets),
    }


def load_visual_covenant(project: Path) -> dict[str, Any]:
    """Load and verify the visual covenant, raising on any fail-closed condition."""
    payload = _load_payload(project.expanduser().resolve(), COVENANT_REL)
    if payload is None:
        raise VisualCovenantError("visual covenant is missing")
    status = verify_visual_covenant(project, payload)
    if status.get("status") != "visual_covenant_verified":
        raise VisualCovenantError(f"visual covenant is not verified: {status.get('status')}")
    return payload


def _approval_canonical(approval: dict[str, Any]) -> bytes:
    """Canonical bytes over the approval artifact with its own event hash stripped."""
    normalized = copy.deepcopy(approval)
    normalized.pop("visual_covenant_approval_sha256", None)
    return _canonical(normalized)


def _write_approval_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def record_visual_covenant_approval(
    project: Path,
    *,
    reviewer: str,
    approved_at: str,
) -> dict[str, Any]:
    """Write the single human Visual Covenant approval event (fail-closed).

    The approval binds the currently verified covenant world
    (``visual_covenant_sha256``) to an independent approval event hash
    (``visual_covenant_approval_sha256``). It is the sole human gate; no other
    stage may approve a world/aesthetic boundary.
    """
    root = project.expanduser().resolve()
    if not isinstance(reviewer, str) or not reviewer or reviewer != reviewer.strip():
        raise VisualCovenantError("reviewer must be a nonempty trimmed string")
    if not isinstance(approved_at, str) or not approved_at:
        raise VisualCovenantError("approved_at must be a nonempty string")
    covenant_status = verify_visual_covenant(root)
    if covenant_status.get("status") != "visual_covenant_verified":
        raise VisualCovenantError(
            f"cannot approve an unverified covenant: {covenant_status.get('status')}"
        )
    visual_covenant_sha = covenant_status["visual_covenant_sha256"]
    payload = {
        "schema_version": "visual-covenant-approval.v2",
        "release_id": covenant_status["release_id"],
        "project_id": root.name,
        "approval_id": str(uuid.uuid4()),
        "reviewer": reviewer,
        "approved_at": approved_at,
        "approval_status": "approved",
        "visual_covenant_sha256": visual_covenant_sha,
        "visual_covenant_approval_sha256": "",
    }
    payload["visual_covenant_approval_sha256"] = _sha_bytes(_approval_canonical(payload))
    approval_path = safe_project_output(root, Path(APPROVAL_REL))
    _write_approval_json(approval_path, payload)
    return payload


def verify_visual_covenant_approval(project: Path) -> dict[str, Any]:
    """Fail-closed verification of the independent Visual Covenant approval event."""
    root = project.expanduser().resolve()
    covenant_status = verify_visual_covenant(root)
    if covenant_status.get("status") != "visual_covenant_verified":
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "blocked_by_covenant_integrity",
            "verified": False,
        }
    approval = _load_payload(root, APPROVAL_REL)
    if approval is None:
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "missing_visual_covenant_approval",
            "verified": False,
        }
    if approval.get("schema_version") != "visual-covenant-approval.v2":
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "invalid_visual_covenant_approval",
            "verified": False,
        }
    for field in (
        "release_id",
        "project_id",
        "approval_id",
        "reviewer",
        "approved_at",
        "approval_status",
        "visual_covenant_sha256",
        "visual_covenant_approval_sha256",
    ):
        value = approval.get(field)
        if not isinstance(value, str) or not value:
            return {
                "schema_version": "visual-covenant-approval.v2",
                "status": "invalid_visual_covenant_approval",
                "verified": False,
            }
    if approval["approval_status"] != "approved":
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "visual_covenant_approval_not_approved",
            "verified": False,
        }
    if str(approval.get("project_id", "")) != root.name:
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "invalid_visual_covenant_approval",
            "verified": False,
        }
    if approval.get("release_id") != covenant_status.get("release_id"):
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "stale_visual_covenant_approval",
            "verified": False,
        }
    if approval.get("visual_covenant_sha256") != covenant_status.get("visual_covenant_sha256"):
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "stale_visual_covenant_approval",
            "verified": False,
        }
    expected_event_sha = _sha_bytes(_approval_canonical(approval))
    if approval.get("visual_covenant_approval_sha256") != expected_event_sha:
        return {
            "schema_version": "visual-covenant-approval.v2",
            "status": "invalid_visual_covenant_approval",
            "verified": False,
        }
    return {
        "schema_version": "visual-covenant-approval.v2",
        "status": "visual_covenant_approval_verified",
        "verified": True,
        "release_id": approval["release_id"],
        "visual_covenant_sha256": approval["visual_covenant_sha256"],
        "visual_covenant_approval_sha256": expected_event_sha,
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise VisualCovenantError(f"promotion ledger is corrupt: {path}") from error
        if not isinstance(record, dict):
            raise VisualCovenantError(f"promotion ledger record is not an object: {path}")
        records.append(record)
    return records


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    bytes_value = b"".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for item in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_bytes(bytes_value)
    os.replace(staging, path)


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_without_field = {key: value for key, value in payload.items() if key != "catalog_sha256"}
    payload["catalog_sha256"] = _sha_bytes(_canonical(canonical_without_field))
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_asset_catalog(
    project: Path,
    *,
    release_id: str,
    approval_sha: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write the immutable asset catalog with its self-referential catalog_sha256."""
    root = project.expanduser().resolve()
    payload = {
        "schema_version": "asset-catalog.v2",
        "release_id": release_id,
        "project_id": root.name,
        "covenant_approval_sha256": approval_sha,
        "assets": assets,
    }
    catalog_path = safe_project_output(root, Path(CATALOG_REL))
    _write_immutable_json(catalog_path, payload)
    return payload


def promote_covenant_assets(project: Path) -> dict[str, Any]:
    """Promote only production_eligible covenant assets into the asset catalog."""
    root = project.expanduser().resolve()
    covenant = load_visual_covenant(project)
    status = verify_visual_covenant(project, covenant)
    approval = verify_visual_covenant_approval(root)
    if approval.get("status") != "visual_covenant_approval_verified":
        raise VisualCovenantError(
            "visual covenant approval is required before asset promotion: "
            f"{approval.get('status')}"
        )
    approval_sha = approval["visual_covenant_approval_sha256"]
    release_id = status["release_id"]
    eligible = [
        item
        for item in covenant.get("assets", [])
        if isinstance(item, dict) and item.get("production_eligible") is True
    ]
    catalog_path = safe_project_output(root, Path(CATALOG_REL))
    existing: dict[str, Any] = {}
    try:
        existing_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing_catalog = None
    if isinstance(existing_catalog, dict) and existing_catalog.get("assets"):
        existing = {
            str(item.get("asset_id")): item
            for item in existing_catalog.get("assets", [])
            if isinstance(item, dict)
        }
    events_path = safe_project_output(root, Path(PROMOTION_EVENTS_REL))
    events = _jsonl(events_path)
    event_ids = {str(item.get("asset_id")) for item in events}
    promoted: list[dict[str, Any]] = []
    new_events: list[dict[str, Any]] = []
    for item in eligible:
        asset_id = str(item["asset_id"])
        promoted_at = _utc_now()
        catalog_entry = {
            "asset_id": asset_id,
            "asset_family": item["asset_family"],
            "visual_function": item["visual_function"],
            "origin": "visual_covenant",
            "status": "approved_production_asset",
            "path": item["path"],
            "file_sha256": item["file_sha256"],
            "covenant_approval_sha256": approval_sha,
            "provenance": dict(item.get("provenance") or {}),
            "promoted_at": promoted_at,
        }
        if asset_id not in existing:
            promoted.append(catalog_entry)
        if asset_id not in event_ids:
            new_events.append({
                "schema_version": "asset-promotion-event.v2",
                "event_id": str(uuid.uuid4()),
                "asset_id": asset_id,
                "origin": "visual_covenant",
                "status": "approved_production_asset",
                "file_sha256": item["file_sha256"],
                "covenant_approval_sha256": approval_sha,
                "provider": item.get("provenance", {}).get("provider", ""),
                "tool_call_id": item.get("provenance", {}).get("tool_call_id", ""),
                "promoted_at": promoted_at,
            })
    merged_assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in eligible:
        asset_id = str(item["asset_id"])
        if asset_id in seen:
            continue
        seen.add(asset_id)
        if asset_id in existing:
            entry = dict(existing[asset_id])
            # Promotion preserves the original file hash and provenance but rebinds
            # the promoted asset to the currently verified covenant approval.
            entry["covenant_approval_sha256"] = approval_sha
        else:
            entry = next(e for e in promoted if e["asset_id"] == asset_id)
        merged_assets.append(entry)
    # Preserve production-generated assets already bound to the current approval.
    # Re-promotion must not drop them; they carry their own registered evidence.
    produced = [
        dict(item)
        for item in existing.values()
        if isinstance(item, dict)
        and item.get("origin") == "production_generation"
        and item.get("covenant_approval_sha256") == approval_sha
    ]
    for entry in sorted(produced, key=lambda item: str(item.get("asset_id", ""))):
        asset_id = str(entry.get("asset_id", ""))
        if asset_id in seen:
            continue
        seen.add(asset_id)
        merged_assets.append(entry)
    next_catalog: dict[str, Any] = {
        "schema_version": "asset-catalog.v2",
        "release_id": release_id,
        "project_id": root.name,
        "covenant_approval_sha256": approval_sha,
        "assets": merged_assets,
    }
    _write_immutable_json(catalog_path, next_catalog)
    if new_events:
        _atomic_jsonl(events_path, [*events, *new_events])
    return {
        "status": "asset_catalog_ready",
        "covenant_approval_sha256": approval_sha,
        "promoted_count": len(promoted),
        "emitted_event_count": len(new_events),
        "catalog_asset_count": len(merged_assets),
    }


def verify_asset_catalog(project: Path) -> dict[str, Any]:
    """Fail-closed verification binding the catalog to the current covenant approval."""
    root = project.expanduser().resolve()
    covenant_status = verify_visual_covenant(root)
    if covenant_status.get("status") != "visual_covenant_verified":
        return {
            "schema_version": "asset-catalog.v2",
            "status": "blocked_by_covenant_integrity",
            "verified": False,
        }
    approval = verify_visual_covenant_approval(root)
    if approval.get("status") != "visual_covenant_approval_verified":
        return {
            "schema_version": "asset-catalog.v2",
            "status": "blocked_by_visual_covenant_approval",
            "verified": False,
            "reason": approval.get("status", "missing_visual_covenant_approval"),
        }
    approval_sha = approval["visual_covenant_approval_sha256"]
    catalog_path = safe_project_output(root, Path(CATALOG_REL))
    if catalog_path.is_symlink() or not catalog_path.is_file():
        return {
            "schema_version": "asset-catalog.v2",
            "status": "missing_asset_catalog",
            "verified": False,
        }
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": "asset-catalog.v2",
            "status": "invalid_asset_catalog",
            "verified": False,
        }
    if not isinstance(catalog, dict) or catalog.get("schema_version") != "asset-catalog.v2":
        return {
            "schema_version": "asset-catalog.v2",
            "status": "invalid_asset_catalog",
            "verified": False,
        }
    if catalog.get("covenant_approval_sha256") != approval_sha:
        return {
            "schema_version": "asset-catalog.v2",
            "status": "stale_covenant_approval",
            "verified": False,
        }
    assets = catalog.get("assets")
    if not isinstance(assets, list):
        return {
            "schema_version": "asset-catalog.v2",
            "status": "invalid_asset_catalog",
            "verified": False,
        }
    for item in assets:
        if not isinstance(item, dict):
            return {
                "schema_version": "asset-catalog.v2",
                "status": "invalid_asset_catalog",
                "verified": False,
            }
        if item.get("covenant_approval_sha256") != approval_sha:
            return {
                "schema_version": "asset-catalog.v2",
                "status": "stale_covenant_approval",
                "verified": False,
                "asset_id": item.get("asset_id"),
            }
        try:
            _verify_asset_file(root, item, "catalog asset")
        except VisualCovenantError as error:
            return {
                "schema_version": "asset-catalog.v2",
                "status": "blocked_by_catalog_integrity",
                "verified": False,
                "reason": str(error),
                "asset_id": item.get("asset_id"),
            }
    catalog_path = safe_project_output(root, Path(CATALOG_REL))
    return {
        "schema_version": "asset-catalog.v2",
        "status": "asset_catalog_verified",
        "verified": True,
        "covenant_approval_sha256": approval_sha,
        "catalog_asset_count": len(assets),
        "catalog_sha256": sha256_file(catalog_path),
    }


def load_asset_catalog(project: Path) -> dict[str, Any]:
    """Load the verified asset catalog, raising on any fail-closed condition."""
    status = verify_asset_catalog(project)
    if status.get("status") != "asset_catalog_verified":
        raise VisualCovenantError(f"asset catalog is not verified: {status.get('status')}")
    root = project.expanduser().resolve()
    catalog_path = safe_project_output(root, Path(CATALOG_REL))
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def covenant_production_assets(project: Path) -> list[dict[str, Any]]:
    """Return approved production-ready catalog assets (director reuse_asset readiness)."""
    catalog = load_asset_catalog(project)
    return [
        item
        for item in catalog.get("assets", [])
        if isinstance(item, dict)
        and item.get("status") == "approved_production_asset"
    ]
