"""P0-4: typed Character Registry resolved from an authored register.

Every character contract-visible in any caption must resolve to a registry
entry with identity anchors and an approved identity board. Fail-closed on
unknown ids: a caption naming an unregistered person is a pipeline error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


class CharacterRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class CharacterEntry:
    character_id: str
    name: str
    narrative_role: str
    life_stage: str
    immutable_traits: tuple[str, ...] = ()
    allowed_changes: tuple[str, ...] = ()
    wardrobe_states: tuple[str, ...] = ()
    identity_anchor_task_ids: tuple[str, ...] = ()
    identity_board_task_id: str = ""
    relationships: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id, "name": self.name,
            "narrative_role": self.narrative_role, "life_stage": self.life_stage,
            "immutable_traits": list(self.immutable_traits),
            "allowed_changes": list(self.allowed_changes),
            "wardrobe_states": list(self.wardrobe_states),
            "identity_anchor_task_ids": list(self.identity_anchor_task_ids),
            "identity_board_task_id": self.identity_board_task_id,
            "relationships": list(self.relationships),
        }


class CharacterRegistry:
    def __init__(self, entries: Sequence[CharacterEntry]) -> None:
        self._by_id: dict[str, CharacterEntry] = {}
        for entry in entries:
            if entry.character_id in self._by_id:
                raise CharacterRegistryError(f"duplicate character_id: {entry.character_id}")
            self._by_id[entry.character_id] = entry

    def get(self, character_id: str) -> CharacterEntry:
        entry = self._by_id.get(character_id)
        if entry is None:
            raise CharacterRegistryError(f"unknown character_id: {character_id}")
        return entry

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def to_document(self, *, release_id: str) -> dict[str, Any]:
        return {
            "schema_version": "character-registry.v1",
            "release_id": release_id,
            "character_count": len(self._by_id),
            "characters": [e.to_dict() for e in self._by_id.values()],
        }


def _entry_from_mapping(raw: Mapping[str, Any]) -> CharacterEntry:
    cid = str(raw.get("character_id") or raw.get("id") or "").strip()
    name = str(raw.get("name", "")).strip()
    if not cid or not name:
        raise CharacterRegistryError("character entry requires character_id and name")
    return CharacterEntry(
        character_id=cid,
        name=name,
        narrative_role=str(raw.get("narrative_role", "")),
        life_stage=str(raw.get("life_stage", "")),
        immutable_traits=tuple(str(x) for x in raw.get("immutable_traits") or ()),
        allowed_changes=tuple(str(x) for x in raw.get("allowed_changes") or ()),
        wardrobe_states=tuple(str(x) for x in raw.get("wardrobe_states") or ()),
        identity_anchor_task_ids=tuple(str(x) for x in raw.get("identity_anchor_task_ids") or ()),
        identity_board_task_id=str(raw.get("identity_board_task_id") or ""),
        relationships=tuple(str(x) for x in raw.get("relationships") or ()),
    )


def parse_character_registry(entries: Iterable[Mapping[str, Any]]) -> CharacterRegistry:
    return CharacterRegistry([_entry_from_mapping(e) for e in entries])


def resolve_visible_characters(registry: CharacterRegistry, character_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for cid in character_ids:
        result[cid] = registry.get(cid).to_dict()
    return result


def verify_registry_covers_characters(
    registry: CharacterRegistry,
    visible_ids: Iterable[str],
    *,
    board_task_ids: Mapping[str, str] | None = None,
) -> None:
    board_task_ids = dict(board_task_ids or {})
    for cid in visible_ids:
        entry = registry.get(cid)  # raises on unknown -> fail closed
        if not entry.identity_anchor_task_ids:
            raise CharacterRegistryError(f"{cid} has no identity anchors")
        if board_task_ids.get(cid) != entry.identity_board_task_id:
            raise CharacterRegistryError(f"{cid} identity board is not approved/bound")
