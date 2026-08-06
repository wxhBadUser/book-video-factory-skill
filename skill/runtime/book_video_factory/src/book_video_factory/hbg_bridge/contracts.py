from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any


class HbgBridgeContractError(ValueError):
    """Bridge input is incomplete, inconsistent, or unsafe."""


_PLACEHOLDER_RE = re.compile(r"(?:待定|后补|占位|placeholder|todo|tbd)", re.IGNORECASE)
_UNSAFE_PATH_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)|^[A-Za-z]:[\\/]|^/")
_EDGE_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_EDGE_PITCH_RE = re.compile(r"^[+-]\d{1,3}Hz$")
_HIGH_RISK_FLAGS = {
    "hands", "phone", "tool_use", "water_action", "animal_contact",
    "body_contact", "reflection", "death_climax", "hero_shot",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HbgBridgeContractError(message)


def _text(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value.strip() != "", f"{label} is required")
    text = value.strip()
    _require(value == text, f"{label} must not contain surrounding whitespace")
    _require(not _PLACEHOLDER_RE.search(text), f"{label} contains placeholder text")
    _require(not _UNSAFE_PATH_RE.search(text), f"{label} contains path traversal or an absolute path")
    return text


def _string_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    _require(isinstance(value, list), f"{label} must be an array")
    if nonempty:
        _require(bool(value), f"{label} must be nonempty")
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    _require(len(result) == len(set(result)), f"{label} must not contain duplicates")
    return result


def _hex64(value: Any, label: str) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             f"{label} must be a lowercase SHA-256 digest")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _scan_unsafe_strings(value: Any, path: str = "root") -> None:
    if isinstance(value, str):
        if _UNSAFE_PATH_RE.search(value.strip()):
            raise HbgBridgeContractError(f"{path} contains path traversal or an absolute path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _scan_unsafe_strings(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for index, item in enumerate(value):
            _scan_unsafe_strings(item, f"{path}[{index}]")


def validate_bridge_input(
    payload: Mapping[str, Any],
    *,
    script: Mapping[str, Any],
    package_digest: str,
    release_text_sha256: str,
) -> dict[str, Any]:
    """Validate an agent-authored Phase 2 handoff without inventing missing data."""
    _require(isinstance(payload, Mapping), "bridge input must be an object")
    normalized = copy.deepcopy(dict(payload))
    _scan_unsafe_strings(normalized)
    _require(normalized.get("schema_version") == "hbg-bridge-input.v1",
             "schema_version must be hbg-bridge-input.v1")
    _require(_hex64(normalized.get("content_package_digest"), "content_package_digest") == package_digest,
             "content package digest does not match the verified Phase 1 package")
    _require(_hex64(normalized.get("release_text_sha256"), "release_text_sha256") == release_text_sha256,
             "release text SHA-256 does not match the verified Phase 1 package")
    release_id = _text(normalized.get("release_id"), "release_id")
    _require(release_id == script.get("release_id"), "release_id does not match script")
    orientation = normalized.get("orientation")
    _require(orientation in {"landscape", "portrait"}, "orientation must be landscape or portrait")

    brand = _mapping(normalized.get("brand"), "brand")
    for key in ("series_name", "lead_text", "lead_display_text", "reveal_text"):
        _text(brand.get(key), f"brand.{key}")
    episode_number = brand.get("episode_number")
    _require(isinstance(episode_number, int) and not isinstance(episode_number, bool) and episode_number >= 1,
             "brand.episode_number must be a positive integer")

    narration = _mapping(normalized.get("narration"), "narration")
    _require(narration.get("provider") == "edge-tts", "narration.provider must be edge-tts")
    _text(narration.get("voice"), "narration.voice")
    for key in ("body_rate", "lead_rate", "reveal_rate"):
        rate = _text(narration.get(key), f"narration.{key}")
        _require(_EDGE_RATE_RE.fullmatch(rate) is not None, f"narration.{key} has invalid Edge TTS rate syntax")
    pitch = _text(narration.get("pitch"), "narration.pitch")
    _require(_EDGE_PITCH_RE.fullmatch(pitch) is not None, "narration.pitch has invalid Edge TTS pitch syntax")

    release = _mapping(script.get("release_version"), "script.release_version")
    sections = release.get("sections")
    _require(isinstance(sections, list) and bool(sections), "script release sections are required")
    section_ids = [_text(item.get("section_id") if isinstance(item, Mapping) else None,
                         f"script section {index}.section_id") for index, item in enumerate(sections)]
    _require(len(section_ids) == len(set(section_ids)), "script section IDs must be unique")
    section_index = {section_id: index for index, section_id in enumerate(section_ids)}
    section_text = {
        section_id: _text(sections[index].get("text"), f"script section {index}.text")
        for index, section_id in enumerate(section_ids)
    }

    chapters = normalized.get("chapters")
    _require(isinstance(chapters, list) and bool(chapters), "chapters must be a nonempty array")
    chapter_ids: list[str] = []
    flattened: list[str] = []
    last_index = -1
    for index, chapter in enumerate(chapters):
        item = _mapping(chapter, f"chapters[{index}]")
        chapter_id = _text(item.get("chapter_id"), f"chapters[{index}].chapter_id")
        _require(chapter_id not in chapter_ids, "chapter_id values must be unique")
        chapter_ids.append(chapter_id)
        _text(item.get("title"), f"chapters[{index}].title")
        covered = _string_list(item.get("section_ids"), f"chapters[{index}].section_ids")
        for section_id in covered:
            _require(section_id in section_index, f"chapter references unknown section: {section_id}")
            _require(section_id not in flattened,
                     "chapter section coverage must include every frozen section exactly once")
            current = section_index[section_id]
            _require(current > last_index, "chapter section order must follow frozen script section order")
            last_index = current
            flattened.append(section_id)
    _require(flattened == section_ids,
             "chapter section coverage must include every frozen section exactly once")

    characters = normalized.get("characters")
    _require(isinstance(characters, list) and bool(characters), "characters must be a nonempty array")
    character_ids: list[str] = []
    for index, character in enumerate(characters):
        item = _mapping(character, f"characters[{index}]")
        character_id = _text(item.get("character_id"), f"characters[{index}].character_id")
        _require(character_id not in character_ids, "character_id values must be unique")
        character_ids.append(character_id)
        for key in ("name", "role", "life_stage"):
            _text(item.get(key), f"characters[{index}].{key}")
        anchor_status = _text(item.get("anchor_status"), f"characters[{index}].anchor_status")
        _require(anchor_status == "pending", "characters.anchor_status must be pending until visual anchor approval")
        for key in ("immutable_traits", "changeable_traits", "wardrobe", "relationships"):
            _string_list(item.get(key), f"characters[{index}].{key}")

    beats = normalized.get("storyboard_beats")
    _require(isinstance(beats, list) and bool(beats), "storyboard_beats must be a nonempty array")
    beat_ids: set[str] = set()
    beat_sections: set[str] = set()
    release_text = str(release.get("text", ""))
    search_from = 0
    for index, beat in enumerate(beats):
        item = _mapping(beat, f"storyboard_beats[{index}]")
        beat_id = _text(item.get("beat_id"), f"storyboard_beats[{index}].beat_id")
        _require(beat_id not in beat_ids, "storyboard beat IDs must be unique")
        beat_ids.add(beat_id)
        section_id = _text(item.get("section_id"), f"storyboard_beats[{index}].section_id")
        _require(section_id in section_index, f"storyboard beat references unknown section: {section_id}")
        beat_sections.add(section_id)
        chapter_id = _text(item.get("chapter_id"), f"storyboard_beats[{index}].chapter_id")
        _require(chapter_id in chapter_ids, f"storyboard beat references unknown chapter: {chapter_id}")
        mapped_chapter = next(ch for ch in chapters if ch["chapter_id"] == chapter_id)
        _require(section_id in mapped_chapter["section_ids"],
                 "storyboard beat chapter does not contain its section")
        cue = _text(item.get("cue"), f"storyboard_beats[{index}].cue")
        _require(cue in section_text[section_id], "storyboard cue must belong to its declared section")
        found = release_text.find(cue, search_from)
        _require(found >= 0, f"storyboard cue is missing or out of order: {cue}")
        search_from = found + max(1, len(cue))
        _text(item.get("description"), f"storyboard_beats[{index}].description")
        _text(item.get("caption_intent"), f"storyboard_beats[{index}].caption_intent")
        required = _string_list(item.get("required_entities"), f"storyboard_beats[{index}].required_entities")
        forbidden = _string_list(item.get("forbidden_entities"), f"storyboard_beats[{index}].forbidden_entities", nonempty=False)
        _require(set(required).isdisjoint(forbidden),
                 "required and forbidden entities must be disjoint")
        risk_flags = _string_list(item.get("risk_flags"), f"storyboard_beats[{index}].risk_flags", nonempty=False)
        unknown_risks = set(risk_flags) - _HIGH_RISK_FLAGS
        _require(not unknown_risks, f"storyboard_beats[{index}].risk_flags contains unknown values: {sorted(unknown_risks)}")
        mode = item.get("generation_mode")
        _require(mode in {"2x2", "single"}, "generation_mode must be 2x2 or single")
        if _HIGH_RISK_FLAGS.intersection(risk_flags):
            _require(mode == "single", "high-risk storyboard beats must use single generation")
        anchors = _string_list(
            item.get("anchor_refs"), f"storyboard_beats[{index}].anchor_refs", nonempty=False
        )
        _require(set(anchors).issubset(character_ids), "storyboard beat contains unknown anchor reference")
        participants = _mapping(item.get("participants"), f"storyboard_beats[{index}].participants")
        allowed = _string_list(
            participants.get("allowed"),
            f"storyboard_beats[{index}].participants.allowed",
            nonempty=False,
        )
        count = participants.get("count")
        _require(isinstance(count, int) and not isinstance(count, bool) and count >= 0 and count == len(allowed),
                 "participants.count must equal the number of allowed participants")
        _require(set(allowed).issubset(character_ids), "participants contain unknown character IDs")
        _require(set(allowed).issubset(anchors), "every visible participant must have an identity anchor reference")
    _require(beat_sections == set(section_ids),
             "storyboard beats must cover every frozen script section")
    return normalized
