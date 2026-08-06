from __future__ import annotations

import re
from typing import Any


class LongformContractError(ValueError):
    pass


SCHEMA_ARRAY_FIELDS = {
    "script.longform.v1": "sections",
    "character-bible.v1": "characters",
    "shot-plan.v1": "shots",
    "image-assets.v1": "assets",
    "timeline.v1": "shots",
    # narrator-essay 正式合同登记（数组字段映射）。
    # 注意：这些合同 schema_version = 合同 id（非 "1.0"），不走 _validate_common，
    # 由 validate_longform_manifest 早分支委托给 narrator_essay_contracts 校验器。
    "creative-decision.v1": "candidate_routes",
    "fate-anchors.v1": "anchors",
    "event-cards.v1": "cards",
    "script-beats.v1": "beats",
    "voice-direction.narrator.v1": "chunks",
    "content-quality-report.v1": "checks",
}
LOCKED_STATUSES = {"reviewed", "approved", "locked"}
RELEASE_ID_PATTERN = re.compile(r"v[1-9][0-9]*-r[1-9][0-9]*")


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LongformContractError(f"{label} must be a nonempty string")
    return value.strip()


def _validate_common(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") != "1.0":
        raise LongformContractError("unsupported schema_version")
    schema_id = _nonempty_text(payload.get("schema_id"), "schema_id")
    if schema_id not in {*SCHEMA_ARRAY_FIELDS, "art-direction.v1"}:
        raise LongformContractError(f"unsupported schema_id: {schema_id}")
    release_id = _nonempty_text(payload.get("release_id"), "release_id")
    if RELEASE_ID_PATTERN.fullmatch(release_id) is None:
        raise LongformContractError("release_id must use vN-rN")
    book = payload.get("book")
    if not isinstance(book, dict):
        raise LongformContractError("book must be an object")
    _nonempty_text(book.get("title"), "book.title")
    _nonempty_text(book.get("author"), "book.author")
    status = _nonempty_text(payload.get("status"), "status")
    if status not in {"draft", "reviewed", "approved", "locked", "rejected"}:
        raise LongformContractError(f"unsupported status: {status}")
    return schema_id


def _validate_unique_ids(
    values: list[Any],
    *,
    id_keys: tuple[str, ...],
    label: str,
) -> None:
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise LongformContractError(f"{label}[{index}] must be an object")
        identifier = ""
        for key in id_keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                identifier = candidate.strip()
                break
        if not identifier:
            raise LongformContractError(
                f"{label}[{index}] requires one of: {', '.join(id_keys)}"
            )
        if identifier in seen:
            raise LongformContractError(f"{label} contains duplicate id: {identifier}")
        seen.add(identifier)


def _validate_locked_shape(schema_id: str, payload: dict[str, Any]) -> None:
    status = str(payload["status"])
    if schema_id == "art-direction.v1":
        if status in LOCKED_STATUSES:
            for key in ("visual_world", "palette", "texture"):
                _nonempty_text(
                    payload.get(key), f"locked art direction {key}"
                )
            lighting = payload.get("lighting")
            if not isinstance(lighting, dict) or not lighting:
                raise LongformContractError(
                    "locked art direction requires nonempty lighting"
                )
        return
    array_field = SCHEMA_ARRAY_FIELDS[schema_id]
    values = payload.get(array_field)
    if not isinstance(values, list):
        raise LongformContractError(f"{array_field} must be an array")
    if status in LOCKED_STATUSES and not values:
        raise LongformContractError(
            f"{status} {schema_id} requires nonempty {array_field}"
        )
    if not values:
        return
    if schema_id == "script.longform.v1":
        _validate_unique_ids(values, id_keys=("section_id",), label=array_field)
    elif schema_id == "character-bible.v1":
        _validate_unique_ids(values, id_keys=("character_id",), label=array_field)
        for character in values:
            _nonempty_text(
                character.get("continuity_anchor"),
                "character.continuity_anchor",
            )
    elif schema_id in {"shot-plan.v1", "timeline.v1"}:
        _validate_unique_ids(values, id_keys=("shot_id",), label=array_field)
        if schema_id == "timeline.v1" and status in LOCKED_STATUSES:
            captions = payload.get("captions")
            if not isinstance(captions, list) or not captions:
                raise LongformContractError(
                    "locked timeline requires nonempty captions"
                )
            if (
                payload.get("caption_timing_source")
                != "edge_vtt"
            ):
                raise LongformContractError(
                    "locked timeline requires edge_vtt"
                )
    elif schema_id == "image-assets.v1":
        _validate_unique_ids(values, id_keys=("asset_id",), label=array_field)


def validate_longform_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LongformContractError("manifest root must be an object")
    # narrator-essay 合同委托给领域校验器（schema_version = 合同 id，非 "1.0"）。
    sv = payload.get("schema_version")
    if isinstance(sv, str) and sv in _narrator_essay_validators():
        _narrator_essay_validators()[sv](payload)
        return payload
    schema_id = _validate_common(payload)
    _validate_locked_shape(schema_id, payload)
    return payload


def _narrator_essay_validators() -> dict:
    """惰性导入 narrator_essay_contracts，避免循环依赖。"""
    from book_video_factory.narrator_essay_contracts import VALIDATORS

    return VALIDATORS
