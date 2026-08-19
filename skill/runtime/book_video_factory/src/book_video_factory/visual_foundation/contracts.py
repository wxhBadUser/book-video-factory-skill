"""Visual Foundation contracts: Style Masters, Character Identity Pack, Location Anchors,
and the production reference contract that binds every scene task to concrete approved images."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

VALID_LIFE_STAGES = ("young", "middle_age", "old", "child", "adult", "elder")


class VisualFoundationError(ValueError):
    """Visual foundation data is invalid or cannot be bound."""


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise VisualFoundationError(f"{label} must be a nonempty trimmed string")
    if _ID.fullmatch(value) is None:
        raise VisualFoundationError(f"{label} must be a safe uppercase identifier")
    return value


def _slug(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise VisualFoundationError(f"{label} must be a nonempty trimmed string")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise VisualFoundationError(f"{label} must be a safe slug identifier")
    return value


def _plain_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise VisualFoundationError(f"{label} must be a nonempty trimmed string")
    return value


def _str_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise VisualFoundationError(f"{label} must be an array")
    if nonempty and not value:
        raise VisualFoundationError(f"{label} must be nonempty")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise VisualFoundationError(f"{label}[{index}] must be a trimmed string")
        if item in result:
            raise VisualFoundationError(f"{label} contains duplicate {item!r}")
        result.append(item)
    return result


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise VisualFoundationError(f"{label} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class StyleMaster:
    style_master_id: str
    book_id: str
    role: str
    image_path: str
    image_sha256: str
    source_lookdev_task_id: str
    visual_profile_sha256: str
    approved: bool
    approval_event_sha256: str
    style_guidance: str
    match_palette_ids: tuple[str, ...] = ()
    match_lighting_ids: tuple[str, ...] = ()
    match_event_states: tuple[str, ...] = ()
    match_time_contexts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_master_id": self.style_master_id,
            "book_id": self.book_id,
            "role": self.role,
            "image_path": self.image_path,
            "image_sha256": self.image_sha256,
            "source_lookdev_task_id": self.source_lookdev_task_id,
            "visual_profile_sha256": self.visual_profile_sha256,
            "approved": self.approved,
            "approval_event_sha256": self.approval_event_sha256,
            "style_guidance": self.style_guidance,
            "match": {
                "palette_ids": list(self.match_palette_ids),
                "lighting_ids": list(self.match_lighting_ids),
                "event_states": list(self.match_event_states),
                "time_contexts": list(self.match_time_contexts),
            },
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "StyleMaster":
        _text(raw.get("style_master_id"), "style_master_id")
        _slug(raw.get("book_id"), "book_id")
        _plain_text(raw.get("role"), "role")
        path = raw.get("image_path")
        if not isinstance(path, str) or not path or path != path.strip() or Path(path).is_absolute():
            raise VisualFoundationError("style master image_path must be a relative trimmed path")
        _sha256(raw.get("image_sha256"), "image_sha256")
        _text(raw.get("source_lookdev_task_id"), "source_lookdev_task_id")
        _sha256(raw.get("visual_profile_sha256"), "visual_profile_sha256")
        approved = raw.get("approved")
        if not isinstance(approved, bool):
            raise VisualFoundationError("style master approved must be a boolean")
        approval = raw.get("approval_event_sha256")
        if not isinstance(approval, str):
            raise VisualFoundationError("style master approval_event_sha256 must be a string")
        if approval and _SHA.fullmatch(approval) is None:
            raise VisualFoundationError("style master approval_event_sha256 must be a SHA-256")
        match = raw.get("match") if isinstance(raw.get("match"), Mapping) else {}
        return cls(
            style_master_id=str(raw["style_master_id"]),
            book_id=str(raw["book_id"]),
            role=str(raw["role"]),
            image_path=path,
            image_sha256=str(raw["image_sha256"]),
            source_lookdev_task_id=str(raw["source_lookdev_task_id"]),
            visual_profile_sha256=str(raw["visual_profile_sha256"]),
            approved=approved,
            approval_event_sha256=approval,
            style_guidance=_plain_text(raw.get("style_guidance", ""), "style_guidance"),
            match_palette_ids=tuple(_str_list(match.get("palette_ids", []), "match.palette_ids", nonempty=False)),
            match_lighting_ids=tuple(_str_list(match.get("lighting_ids", []), "match.lighting_ids", nonempty=False)),
            match_event_states=tuple(_str_list(match.get("event_states", []), "match.event_states", nonempty=False)),
            match_time_contexts=tuple(_str_list(match.get("time_contexts", []), "match.time_contexts", nonempty=False)),
        )


@dataclass(frozen=True)
class DerivedView:
    """A supporting identity view derived from the identity root via real edit/img2img.

    Role hygiene: derived views belong to the SAME character/life_stage and must carry
    the source root SHA, style source SHA, generation attempt id and output SHA so the
    provenance chain (root -> 3Q -> full -> wardrobe) is auditable.
    """

    view: str
    task_id: str
    source_root_sha256: str = ""
    style_source_sha256: str = ""
    generation_attempt_id: str = ""
    output_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": self.view,
            "task_id": self.task_id,
            "source_root_sha256": self.source_root_sha256,
            "style_source_sha256": self.style_source_sha256,
            "generation_attempt_id": self.generation_attempt_id,
            "output_sha256": self.output_sha256,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DerivedView":
        view = str(raw.get("view", "")).strip()
        task_id = str(raw.get("task_id", "")).strip()
        if not view or not task_id:
            raise VisualFoundationError("derived view requires view and task_id")
        sha = lambda key: str(raw.get(key, "") or "")
        return cls(view=view, task_id=task_id, source_root_sha256=sha("source_root_sha256"),
                   style_source_sha256=sha("style_source_sha256"),
                   generation_attempt_id=sha("generation_attempt_id"), output_sha256=sha("output_sha256"))


@dataclass(frozen=True)
class LifeStageIdentity:
    life_stage: str
    identity_master_task_id: str
    supporting_reference_task_ids: tuple[str, ...]
    identity_invariants: tuple[str, ...]
    allowed_changes: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    wardrobe_states: tuple[str, ...]
    scope_chapters: tuple[str, ...] = ()
    scope_event_states: tuple[str, ...] = ()
    approved: bool = False
    identity_root_task_id: str = ""
    apparent_age_range: str = ""
    derived_views: tuple[DerivedView, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "life_stage": self.life_stage,
            "identity_master_task_id": self.identity_master_task_id,
            "supporting_reference_task_ids": list(self.supporting_reference_task_ids),
            "identity_invariants": list(self.identity_invariants),
            "allowed_changes": list(self.allowed_changes),
            "forbidden_changes": list(self.forbidden_changes),
            "wardrobe_states": list(self.wardrobe_states),
            "scope": {"chapters": list(self.scope_chapters), "event_states": list(self.scope_event_states)},
            "approved": self.approved,
            "identity_root_task_id": self.identity_root_task_id,
            "apparent_age_range": self.apparent_age_range,
            "derived_views": [item.to_dict() for item in self.derived_views],
        }


@dataclass(frozen=True)
class CharacterIdentity:
    character_id: str
    name: str
    same_person_lineage: bool
    life_stages: tuple[LifeStageIdentity, ...]
    narrative_role: str = ""
    gender_presentation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "same_person_lineage": self.same_person_lineage,
            "life_stages": [item.to_dict() for item in self.life_stages],
            "narrative_role": self.narrative_role,
            "gender_presentation": self.gender_presentation,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CharacterIdentity":
        _text(raw.get("character_id"), "character_id")
        _plain_text(raw.get("name"), "name")
        lineage = raw.get("same_person_lineage")
        if not isinstance(lineage, bool):
            raise VisualFoundationError("same_person_lineage must be a boolean")
        narrative_role = str(raw.get("narrative_role", "") or "").strip()
        gender_presentation = str(raw.get("gender_presentation", "") or "").strip()
        stages = raw.get("life_stages")
        if not isinstance(stages, list) or not stages:
            raise VisualFoundationError("life_stages must be nonempty")
        parsed: list[LifeStageIdentity] = []
        seen: set[str] = set()
        for index, item in enumerate(stages):
            if not isinstance(item, Mapping):
                raise VisualFoundationError(f"life_stages[{index}] must be an object")
            stage = str(item.get("life_stage", "")).strip()
            if stage not in VALID_LIFE_STAGES or stage in seen:
                raise VisualFoundationError(f"life_stages[{index}].life_stage is invalid or duplicate")
            seen.add(stage)
            _text(item.get("identity_master_task_id"), f"life_stages[{index}].identity_master_task_id")
            supporting = tuple(_str_list(item.get("supporting_reference_task_ids", []), "supporting_reference_task_ids", nonempty=False))
            if not supporting and not item.get("identity_master_task_id"):
                raise VisualFoundationError("life stage requires at least one identity reference")
            scope_ch = tuple(_str_list(item.get("scope", {}).get("chapters", []) if isinstance(item.get("scope"), Mapping) else [], "scope.chapters", nonempty=False))
            scope_ev = tuple(_str_list(item.get("scope", {}).get("event_states", []) if isinstance(item.get("scope"), Mapping) else [], "scope.event_states", nonempty=False))
            if not scope_ch and not scope_ev:
                raise VisualFoundationError("life stage requires a scope (chapters or event states)")
            parsed.append(LifeStageIdentity(
                life_stage=stage,
                identity_master_task_id=str(item["identity_master_task_id"]),
                supporting_reference_task_ids=supporting,
                identity_invariants=tuple(_str_list(item.get("identity_invariants", []), "identity_invariants")),
                allowed_changes=tuple(_str_list(item.get("allowed_changes", []), "allowed_changes")),
                forbidden_changes=tuple(_str_list(item.get("forbidden_changes", []), "forbidden_changes")),
                wardrobe_states=tuple(_str_list(item.get("wardrobe_states", []), "wardrobe_states")),
                scope_chapters=scope_ch,
                scope_event_states=scope_ev,
                approved=bool(item.get("approved", True)),
                identity_root_task_id=str(item.get("identity_root_task_id", "") or "").strip(),
                apparent_age_range=_plain_text(item.get("apparent_age_range", ""), "apparent_age_range"),
                derived_views=tuple(
                    DerivedView.from_mapping(entry) for entry in item.get("derived_views", [])
                    if isinstance(entry, Mapping)
                ),
            ))
        return cls(str(raw["character_id"]), str(raw["name"]), lineage, tuple(parsed),
                   narrative_role, gender_presentation)


@dataclass(frozen=True)
class LocationAnchor:
    location_id: str
    name: str
    anchor_task_id: str
    image_path: str
    image_sha256: str
    approved: bool
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "name": self.name,
            "anchor_task_id": self.anchor_task_id,
            "image_path": self.image_path,
            "image_sha256": self.image_sha256,
            "approved": self.approved,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LocationAnchor":
        _text(raw.get("location_id"), "location_id")
        _plain_text(raw.get("name"), "name")
        _text(raw.get("anchor_task_id"), "anchor_task_id")
        path = raw.get("image_path")
        if not isinstance(path, str) or not path or path != path.strip() or Path(path).is_absolute():
            raise VisualFoundationError("location anchor image_path must be a relative trimmed path")
        _sha256(raw.get("image_sha256"), "image_sha256")
        approved = raw.get("approved")
        if not isinstance(approved, bool):
            raise VisualFoundationError("location anchor approved must be a boolean")
        return cls(
            str(raw["location_id"]), str(raw["name"]), str(raw["anchor_task_id"]), path,
            str(raw["image_sha256"]), approved,
            tuple(_str_list(raw.get("aliases", []), "aliases", nonempty=False)),
        )


def _parse_manifest(payload: Mapping[str, Any], kind: str) -> tuple[list[Any], str]:
    if payload.get("schema_version") != kind:
        raise VisualFoundationError(f"unsupported {kind} schema")
    release_id = payload.get("release_id")
    if not isinstance(release_id, str) or not release_id.strip() or release_id != release_id.strip():
        raise VisualFoundationError("release_id must be a nonempty trimmed string")
    return payload.get("items", []), str(release_id)


def parse_style_masters(payload: Mapping[str, Any]) -> tuple[list[StyleMaster], str]:
    items, release_id = _parse_manifest(payload, "style-master-manifest.v1")
    if not isinstance(items, list) or not items:
        raise VisualFoundationError("style master manifest must contain masters")
    seen: set[str] = set()
    parsed: list[StyleMaster] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise VisualFoundationError(f"masters[{index}] must be an object")
        master = StyleMaster.from_mapping(item)
        if master.style_master_id in seen:
            raise VisualFoundationError(f"duplicate style master {master.style_master_id}")
        seen.add(master.style_master_id)
        parsed.append(master)
    return parsed, release_id


def parse_character_identities(payload: Mapping[str, Any]) -> tuple[list[CharacterIdentity], str]:
    items, release_id = _parse_manifest(payload, "character-identity-manifest.v1")
    if not isinstance(items, list) or not items:
        raise VisualFoundationError("character identity manifest must contain characters")
    seen: set[str] = set()
    parsed: list[CharacterIdentity] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise VisualFoundationError(f"items[{index}] must be an object")
        identity = CharacterIdentity.from_mapping(item)
        if identity.character_id in seen:
            raise VisualFoundationError(f"duplicate character {identity.character_id}")
        seen.add(identity.character_id)
        parsed.append(identity)
    return parsed, release_id


def parse_location_anchors(payload: Mapping[str, Any]) -> tuple[list[LocationAnchor], str]:
    items, release_id = _parse_manifest(payload, "location-anchor-manifest.v1")
    parsed: list[LocationAnchor] = []
    seen: set[str] = set()
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, Mapping):
            raise VisualFoundationError(f"items[{index}] must be an object")
        anchor = LocationAnchor.from_mapping(item)
        if anchor.location_id in seen:
            raise VisualFoundationError(f"duplicate location {anchor.location_id}")
        seen.add(anchor.location_id)
        parsed.append(anchor)
    return parsed, release_id
