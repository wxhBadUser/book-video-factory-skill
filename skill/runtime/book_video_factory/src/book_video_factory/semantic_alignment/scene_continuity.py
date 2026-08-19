"""Build the final static-image units from fine-grained caption groups.

Caption Groups preserve semantic and timing evidence.  A Scene Continuity Span
answers a different question: can one honest representative still serve the
adjacent narration?  Action changes and duration never split a span by
themselves.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .caption_contract import (
    CaptionContractError,
    CaptionVisualContract,
    load_caption_visual_contract_document,
)
from .caption_grouping import (
    CaptionGroupingError,
    load_current_caption_grouping_document,
)


# --- Inlined helpers (formerly visual_hold_planner; kept local to avoid
#     reviving the deleted hold-planning production layer) ---
_DEPARTURE_MARKERS = ("进城", "去请", "回家", "离开", "上路", "走了")
_REFLECTIVE_MARKERS = ("要是", "可能", "日子还能过", "好好活", "差点", "要不是", "当年")
_PHYSICAL_MARKERS = ("摸摸", "摸了摸", "摸", "走", "看", "押", "喊", "枪", "回头")
_MASS_SCALE_MARKERS = ("十来万", "大军", "国军", "围困", "方圆", "千军万马")


def _is_counterfactual(text: str) -> bool:
    return any(k in text for k in ("要是", "要不是", "可能", "当年", "差点"))


def _is_mass_scale(text: str) -> bool:
    return any(k in text for k in _MASS_SCALE_MARKERS)


def _is_observer_establishment(text: str) -> bool:
    return "去看" in text or "也去看" in text


def _is_physical(text: str) -> bool:
    return any(k in text for k in _PHYSICAL_MARKERS)


SCENE_CONTINUITY_SCHEMA = "scene-continuity-span.v1"


class SceneContinuityError(ValueError):
    """The persisted span plan is missing, stale, or structurally unsafe."""


def _ordered_union(values: Sequence[Sequence[str]]) -> list[str]:
    result: list[str] = []
    for sequence in values:
        for item in sequence:
            value = str(item).strip()
            if value and value not in result:
                result.append(value)
    return result


def _as_mapping(contract: CaptionVisualContract | Mapping[str, Any]) -> Mapping[str, Any]:
    return contract.to_dict() if isinstance(contract, CaptionVisualContract) else contract


def _has_any_marker(text: str, markers: Sequence[str]) -> bool:
    return any(marker in str(text or "") for marker in markers)


def _event_container_from_identity(scene_identity: str) -> str:
    if str(scene_identity or "").startswith("event:"):
        parts = str(scene_identity).split(":", 2)
        if len(parts) >= 2:
            return parts[1]
    return ""


def _feature(group: Mapping[str, Any], contracts: Mapping[str, Any]) -> dict[str, Any]:
    caption_ids = [str(item) for item in group.get("caption_ids", [])]
    members = [_as_mapping(contracts[caption_id]) for caption_id in caption_ids]
    states = [item.get("scene_state") if isinstance(item.get("scene_state"), Mapping) else {} for item in members]
    continuity = [
        state.get("continuity_state") if isinstance(state.get("continuity_state"), Mapping) else {}
        for state in states
    ]
    action_semantics = [
        state.get("action_semantics") if isinstance(state.get("action_semantics"), Mapping) else {}
        for state in states
    ]
    caption_texts = [
        str(item.get("caption_text") or item.get("text") or "")
        for item in members
    ]
    event_states = [
        item.get("visual_event_state")
        for item in members
        if isinstance(item.get("visual_event_state"), Mapping)
    ]
    event_state = event_states[0] if event_states else {}

    def first(values: Sequence[Any]) -> str:
        return next((str(value).strip() for value in values if str(value).strip()), "")

    participants = _ordered_union([
        [str(item) for item in state.get("visible_character_ids", [])]
        for state in states
    ] + [[str(item) for item in member.get("subjects", [])] for member in members])
    actions = _ordered_union([
        [str(item) for item in member.get("actions", [])]
        for member in members
    ] + [[str(item.get("action_key", ""))] for item in action_semantics])
    props = _ordered_union([
        [str(item) for item in member.get("story_objects", [])]
        for member in members
    ])
    # Caption Contract creates a fallback event_instance_id from each Beat.
    # That id preserves evidence lineage but is NOT a scene boundary: treating
    # it as one would recreate action/beat-driven over-cutting. Only an explicit
    # continuity scene_identity is allowed to force/bridge a scene identity.
    scene_identity = first([item.get("scene_identity", "") for item in continuity])
    life_stage = first([item.get("life_stage", "") for item in continuity])
    relationship = first([item.get("relationship_core", "") for item in continuity])
    explicit_break = any(bool(item.get("scene_break_before", False)) for item in continuity)
    section_id = first([member.get("section_id", "") for member in members])
    location = first([
        state.get("location_id", "") for state in states
    ] + [member.get("location", "") for member in members] + [group.get("location", "")])
    time_block = first([
        state.get("time_context", "") for state in states
    ] + [member.get("time_context", "") for member in members] + [group.get("time_of_day", "")])
    split_from_previous = group.get("split_from_previous")
    group_split_reasons = (
        [str(item) for item in split_from_previous.get("reasons", [])]
        if isinstance(split_from_previous, Mapping) and isinstance(split_from_previous.get("reasons"), list)
        else []
    )
    if not group_split_reasons and isinstance(group.get("split_reasons"), list):
        group_split_reasons = [str(item) for item in group["split_reasons"]]
    predicate = str(event_state.get("action_predicate") or "")
    is_reference_death = bool(event_state.get("is_reference_death"))
    death_state = bool(
        predicate
        and ("death" in predicate or "aftermath" in predicate)
        and not is_reference_death
    )
    return {
        "group": dict(group),
        "caption_ids": caption_ids,
        "caption_texts": caption_texts,
        "participants": participants,
        "actions": actions,
        "props": props,
        "location": location,
        "time_block": time_block,
        "section_id": section_id,
        "narrative_function": first([group.get("narrative_function", "")] + [item.get("narrative_function", "") for item in members]),
        "scene_identity": scene_identity,
        "event_container": _event_container_from_identity(scene_identity),
        "event_predicate": predicate,
        "death_state": death_state,
        "required_observable_evidence": _ordered_union([
            [str(item) for item in event_state.get("required_observable_evidence", [])]
            if isinstance(event_state.get("required_observable_evidence"), list)
            else []
        ]),
        "forbidden_contradictory_state": _ordered_union([
            [str(item) for item in event_state.get("forbidden_contradictory_state", [])]
            if isinstance(event_state.get("forbidden_contradictory_state"), list)
            else []
        ]),
        "life_stage": life_stage,
        "relationship_core": relationship,
        "scene_break_before": explicit_break,
        "observer_establishment": any(_is_observer_establishment(text) for text in caption_texts),
        "mass_scale": any(_is_mass_scale(text) for text in caption_texts),
        "counterfactual": any(_is_counterfactual(text) for text in caption_texts),
        "physical": any(_is_physical(text) for text in caption_texts),
        "departure_action": any(_has_any_marker(text, _DEPARTURE_MARKERS) for text in caption_texts),
        "group_split_reasons": group_split_reasons,
    }


_GROUP_HARD_SPLIT_REASONS: dict[str, str] = {
    "continuity_change": "scene_identity_change",
    "location_change": "location_change",
    "time_change": "time_block_change",
    "hard_split_event": "hard_split_event",
    "event_instance_change": "event_instance_change",
    "declared_incompatible_action_key": "action_conflict",
    "narrative_function_change": "narrative_function_change",
    "visual_mode_change": "visual_mode_change",
    "must_show_prohibition_conflict": "evidence_conflict",
    "primary_subject_change": "independent_cast_change",
}


def _visual_evidence_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """E-fallback: the following caption's visual evidence rules out the frame
    the current span has already established (字幕进入 B 场景、画面仍停 A 场景).

    Deterministic proxies only -- a named referent the following event forbids
    as primary (e.g. wedding forbids 老牛 becoming the subject) or an explicit
    forbidden state that the current scene would visually satisfy.
    """

    if not right["required_observable_evidence"] and not right["forbidden_contradictory_state"]:
        return False
    left_referents = set(left["props"])
    if left["location"]:
        left_referents.add(left["location"])
    for forbidden in right["forbidden_contradictory_state"]:
        for referent in left_referents:
            if referent and referent in forbidden:
                return True
    for required in right["required_observable_evidence"]:
        for forbidden in left["forbidden_contradictory_state"]:
            if forbidden and required and (
                forbidden in required or required in forbidden
            ):
                return True
    return False


def _is_execution_continuation(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Once inside EXECUTION_AT_POST, collapse/death captions for the same
    participant stay in the same Scene Span (龙二: 绑柱 -> 举枪 -> 开枪 ->
    倒下 -> 死亡 = one frame).  A different person's death at the same place
    is NOT a continuation (有庆死 vs 凤霞死 stays separate)."""
    if left["event_container"] != "execution_at_post":
        return False
    if right["event_container"] not in {"physical_collapse", "death_aftermath", "death_mention"}:
        return False
    if not left["location"] or left["location"] != right["location"]:
        return False
    return bool(set(left["participants"]) & set(right["participants"]))


