"""Build, load, and verify the Visual Foundation manifests (Style Master / Character Identity / Location Anchor).

The three manifests are immutable, hash-bound artifacts under 03_images_生成图片/ that must be
human-approved (gate `visual_foundation`) before any Scene Image Generation wave is planned.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_foundation.contracts import (
    CharacterIdentity,
    LocationAnchor,
    StyleMaster,
    VisualFoundationError,
    parse_character_identities,
    parse_location_anchors,
    parse_style_masters,
)

FOUNDATION_DIR = "03_images_生成图片"
STYLE_MASTER_REL = f"{FOUNDATION_DIR}/STYLE_MASTER_MANIFEST.json"
CHARACTER_IDENTITY_REL = f"{FOUNDATION_DIR}/CHARACTER_IDENTITY_MANIFEST.json"
LOCATION_ANCHOR_REL = f"{FOUNDATION_DIR}/LOCATION_ANCHOR_MANIFEST.json"
FOUNDATION_MANIFEST_REL = f"{FOUNDATION_DIR}/VISUAL_FOUNDATION_MANIFEST.json"
FOUNDATION_APPROVAL_REL = f"{FOUNDATION_DIR}/VISUAL_FOUNDATION_APPROVAL.json"
FOUNDATION_APPROVAL_GATE = "visual_foundation"

MANIFEST_KINDS = ("style_master", "character_identity", "location_anchor")
KIND_TO_REL = {
    "style_master": STYLE_MASTER_REL,
    "character_identity": CHARACTER_IDENTITY_REL,
    "location_anchor": LOCATION_ANCHOR_REL,
}


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def style_master_canonical_sha(payload: dict[str, Any]) -> str:
    """Stable SHA over a style master manifest with the approval_event_sha256 field
    normalized to the empty string, so binding a manifest revision is not invalidated
    when approve_visual_foundation fills in the real event SHA."""
    import copy
    normalized = copy.deepcopy(payload)
    items = normalized.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item["approval_event_sha256"] = ""
    return _sha_bytes(_canonical(normalized))
    return hashlib.sha256(value).hexdigest()


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


def _verify_visual_profile_sha(root: Path, profile_sha: str) -> None:
    profile_path = safe_project_output(root, Path(f"{FOUNDATION_DIR}/BOOK_VISUAL_PROFILE.json"))
    if profile_path.is_symlink() or not profile_path.is_file():
        raise VisualFoundationError("BOOK_VISUAL_PROFILE.json is missing")
    if sha256_file(profile_path) != profile_sha:
        raise VisualFoundationError("style master visual_profile_sha256 does not match BOOK_VISUAL_PROFILE.json")


def _asset_kind(root: Path, task_id: str) -> str | None:
    """Reference role hygiene: the production role of an asset is fixed by its Phase 3
    task kind (character_anchor / scene_anchor / object_anchor / lookdev)."""
    assets = _visual_asset_manifest(root)
    for item in assets.get("assets", []):
        if isinstance(item, Mapping) and item.get("task_id") == task_id:
            return str(item.get("task_kind", "")) or None
    return None


def _asset_character_id(task_id: str) -> str | None:
    """Parse ANCHOR_<CID>_<VIEW> -> CID; None when not a character anchor task id."""
    prefix = "ANCHOR_"
    if not task_id.startswith(prefix):
        return None
    rest = task_id[len(prefix):]
    parts = rest.split("_")
    if len(parts) < 2:
        return None
    candidate = parts[0]
    return candidate if candidate.startswith("C") else None


def _character_anchor_ids(root: Path) -> set[str]:
    profile = _load_json(root, f"{FOUNDATION_DIR}/BOOK_VISUAL_PROFILE.json", "visual profile")
    result: set[str] = set()
    for anchor in profile.get("character_anchors", []):
        if isinstance(anchor, Mapping) and anchor.get("anchor_type") == "character_identity":
            anchor_id = anchor.get("anchor_id")
            if isinstance(anchor_id, str) and anchor_id:
                result.add(anchor_id)
    return result


def _verify_asset_kind(root: Path, task_id: str, expected: str, label: str) -> None:
    kind = _asset_kind(root, task_id)
    if kind is None:
        raise VisualFoundationError(f"{label}: asset {task_id} is not registered in the Phase 3 manifest")
    if kind != expected:
        raise VisualFoundationError(
            f"{label}: role hygiene violated - {task_id} is a {kind} asset but the reference role requires {expected}"
        )


def _verify_asset_character(root: Path, task_id: str, character_id: str, label: str) -> None:
    _verify_asset_kind(root, task_id, "character_anchor", label)
    cid = _asset_character_id(task_id)
    if cid != character_id:
        raise VisualFoundationError(
            f"{label}: identity role hygiene violated - {task_id} belongs to {cid!r} not {character_id!r}"
        )


def _verify_asset_current(root: Path, image_path: str, image_sha256: str, label: str) -> None:
    target = safe_project_output(root, Path(image_path))
    if target.is_symlink() or not target.is_file():
        raise VisualFoundationError(f"{label} reference image is missing or symlinked: {image_path}")
    if sha256_file(target) != image_sha256:
        raise VisualFoundationError(f"{label} reference image hash is stale: {image_path}")


def verify_style_master_manifest(root: Path, release_id: str) -> dict[str, Any]:
    payload = _load_json(root, STYLE_MASTER_REL, "style master manifest")
    masters, manifest_release = parse_style_masters(payload)
    if manifest_release != release_id:
        raise VisualFoundationError("style master manifest release does not match")
    if not payload.get("visual_profile_sha256"):
        raise VisualFoundationError("style master manifest requires visual_profile_sha256")
    _verify_visual_profile_sha(root, str(payload["visual_profile_sha256"]))
    character_anchor_ids = _character_anchor_ids(root)
    lookdev_path = safe_project_output(root, Path(f"{FOUNDATION_DIR}/LOOKDEV_TASKS.jsonl"))
    lookdev_anchor_refs: dict[str, list[str]] = {}
    if lookdev_path.is_file() and not lookdev_path.is_symlink():
        try:
            for line in lookdev_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if isinstance(record, dict) and isinstance(record.get("task_id"), str):
                    refs = record.get("anchor_refs")
                    lookdev_anchor_refs[record["task_id"]] = [str(x) for x in refs] if isinstance(refs, list) else []
        except (OSError, json.JSONDecodeError) as error:
            raise VisualFoundationError(f"LOOKDEV_TASKS.jsonl is unreadable: {error}") from error
    for master in masters:
        if not master.approved:
            raise VisualFoundationError(f"style master {master.style_master_id} is not approved")
        _verify_asset_current(root, master.image_path, master.image_sha256, "style master")
        _verify_asset_kind(root, master.source_lookdev_task_id, "lookdev", "style master")
        refs = lookdev_anchor_refs.get(master.source_lookdev_task_id, [])
        if set(refs) & character_anchor_ids:
            raise VisualFoundationError(
                f"style master {master.style_master_id} is not character-neutral: its source lookdev "
                f"{master.source_lookdev_task_id} references recurring characters {sorted(set(refs) & character_anchor_ids)}"
            )
    return {"path": STYLE_MASTER_REL, "sha256": style_master_canonical_sha(payload)}


def verify_character_identity_manifest(root: Path, release_id: str, assets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    payload = _load_json(root, CHARACTER_IDENTITY_REL, "character identity manifest")
    identities, manifest_release = parse_character_identities(payload)
    if manifest_release != release_id:
        raise VisualFoundationError("character identity manifest release does not match")
    by_task = {str(asset.get("task_id")): asset for asset in assets.get("assets", []) if isinstance(asset, Mapping)}
    for identity in identities:
        for stage in identity.life_stages:
            if not stage.approved:
                raise VisualFoundationError(f"life stage {identity.character_id}/{stage.life_stage} is not approved")
            root_task = stage.identity_root_task_id or stage.identity_master_task_id
            if root_task not in by_task:
                raise VisualFoundationError(f"identity root task {root_task} is not registered")
            if not root_task.endswith("_FRONT"):
                raise VisualFoundationError(
                    f"life stage {identity.character_id}/{stage.life_stage} identity root must be a FRONT view (life stage precedes view)"
                )
            for task_id in (root_task, *stage.supporting_reference_task_ids):
                asset = by_task.get(task_id)
                if asset is None:
                    raise VisualFoundationError(
                        f"identity reference task {task_id} is not a registered Phase 3 asset"
                    )
                _verify_asset_current(root, str(asset["path"]), str(asset["sha256"]), "identity")
                _verify_asset_character(root, task_id, identity.character_id, "identity")
            root_sha = str(by_task[root_task].get("sha256", ""))
            for derived in stage.derived_views:
                if derived.task_id not in by_task:
                    raise VisualFoundationError(f"derived view task {derived.task_id} is not registered")
                _verify_asset_character(root, derived.task_id, identity.character_id, "derived view")
                if derived.source_root_sha256 and derived.source_root_sha256 != root_sha:
                    raise VisualFoundationError(
                        f"derived view {derived.task_id} source root SHA does not match identity root {root_task}"
                    )
    return {"path": CHARACTER_IDENTITY_REL, "sha256": sha256_file(safe_project_output(root, Path(CHARACTER_IDENTITY_REL)))}


def verify_location_anchor_manifest(root: Path, release_id: str, assets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    payload = _load_json(root, LOCATION_ANCHOR_REL, "location anchor manifest")
    anchors, manifest_release = parse_location_anchors(payload)
    if manifest_release != release_id:
        raise VisualFoundationError("location anchor manifest release does not match")
    by_task = {str(asset.get("task_id")): asset for asset in assets.get("assets", []) if isinstance(asset, Mapping)}
    for anchor in anchors:
        if not anchor.approved:
            raise VisualFoundationError(f"location anchor {anchor.location_id} is not approved")
        asset = by_task.get(anchor.anchor_task_id)
        if asset is None:
            raise VisualFoundationError(f"location anchor task {anchor.anchor_task_id} is not registered")
        _verify_asset_current(root, str(asset["path"]), str(asset["sha256"]), "location")
        _verify_asset_kind(root, anchor.anchor_task_id, "scene_anchor", "location anchor")
    return {"path": LOCATION_ANCHOR_REL, "sha256": sha256_file(safe_project_output(root, Path(LOCATION_ANCHOR_REL)))}


def _visual_asset_manifest(root: Path) -> dict[str, Any]:
    return _load_json(root, f"{FOUNDATION_DIR}/VISUAL_ASSET_MANIFEST.json", "visual asset manifest")


def verify_visual_foundation(root: Path, release_id: str) -> dict[str, Any]:
    """Fail-closed verification of the three foundation manifests against approved assets + profile."""
    root = root.expanduser().resolve()
    assets = _visual_asset_manifest(root)
    style = verify_style_master_manifest(root, release_id)
    identity = verify_character_identity_manifest(root, release_id, assets)
    location = verify_location_anchor_manifest(root, release_id, assets)
    foundation_path = safe_project_output(root, Path(FOUNDATION_MANIFEST_REL))
    if foundation_path.is_symlink() or not foundation_path.is_file():
        raise VisualFoundationError("VISUAL_FOUNDATION_MANIFEST.json is missing")
    foundation = _load_json(root, FOUNDATION_MANIFEST_REL, "visual foundation manifest")
    expected = {kind: {"path": KIND_TO_REL[kind], "sha256": entry["sha256"]} for kind, entry in {
        "style_master": style, "character_identity": identity, "location_anchor": location,
    }.items()}
    if foundation.get("release_id") != release_id:
        raise VisualFoundationError("visual foundation manifest release mismatch")
    if foundation.get("manifests") != expected:
        raise VisualFoundationError("visual foundation manifest does not bind the current manifests")
    expected_self = _sha_bytes(_canonical({key: value for key, value in foundation.items() if key != "visual_foundation_sha256"}))
    if foundation.get("visual_foundation_sha256") != expected_self:
        raise VisualFoundationError("visual foundation manifest hash field is stale")
    return {
        "release_id": release_id,
        "manifests": expected,
        "foundation_manifest_sha256": sha256_file(foundation_path),
    }


def build_visual_foundation(project: Path, foundation_input: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the three foundation manifests from an agent-authored VISUAL_FOUNDATION_INPUT.

    Returns the foundation manifest payload. Publishing is transactional and refuses to
    overwrite existing foundation artifacts (use the approval flow to record changes).
    """
    root = project.expanduser().resolve()
    release_id = foundation_input.get("release_id")
    if not isinstance(release_id, str) or not release_id.strip():
        raise VisualFoundationError("foundation input requires release_id")
    profile_path = safe_project_output(root, Path(f"{FOUNDATION_DIR}/BOOK_VISUAL_PROFILE.json"))
    if profile_path.is_symlink() or not profile_path.is_file():
        raise VisualFoundationError("BOOK_VISUAL_PROFILE.json is missing; build Phase 3 first")
    profile_sha = sha256_file(profile_path)

    masters_raw = foundation_input.get("style_masters")
    identities_raw = foundation_input.get("character_identities")
    locations_raw = foundation_input.get("location_anchors", [])
    if not isinstance(masters_raw, list) or not masters_raw:
        raise VisualFoundationError("foundation input requires style_masters")
    if not isinstance(identities_raw, list) or not identities_raw:
        raise VisualFoundationError("foundation input requires character_identities")
    if not isinstance(locations_raw, list):
        raise VisualFoundationError("foundation input location_anchors must be an array")

    style_payload = {
        "schema_version": "style-master-manifest.v1",
        "release_id": release_id,
        "book_id": str(foundation_input.get("book_id", "")),
        "visual_profile_sha256": profile_sha,
        "items": masters_raw,
    }
    identity_payload = {
        "schema_version": "character-identity-manifest.v1",
        "release_id": release_id,
        "items": identities_raw,
    }
    location_payload = {
        "schema_version": "location-anchor-manifest.v1",
        "release_id": release_id,
        "items": locations_raw,
    }
    # Validate before any publish.
    masters, _ = parse_style_masters(style_payload)
    identities, _ = parse_character_identities(identity_payload)
    anchors, _ = parse_location_anchors(location_payload)
    for master in masters:
        _verify_asset_current(root, master.image_path, master.image_sha256, "style master")
    # Identity/location reference assets are validated against the Phase 3 asset manifest
    # at approval/verify time (verify_visual_foundation), which re-hashes every file.

    payloads = {
        STYLE_MASTER_REL: _pretty(style_payload),
        CHARACTER_IDENTITY_REL: _pretty(identity_payload),
        LOCATION_ANCHOR_REL: _pretty(location_payload),
    }
    for relative in payloads:
        target = safe_project_output(root, Path(relative))
        if target.exists():
            raise VisualFoundationError(f"refusing to overwrite existing foundation artifact: {relative}")
    foundation_manifest = {
        "schema_version": "visual-foundation-manifest.v1",
        "release_id": release_id,
        "visual_profile_sha256": profile_sha,
        "manifests": {
            "style_master": {"path": STYLE_MASTER_REL, "sha256": style_master_canonical_sha(style_payload)},
            "character_identity": {"path": CHARACTER_IDENTITY_REL, "sha256": _sha_bytes(payloads[CHARACTER_IDENTITY_REL])},
            "location_anchor": {"path": LOCATION_ANCHOR_REL, "sha256": _sha_bytes(payloads[LOCATION_ANCHOR_REL])},
        },
    }
    canonical_without_field = {key: value for key, value in foundation_manifest.items() if key != "visual_foundation_sha256"}
    foundation_manifest["visual_foundation_sha256"] = _sha_bytes(_canonical(canonical_without_field))
    payloads[FOUNDATION_MANIFEST_REL] = _pretty(foundation_manifest)
    for relative, content in payloads.items():
        target = safe_project_output(root, Path(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return foundation_manifest


def load_foundation(root: Path, release_id: str) -> dict[str, Any]:
    """Load and verify the three foundation manifests (without the approval gate).

    Returns parsed objects plus binding hashes, or raises if any manifest is stale.
    """
    root = root.expanduser().resolve()
    assets = _visual_asset_manifest(root)
    style_entry = verify_style_master_manifest(root, release_id)
    identity_entry = verify_character_identity_manifest(root, release_id, assets)
    location_entry = verify_location_anchor_manifest(root, release_id, assets)
    style_payload = _load_json(root, STYLE_MASTER_REL, "style master manifest")
    identity_payload = _load_json(root, CHARACTER_IDENTITY_REL, "character identity manifest")
    location_payload = _load_json(root, LOCATION_ANCHOR_REL, "location anchor manifest")
    masters, _ = parse_style_masters(style_payload)
    identities, _ = parse_character_identities(identity_payload)
    locations, _ = parse_location_anchors(location_payload)
    by_task = {str(asset.get("task_id")): asset for asset in assets.get("assets", []) if isinstance(asset, Mapping)}
    return {
        "style_masters": masters,
        "character_identities": identities,
        "location_anchors": locations,
        "assets_by_task": by_task,
        "visual_profile_sha256": str(style_payload.get("visual_profile_sha256", "")),
        "style_master_manifest_sha256": style_entry["sha256"],
        "character_identity_manifest_sha256": identity_entry["sha256"],
        "location_anchor_manifest_sha256": location_entry["sha256"],
        "asset_manifest_sha256": sha256_file(safe_project_output(root, Path(f"{FOUNDATION_DIR}/VISUAL_ASSET_MANIFEST.json"))),
    }
