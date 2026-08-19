from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .script_export import ScriptExports


class HbgStoryboardExportError(ValueError):
    """Semantic storyboard cannot be safely exported to HBG."""


_HIGH_RISK = {
    "hands", "phone", "tool_use", "water_action", "animal_contact",
    "body_contact", "reflection", "death_climax", "hero_shot",
}


def export_storyboard_base(
    bridge_input: Mapping[str, Any], exports: ScriptExports
) -> list[dict[str, Any]]:
    characters = bridge_input.get("characters")
    beats = bridge_input.get("storyboard_beats")
    chapters = bridge_input.get("chapters")
    if not isinstance(characters, list) or not characters:
        raise HbgStoryboardExportError("characters are required")
    if not isinstance(beats, list) or not beats:
        raise HbgStoryboardExportError("agent-authored storyboard beats are required")
    if not isinstance(chapters, list) or not chapters:
        raise HbgStoryboardExportError("chapters are required")
    character_ids = {item.get("character_id") for item in characters if isinstance(item, Mapping)}
    chapter_numbers = {item.get("chapter_id"): index for index, item in enumerate(chapters, start=1) if isinstance(item, Mapping)}
    release_text = exports.source_bytes.decode("utf-8")
    cursor = 0
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, Mapping):
            raise HbgStoryboardExportError(f"beat {index} must be an object")
        beat_id = beat.get("beat_id")
        if not isinstance(beat_id, str) or not beat_id or beat_id in seen:
            raise HbgStoryboardExportError("beat IDs must be nonempty and unique")
        seen.add(beat_id)
        chapter_id = beat.get("chapter_id")
        if chapter_id not in chapter_numbers:
            raise HbgStoryboardExportError(f"unknown chapter: {chapter_id}")
        cue = beat.get("cue")
        if not isinstance(cue, str) or not cue:
            raise HbgStoryboardExportError(f"beat {beat_id} requires a cue")
        found = release_text.find(cue, cursor)
        if found < 0:
            raise HbgStoryboardExportError(f"beat cue is missing or out of order: {cue}")
        cursor = found + len(cue)
        description = beat.get("description")
        if not isinstance(description, str) or not description.strip():
            raise HbgStoryboardExportError("visual description must be agent-authored")
        anchors = beat.get("anchor_refs")
        if not isinstance(anchors, list) or not set(anchors).issubset(character_ids):
            raise HbgStoryboardExportError("storyboard beat contains an unknown anchor")
        risks = beat.get("risk_flags")
        if not isinstance(risks, list):
            raise HbgStoryboardExportError("risk_flags must be an array")
        mode = beat.get("generation_mode")
        if _HIGH_RISK.intersection(risks) and mode != "single":
            raise HbgStoryboardExportError("high-risk storyboard beats must use single generation")
        required = beat.get("required_entities")
        forbidden = beat.get("forbidden_entities")
        if not isinstance(required, list) or not required:
            raise HbgStoryboardExportError("required_entities must be nonempty")
        if not isinstance(forbidden, list):
            raise HbgStoryboardExportError("forbidden_entities must be an array")
        if not set(required).isdisjoint(forbidden):
            raise HbgStoryboardExportError("required and forbidden entities overlap")
        participants = beat.get("participants")
        if not isinstance(participants, Mapping):
            raise HbgStoryboardExportError("participants are required")
        allowed = participants.get("allowed")
        count = participants.get("count")
        if not isinstance(allowed, list) or not isinstance(count, int) or isinstance(count, bool):
            raise HbgStoryboardExportError("participants are invalid")
        if count != len(allowed) or not set(allowed).issubset(anchors):
            raise HbgStoryboardExportError("every visible participant must have an identity anchor reference")
        result.append({
            "id": f"s{index:03d}",
            "beatId": beat_id,
            "sectionId": beat.get("section_id"),
            "chapter": chapter_numbers[chapter_id],
            "cue": cue,
            "description": description.strip(),
            "captionIntent": beat.get("caption_intent"),
            "requiredEntities": list(required),
            "forbiddenEntities": list(forbidden),
            "riskFlags": list(risks),
            "generationMode": mode,
            "anchorRefs": list(anchors),
            "participants": {"count": participants.get("count"), "allowed": list(participants.get("allowed", []))},
            "highRisk": bool(_HIGH_RISK.intersection(risks)),
            "motion": "hold",
            "asset": f"assets/generated/scenes/s{index:03d}.png",
        })
    return result