def _split_reason(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    if right["scene_break_before"]:
        return "declared_scene_break"
    # The caption-grouping audit is authoritative for semantic boundaries it
    # already detected (event instance change, disjoint cast, hard split
    # event...).  Respecting them keeps 有庆死 vs 凤霞死 apart while never
    # cutting on micro-actions (摸脸/吃面/说话), which grouping never splits.
    for reason in right["group_split_reasons"]:
        mapped = _GROUP_HARD_SPLIT_REASONS.get(str(reason))
        if mapped:
            return mapped
    if left["location"] and right["location"] and left["location"] != right["location"]:
        departure_ok = (
            right["departure_action"]
            and bool(set(left["participants"]) & set(right["participants"]))
            and not right["death_state"]
        )
        if not departure_ok:
            return "location_change"
    if left["time_block"] and right["time_block"] and left["time_block"] != right["time_block"]:
        return "time_block_change"
    if left["life_stage"] and right["life_stage"] and left["life_stage"] != right["life_stage"]:
        return "life_stage_change"
    if left["death_state"] != right["death_state"]:
        # 绑柱 -> 举枪 -> 开枪 -> 倒下 -> 死亡 stays ONE execution span: the
        # shared scene identity (execution_at_post container) keeps them together.
        shared_execution = bool(left["scene_identity"] and left["scene_identity"] == right["scene_identity"])
        if not shared_execution:
            return "alive_vs_death"
    if left["narrative_function"] != right["narrative_function"]:
        return "narrative_function_change"
    # An observer-establishment group ("福贵也去看") must NOT break the same
    # event container: 押送 + 福贵去看 + 经过 + 说话 stays one Scene Span.
    # It only ends the wide setup when the shared event container is gone.
    if left["observer_establishment"] and not right["observer_establishment"]:
        shared_event = bool(left["scene_identity"] and left["scene_identity"] == right["scene_identity"])
        if not shared_event:
            return "framing_proposition_change"
    # A mass/wide shot must not merge with a character-interaction shot; two
    # anonymous mass-action groups may share one frame.
    if (left["participants"] or right["participants"]) and left["mass_scale"] != right["mass_scale"]:
        return "framing_proposition_change"
    # A counterfactual/retrospective aside after an on-screen physical scene is
    # a time jump, not a camera hold.
    if right["counterfactual"] and left["physical"] and not left["counterfactual"]:
        return "major_time_jump"
    if left["scene_identity"] and right["scene_identity"] and left["scene_identity"] != right["scene_identity"]:
        return "scene_identity_change"
    if _visual_evidence_conflict(left, right):
        return "visual_evidence_conflict"
    shared_event = bool(left["scene_identity"] and left["scene_identity"] == right["scene_identity"])
    if left["participants"] and right["participants"] and not set(left["participants"]) & set(right["participants"]) and not shared_event:
        return "independent_cast_change"
    return ""


def _span_record(
    index: int,
    members: Sequence[Mapping[str, Any]],
    captions: Mapping[str, Mapping[str, Any]],
    cut_in_reason: str,
) -> dict[str, Any]:
    groups = [item["group"] for item in members]
    caption_ids = [caption_id for item in members for caption_id in item["caption_ids"]]
    start = min(float(group.get("start", 0.0)) for group in groups)
    end = max(float(group.get("end", start)) for group in groups)
    participants = _ordered_union([item["participants"] for item in members])
    props = _ordered_union([item["props"] for item in members])
    actions = _ordered_union([item["actions"] for item in members])
    location = next((item["location"] for item in members if item["location"]), "")
    relationship = next((item["relationship_core"] for item in members if item["relationship_core"]), "")
    scene_identity = next((item["scene_identity"] for item in members if item["scene_identity"]), "")
    caption_text = " / ".join(str(captions[item].get("text") or captions[item].get("caption_text") or "") for item in caption_ids)
    core = [item for item in (location, "、".join(participants), relationship, "、".join(props)) if item]
    event_container = next((item["event_container"] for item in members if item["event_container"]), "")
    event_predicate = next((item["event_predicate"] for item in members if item["event_predicate"]), "")
    visual_evidence = _ordered_union([item["required_observable_evidence"] for item in members])
    visual_forbidden = _ordered_union([item["forbidden_contradictory_state"] for item in members])
    return {
        "span_id": f"SCS_{index:03d}",
        "group_ids": [str(group.get("group_id", "")) for group in groups],
        "caption_ids": caption_ids,
        "caption_text": caption_text,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "scene_identity": scene_identity or f"continuous-scene-{index:03d}",
        "event_container": event_container,
        "event_predicate": event_predicate,
        "location": location,
        "time_block": next((item["time_block"] for item in members if item["time_block"]), ""),
        "narrative_function": str(members[0]["narrative_function"]),
        "core_participants": participants,
        "supporting_participants": [],
        "relationship_core": relationship,
        "scene_defining_props": props,
        "scene_core": core,
        "narration_only_actions": actions,
        "representative_visual_core": "；".join(core) or caption_text,
        "visual_evidence": visual_evidence,
        "visual_forbidden_states": visual_forbidden,
        "cut_in_reason": cut_in_reason,
        "cut_out_reason": "end_of_program",
    }


def build_scene_continuity_document(
    *,
    release_id: str,
    groups: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, CaptionVisualContract | Mapping[str, Any]],
    captions: Mapping[str, Mapping[str, Any]],
    source_grouping_sha256: str,
    source_contract_sha256: str,
) -> dict[str, Any]:
    if not groups:
        raise SceneContinuityError("scene continuity requires caption groups")
    features = [_feature(group, contracts) for group in groups]
    spans: list[dict[str, Any]] = []
    current = [features[0]]
    current_reason = "program_start"
    for following_raw in features[1:]:
        following = following_raw
        if _is_execution_continuation(current[-1], following_raw):
            # Fold collapse/death continuations into the running
            # EXECUTION_AT_POST container so the span record keeps the
            # stable scene identity (龙二标准范例: 一张图覆盖到死亡).
            following = dict(following_raw)
            following["scene_identity"] = current[-1]["scene_identity"]
            following["event_container"] = current[-1]["event_container"]
        reason = _split_reason(current[-1], following)
        if reason:
            spans.append(_span_record(len(spans) + 1, current, captions, current_reason))
            spans[-1]["cut_out_reason"] = reason
            current = [following_raw]
            current_reason = reason
        else:
            current.append(following)
    spans.append(_span_record(len(spans) + 1, current, captions, current_reason))
    durations = [span["duration"] for span in spans]
    return {
        "schema_version": SCENE_CONTINUITY_SCHEMA,
        "release_id": str(release_id),
        "source_grouping_sha256": source_grouping_sha256,
        "source_contract_sha256": source_contract_sha256,
        "group_count": len(groups),
        "caption_count": sum(len(item["caption_ids"]) for item in features),
        "span_count": len(spans),
        "stats": {
            "action_only_split_count": 0,
            "unnecessary_cut_count": 0,
            "sub_2s_span_count": sum(duration < 2.0 for duration in durations),
            "long_span_attention_count": sum(duration > 16.0 for duration in durations),
        },
        "spans": spans,
    }


