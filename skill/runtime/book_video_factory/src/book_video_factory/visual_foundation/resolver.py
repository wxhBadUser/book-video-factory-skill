"""Reference Resolver: turn a production image task into a concrete, hash-bound reference pack.

Every scene task must resolve to concrete approved image files (path + SHA-256) BEFORE it can
enter a generation wave. This module enforces the deterministic priority and all fail-closed
hard gates:

  priority: identity master(s) -> location anchor -> one book Style Master -> previous scene
  fail closed on: missing/unapproved style master, missing/stale identity anchor, unresolved
  life stage, missing/stale reference file, declared-but-unresolvable location, or an explicit
  previous-scene requirement with no previous asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_foundation.contracts import (
    CharacterIdentity,
    LocationAnchor,
    StyleMaster,
    VisualFoundationError,
)

REFERENCE_PACK_SCHEMA = "reference-pack.v1"
PRIORITY_ORDER = ("identity", "location", "object", "style", "continuity")


@dataclass(frozen=True)
class ReferenceInput:
    role: str
    reference_id: str
    image_path: str
    image_sha256: str
    meta: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "reference_id": self.reference_id,
            "image_path": self.image_path,
            "image_sha256": self.image_sha256,
            "meta": dict(self.meta),
        }


def _asset_for(foundation: Mapping[str, Any], task_id: str, label: str) -> Mapping[str, Any]:
    asset = foundation.get("assets_by_task", {}).get(task_id)
    if asset is None:
        raise VisualFoundationError(f"{label}: registered asset {task_id} is missing")
    path = asset.get("path")
    digest = asset.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise VisualFoundationError(f"{label}: asset {task_id} lacks path or hash")
    return asset


def _verify_current(root: Path, image_path: str, image_sha256: str, label: str) -> None:
    target = safe_project_output(root, Path(image_path))
    if target.is_symlink() or not target.is_file():
        raise VisualFoundationError(f"{label}: reference image is missing or symlinked: {image_path}")
    if sha256_file(target) != image_sha256:
        raise VisualFoundationError(f"{label}: reference image hash is stale: {image_path}")


def _match_style_master(
    masters: Sequence[StyleMaster], task: Mapping[str, Any], contract: Mapping[str, Any] | None
) -> StyleMaster:
    requested = None
    if isinstance(contract, Mapping) and contract.get("style"):
        requested = (contract.get("style") or {}).get("role")
    candidates: list[StyleMaster] = []
    for master in masters:
        if not master.approved:
            continue
        if requested and master.role != requested:
            continue
        if requested:
            candidates.append(master)
            continue
        palette = str(task.get("palette_id", ""))
        lighting = str(task.get("lighting_id", ""))
        event_state = ""
        ves = task.get("visual_event_state")
        if isinstance(ves, Mapping):
            event_state = str(ves.get("action_predicate", ""))
        if master.match_palette_ids and palette and palette not in master.match_palette_ids:
            continue
        if master.match_lighting_ids and lighting and lighting not in master.match_lighting_ids:
            continue
        if master.match_event_states and event_state and event_state not in master.match_event_states:
            continue
        candidates.append(master)
    if not candidates:
        raise VisualFoundationError("no approved Style Master matches the scene task")
    # Prefer the most specific match (most non-wildcard criteria), then manifest order.
    def specificity(master: StyleMaster) -> int:
        return len(master.match_palette_ids) + len(master.match_lighting_ids) + len(master.match_event_states) + len(master.match_time_contexts)
    return max(candidates, key=lambda master: (specificity(master), -masters.index(master)))


def _resolve_life_stage(
    identity: CharacterIdentity, task: Mapping[str, Any], contract: Mapping[str, Any] | None
) -> Any:
    requested = None
    if isinstance(contract, Mapping) and contract.get("identity"):
        for char in (contract.get("identity") or {}).get("characters", []):
            if isinstance(char, Mapping) and char.get("character_id") == identity.character_id:
                requested = str(char.get("life_stage", "")).strip() or None
                break
    if requested:
        matches = [stage for stage in identity.life_stages if stage.life_stage == requested]
        if not matches:
            raise VisualFoundationError(
                f"life stage {identity.character_id}/{requested} is not declared"
            )
        return matches[0]
    chapters = [str(item) for item in task.get("source_beat_ids", [])]
    chapter_ids = [str(item) for item in task.get("chapter_ids", [])]
    event_state = ""
    ves = task.get("visual_event_state")
    if isinstance(ves, Mapping):
        event_state = str(ves.get("action_predicate", ""))
    matched = [
        stage for stage in identity.life_stages
        if (stage.scope_chapters and any(ch in stage.scope_chapters for ch in (*chapters, *chapter_ids)))
        or (stage.scope_event_states and event_state and event_state in stage.scope_event_states)
    ]
    if len(matched) != 1:
        raise VisualFoundationError(
            f"life stage for {identity.character_id} is unresolved (matched {len(matched)} stages); "
            "declare life_stage in the task reference contract"
        )
    return matched[0]


def _validate_participant_agreement(task: Mapping[str, Any], contract: Mapping[str, Any] | None) -> None:
    """S4: when unlisted narrative characters are forbidden, the declared identity
    character set must exactly equal the expected visible character set."""
    participant = task.get("participant_constraint")
    if not isinstance(participant, Mapping) or participant.get("allow_unlisted_narrative_characters") is not False:
        return
    expected = {
        str(item).strip()
        for item in participant.get("expected_visible_character_ids", [])
        if str(item).strip()
    }
    declared: list[str] = []
    if isinstance(contract, Mapping) and contract.get("identity"):
        for item in (contract.get("identity") or {}).get("characters", []):
            if isinstance(item, Mapping) and str(item.get("character_id", "")).strip():
                declared.append(str(item["character_id"]).strip())
    if sorted(expected) != sorted(declared):
        raise VisualFoundationError(
            "participant cardinality mismatch: expected visible characters "
            f"{sorted(expected)} do not match declared identity characters {sorted(declared)}"
        )


def resolve_reference_pack(
    root: Path,
    task: Mapping[str, Any],
    foundation: Mapping[str, Any],
    *,
    previous_asset: Mapping[str, Any] | None = None,
    previous_scene_id: str | None = None,
    previous_task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one task to a concrete reference pack, failing closed on any gap."""
    root = root.expanduser().resolve()
    contract = task.get("reference_contract") if isinstance(task.get("reference_contract"), Mapping) else None
    references: list[ReferenceInput] = []
    used_roles: list[str] = []
    _validate_participant_agreement(task, contract)

    # 1. identity masters (highest priority)
    if isinstance(contract, Mapping) and contract.get("identity"):
        identity_spec = contract.get("identity") or {}
        characters = identity_spec.get("characters", []) if isinstance(identity_spec, Mapping) else []
        if identity_spec.get("required_when_characters_present", True) is False and not characters:
            pass
        else:
            for char in characters if isinstance(characters, list) else []:
                if not isinstance(char, Mapping):
                    continue
                character_id = str(char.get("character_id", "")).strip()
                if not character_id:
                    continue
                identity = next((item for item in foundation["character_identities"] if item.character_id == character_id), None)
                if identity is None:
                    raise VisualFoundationError(f"identity manifest has no entry for {character_id}")
                stage = _resolve_life_stage(identity, task, contract)
                if not stage.approved:
                    raise VisualFoundationError(
                        f"life stage {identity.character_id}/{stage.life_stage} is not approved"
                    )
                if not stage.apparent_age_range:
                    raise VisualFoundationError(
                        f"life stage {identity.character_id}/{stage.life_stage} has no apparent_age_range "
                        "(life-stage lock must reach scene generation)"
                    )
                apparent_age_range = str(char.get("apparent_age_range", "") or stage.apparent_age_range).strip()
                wardrobe_state = str(char.get("wardrobe_state", "") or (stage.wardrobe_states[0] if stage.wardrobe_states else "")).strip()
                narrative_role = str(char.get("narrative_role", "") or identity.narrative_role).strip()
                gender_presentation = str(char.get("gender_presentation", "") or identity.gender_presentation).strip()
                master_asset = _asset_for(foundation, stage.identity_master_task_id, "identity master")
                _verify_current(root, str(master_asset["path"]), str(master_asset["sha256"]), "identity master")
                references.append(ReferenceInput(
                    role="identity_master",
                    reference_id=f"{identity.character_id}:{stage.life_stage}",
                    image_path=str(master_asset["path"]),
                    image_sha256=str(master_asset["sha256"]),
                    meta={
                        "character_id": identity.character_id,
                        "life_stage": stage.life_stage,
                        "apparent_age_range": apparent_age_range,
                        "wardrobe_state": wardrobe_state,
                        "narrative_role": narrative_role,
                        "gender_presentation": gender_presentation,
                    },
                ))
                for support_task in stage.supporting_reference_task_ids:
                    support_asset = _asset_for(foundation, support_task, "identity support")
                    _verify_current(root, str(support_asset["path"]), str(support_asset["sha256"]), "identity support")
                    references.append(ReferenceInput(
                        role="identity_support",
                        reference_id=f"{identity.character_id}:{stage.life_stage}:{support_task}",
                        image_path=str(support_asset["path"]),
                        image_sha256=str(support_asset["sha256"]),
                    ))
    used_roles.append("identity")

    # 2. location reference (persistent vs generic vs none)
    location_id = ""
    location_reference_type = "none"
    if isinstance(contract, Mapping) and contract.get("location"):
        location_spec = contract.get("location") or {}
        location_id = str(location_spec.get("location_id", "")).strip()
        location_reference_type = str(location_spec.get("location_reference_type", "persistent_location_anchor" if location_id else "none")).strip()
        if location_reference_type not in {"persistent_location_anchor", "generic_environment_reference", "none"}:
            raise VisualFoundationError("location_reference_type must be persistent_location_anchor, generic_environment_reference, or none")
        required_persistent = bool(location_spec.get("required_persistent", False))
        if location_id:
            anchor = next((item for item in foundation["location_anchors"] if item.location_id == location_id), None)
            if anchor is None:
                if required_persistent or location_reference_type == "persistent_location_anchor":
                    raise VisualFoundationError(
                        f"persistent location {location_id} is required but no canonical persistent anchor exists"
                    )
                location_reference_type = "generic_environment_reference"
            else:
                if not anchor.approved:
                    raise VisualFoundationError(f"location anchor {location_id} is not approved")
                asset = _asset_for(foundation, anchor.anchor_task_id, "location anchor")
                _verify_current(root, str(asset["path"]), str(asset["sha256"]), "location anchor")
                references.append(ReferenceInput(
                    role="location_anchor",
                    reference_id=location_id,
                    image_path=str(asset["path"]),
                    image_sha256=str(asset["sha256"]),
                    meta={"location_reference_type": "persistent_location_anchor"},
                ))
    used_roles.append("location")

    # 2b. object anchors (story objects that must be in frame)
    for object_task_id in (task.get("object_anchor_task_ids") or []):
        object_task_id = str(object_task_id).strip()
        if not object_task_id:
            continue
        asset = _asset_for(foundation, object_task_id, "object anchor")
        _verify_current(root, str(asset["path"]), str(asset["sha256"]), "object anchor")
        references.append(ReferenceInput(
            role="object_anchor",
            reference_id=object_task_id,
            image_path=str(asset["path"]),
            image_sha256=str(asset["sha256"]),
        ))
    used_roles.append("object")

    # 3. one book Style Master
    if isinstance(contract, Mapping) and contract.get("style") and (contract.get("style") or {}).get("required", True) is False:
        pass  # style explicitly not required
    else:
        master = _match_style_master(foundation["style_masters"], task, contract)
        _verify_current(root, master.image_path, master.image_sha256, "style master")
        references.append(ReferenceInput(
            role="style_master",
            reference_id=master.style_master_id,
            image_path=master.image_path,
            image_sha256=master.image_sha256,
        ))
    used_roles.append("style")

    # 4. previous approved scene (continuity only; optional by default)
    use_previous = False
    previous_reason = ""
    continuity_required = False
    if isinstance(contract, Mapping) and contract.get("continuity"):
        continuity = contract.get("continuity") or {}
        use_previous = bool(continuity.get("use_previous_scene", False))
        previous_reason = str(continuity.get("reason", "")).strip()
        continuity_required = bool(continuity.get("required", False))
    if use_previous:
        if previous_asset is None:
            if continuity_required:
                raise VisualFoundationError("continuity requires a previous approved scene but none exists")
            use_previous = False  # optional continuity: skip when the previous scene is not yet generated
        else:
            if previous_task is not None:
                prev_event = ""
                prev_state = previous_task.get("visual_event_state")
                if isinstance(prev_state, Mapping):
                    prev_event = str(prev_state.get("action_predicate", ""))
                this_event = ""
                this_state = task.get("visual_event_state")
                if isinstance(this_state, Mapping):
                    this_event = str(this_state.get("action_predicate", ""))
                if prev_event and this_event and prev_event != this_event:
                    raise VisualFoundationError(
                        f"unrelated previous-frame inheritance: scene event {this_event!r} "
                        f"differs from previous scene event {prev_event!r}"
                    )
            _verify_current(root, str(previous_asset["path"]), str(previous_asset["sha256"]), "previous scene")
            references.append(ReferenceInput(
                role="previous_scene",
                reference_id=previous_scene_id or str(previous_asset.get("task_id", "")),
                image_path=str(previous_asset["path"]),
                image_sha256=str(previous_asset["sha256"]),
            ))
    used_roles.append("continuity")

    return {
        "schema_version": REFERENCE_PACK_SCHEMA,
        "task_id": str(task.get("task_id", "")),
        "priority_order": list(PRIORITY_ORDER),
        "references": [item.to_dict() for item in references],
        "location": {"location_id": location_id, "location_reference_type": location_reference_type},
        "continuity": {"use_previous_scene": use_previous, "reason": previous_reason},
        "resolved_from": {
            "visual_profile_sha256": str(foundation.get("visual_profile_sha256", "")),
            "style_master_manifest_sha256": str(foundation.get("style_master_manifest_sha256", "")),
            "character_identity_manifest_sha256": str(foundation.get("character_identity_manifest_sha256", "")),
            "location_anchor_manifest_sha256": str(foundation.get("location_anchor_manifest_sha256", "")),
            "asset_manifest_sha256": str(foundation.get("asset_manifest_sha256", "")),
        },
    }