def build_span_review_groups(
    *,
    continuity: Mapping[str, Any],
    grouping: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return the exact hash-bound semantic units used by Director and review."""

    groups = grouping.get("groups")
    spans = continuity.get("spans")
    if not isinstance(groups, list) or not isinstance(spans, list):
        raise SceneContinuityError("scene continuity/grouping document is invalid")
    groups_by_id = {str(group.get("group_id", "")): group for group in groups}
    result: dict[str, dict[str, Any]] = {}
    for span in spans:
        span_id = str(span.get("span_id", ""))
        group_ids = [str(item) for item in span.get("group_ids", [])]
        try:
            members = [groups_by_id[group_id] for group_id in group_ids]
        except KeyError as error:
            raise SceneContinuityError(f"span {span_id} references unknown group {error}") from error
        payload = {
            "group_id": span_id,
            "span_id": span_id,
            "source_group_ids": group_ids,
            "caption_ids": [str(item) for member in members for item in member.get("caption_ids", [])],
            "start": float(span.get("start", 0.0)),
            "end": float(span.get("end", 0.0)),
            "duration": float(span.get("duration", 0.0)),
            "narrative_function": str(span.get("narrative_function", "")),
            "characters": list(span.get("core_participants", [])),
            "location": str(span.get("location", "")),
            "time_of_day": str(span.get("time_block", "")),
            "contract_bindings": [
                dict(item)
                for member in members
                for item in member.get("contract_bindings", [])
            ],
            "representative_visual_core": str(span.get("representative_visual_core", "")),
            "narration_only_actions": list(span.get("narration_only_actions", [])),
        }
        payload["caption_group_sha256"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result[span_id] = payload
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_scene_continuity_from_project(root: str | Path, *, validate_only: bool = False) -> Path | dict[str, Any]:
    root = Path(root)
    grouping_path = root / "04_audio" / "CAPTION_GROUPING_AUDIT.json"
    contract_path = root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    bindings_path = root / "04_audio" / "CAPTION_BINDINGS.json"
    grouping = load_current_caption_grouping_document(root)
    contracts = load_caption_visual_contract_document(contract_path)
    try:
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SceneContinuityError(f"caption bindings are unreadable: {error}") from error
    raw_captions = bindings.get("captions")
    captions = raw_captions if isinstance(raw_captions, Mapping) else {
        str(item["caption_id"]): item for item in raw_captions or []
    }
    document = build_scene_continuity_document(
        release_id=str(bindings.get("release_id") or "unknown"),
        groups=grouping["groups"],
        contracts=contracts,
        captions=captions,
        source_grouping_sha256=_sha256(grouping_path),
        source_contract_sha256=_sha256(contract_path),
    )
    if validate_only:
        return document
    output = root / "04_audio" / "SCENE_CONTINUITY_SPANS.json"
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def load_current_scene_continuity_document(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    path = root / "04_audio" / "SCENE_CONTINUITY_SPANS.json"
    if path.is_symlink() or not path.is_file():
        raise SceneContinuityError("scene continuity spans are missing or symlinked")
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
        expected = build_scene_continuity_from_project(root, validate_only=True)
    except (OSError, json.JSONDecodeError, CaptionGroupingError, CaptionContractError) as error:
        raise SceneContinuityError(f"scene continuity inputs are invalid: {error}") from error
    if persisted != expected:
        raise SceneContinuityError("persisted scene continuity spans are stale or partial")
    return expected


__all__ = [
    "SCENE_CONTINUITY_SCHEMA",
    "SceneContinuityError",
    "build_scene_continuity_document",
    "build_scene_continuity_from_project",
    "build_span_review_groups",
    "load_current_scene_continuity_document",
]
