"""Group Visual Contract: one authoritative visual record per Semantic Shot Group.

The final image unit is the Semantic Shot Group, not the caption. Caption-level
contracts remain as bottom-layer evidence; this module aggregates them into one
per-group record that the Director consumes to build the Group Visual
Proposition and the caption-first prompt.

Everything here is deterministic: same inputs, same document, no model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .caption_contract import (
    CaptionContractError,
    CaptionVisualContract,
    _derive_visual_event_state,
    _derive_visual_state,
    _scene_terms_in_text,
    infer_death_cause,
    merge_visual_event_states,
)
from .caption_grouping import (
    CaptionGroupingError,
    load_current_caption_grouping_document,
)

GROUP_CONTRACT_SCHEMA = "group-visual-contract.v1"

# Deterministic one-line summaries per event predicate. Placeholders:
# {subjects} {location} {objects}
_FOCUS_PHRASES: dict[str, str] = {
    "departure_absence": "空屋中{objects}留在{location}，人物已离开，门外远处可看到背影走向田地",
    "alive_interaction": "{subjects}在{location}清醒互动（说话/走动），人物在场，克制呈现",
    "death_aftermath": "非血腥的死亡后果（cause={cause}）：{subjects}瘫软倒伏于{location}，旁边有{objects}，姿态与普通睡眠不相容",
    "death_mention": "对死亡的回忆或提及：{subjects}不在画面中倒伏，{location}保持克制，避免剧透与血腥",
    "medical_visit": "{subjects}在{location}求医：郎中/医馆与药柜清晰可见，呈急切求助姿态",
    "pregnancy_birth": "怀孕/家人反应：{subjects}孕中，家人激动围聚（{location}，非婚礼现场）",
    "return_home": "{subjects}归家：正在进门或刚抵达{location}，家人面向归来者相迎",
    "folk_song_collection": "下乡采风：年轻人在{location}与村民交谈记录，身背布袋、手拿笔记本",
    "wedding": "{subjects}出嫁婚礼：嫁衣花轿与迎亲队伍在{location}，锣鼓红布喜庆",
    "smoke_rising": "炊烟从{location}的农舍屋顶袅袅升起，乡村远景、天色渐暗",
    "solitude_leftover": "{location}空旷场景中仅剩{subjects}，相互依存、孤独构图",
    "author_creation_context": "创作之前的语境：旧式写字台、稿纸与民歌唱片/乐谱，不出现成书与故事人物",
    "donation_setup": "医院产房外/学校集合：组织学生献血的准备场景（{location}），克制呈现",
    "donation_rush": "有庆脱鞋奔向医院门口，被老师拦住/拖到一边（{location}）",
    "donation_wait": "医院验血区：学生们排队验血，有庆面对老师等待进入（{location}）",
    "donation_match": "血型匹配成功：有庆兴奋地跑到门口喊“要抽我的血啦！”（{location}）",
    "blood_loss": "持续抽血/失血：有庆脸色苍白、虚弱但仍清醒（{location}），克制、非血腥",
    "caption_scene_state": "{location}场景，按字幕呈现核心事件与关键物件",
}

_QUOTE_CONTINUATION_PREFIXES: tuple[str, ...] = ("我", "这", "就", "那", "还", "也", "他", "她")

_DONATION_STATES: frozenset[str] = frozenset({
    "donation_setup", "donation_rush", "donation_wait", "donation_match",
})


def _ordered_union(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    return out


def _dedupe_evidence(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("entity_id", "")), str(item.get("natural_language", "")))
        if key not in seen:
            seen.add(key)
            out.append(dict(item))
    return out


def summarize_group_visual_focus(
    *,
    event_state: Mapping[str, Any] | None,
    primary_subjects: Sequence[str],
    location: str,
    objects: Sequence[str],
) -> str:
    """One concise sentence describing what ONE image must show for the group."""

    predicate = str((event_state or {}).get("action_predicate") or "")
    subjects = "、".join(primary_subjects) or "人物"
    loc = location or "画面"
    objs = "、".join(objects) or "相关物件"
    cause = str((event_state or {}).get("cause_type") or "unknown")
    template = _FOCUS_PHRASES.get(predicate)
    if template:
        return template.format(subjects=subjects, location=loc, objects=objs, cause=cause)
    evidence = list((event_state or {}).get("required_observable_evidence", []))
    parts = [f"{loc}场景"]
    if evidence:
        parts.append(str(evidence[0]))
    parts.append(f"关键物件：{objs}")
    return "；".join(parts) + "。"


def aggregate_group_visual_contract(
    *,
    group: Mapping[str, Any],
    contracts: Mapping[str, CaptionVisualContract],
    captions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate one caption group into its Group Visual Contract record."""

    caption_ids = [str(item) for item in group.get("caption_ids", [])]
    if not caption_ids:
        raise CaptionGroupingError("group visual contract requires nonempty caption_ids")
    members = [contracts[cid] for cid in caption_ids]
    first = members[0]
    group_text = " / ".join(str(captions[cid].get("text", "")) for cid in caption_ids)

    must_show = _dedupe_evidence(item.to_dict() for contract in members for item in contract.must_show)
    may_show = _dedupe_evidence(item.to_dict() for contract in members for item in contract.may_show)
    narrative_function = str(group.get("narrative_function", first.narrative_function))
    # Author-background register never depicts story characters: a story
    # character that a caption merely mentions becomes supporting context, not
    # a must-show primary subject (Group Visual Proposition decides must_show).
    if narrative_function == "author_background":
        story_items = [item for item in must_show if str(item.get("entity_id", "")).startswith("C")]
        if story_items:
            must_show = [item for item in must_show if not str(item.get("entity_id", "")).startswith("C")]
            may_show = _dedupe_evidence([
                *may_show,
                *[
                    {**item, "reason": "author_background_story_character_context"}
                    for item in story_items
                ],
            ])
    must_ids = {item["entity_id"] for item in must_show}
    may_show = [item for item in may_show if item["entity_id"] not in must_ids]
    # Presence mode (memory/absent): in reflective registers, a character named
    # as the OBJECT of a first-person memory ("我只有这点想想凤霞的福份") must
    # not become a living current-timeline actor in the frame.
    if narrative_function in {"theory", "author_background"}:
        demoted_memory: list[dict[str, Any]] = []
        for contract in members:
            caption_text = str(contract.caption_text or "").strip()
            if caption_text.startswith(_QUOTE_CONTINUATION_PREFIXES):
                for item in contract.must_show:
                    if str(item.entity_id).startswith("C") and item.entity_id in must_ids:
                        demoted_memory.append({
                            **item.to_dict(),
                            "presence_mode": "absent_referenced",
                            "reason": "memory_object_absent_referenced",
                        })
        if demoted_memory:
            demoted_ids = {entry["entity_id"] for entry in demoted_memory}
            must_show = [item for item in must_show if item["entity_id"] not in demoted_ids]
            may_show = _dedupe_evidence([*may_show, *demoted_memory])
    # Recompute the participant constraint from the FINAL Group must_show so the
    # Group Contract never contradicts its own prompt (e.g. an author-background
    # hold with "no recurring character" must not keep expected_character_count=1).
    primary_ids = _ordered_union(
        item["entity_id"] for item in must_show if str(item["entity_id"]).startswith("C")
    )
    primary_names = _ordered_union(
        item["natural_language"] for item in must_show if str(item["entity_id"]).startswith("C")
    )
    supporting_ids = _ordered_union(
        item["entity_id"] for item in may_show
        if str(item["entity_id"]).startswith("C") and str(item["entity_id"]) not in primary_ids
    )
    supporting_names = _ordered_union(
        item["natural_language"] for item in may_show
        if str(item["entity_id"]).startswith("C") and str(item["entity_id"]) not in primary_ids
    )
    must_not_show = _dedupe_evidence(
        item.to_dict() for contract in members for item in contract.must_not_show_as_primary
    )

    visual_modes = {contract.visual_mode for contract in members}
    # Literal dominates: if any caption in a concrete event phase names a
    # drawable referent, the whole group frame is concrete.
    visual_mode = "literal" if "literal" in visual_modes else next(iter(visual_modes))
    # Re-derive the Group Contract from the FINAL group text instead of
    # aggregating stale caption-level contracts (contamination source).
    scene_terms = _scene_terms_in_text(group_text)
    group_location = scene_terms[0] if scene_terms else ""
    union_evidence = [
        item for contract in members for item in contract.must_show
    ]
    raw_event_state = _derive_visual_event_state(
        text=group_text,
        narrative_function=narrative_function,
        visual_mode=visual_mode,
        must_show=union_evidence,
        location=group_location,
    )
    group_visual_state = _derive_visual_state(
        text=group_text,
        narrative_function=narrative_function,
        visual_mode=visual_mode,
        event_state=raw_event_state,
        must_show=union_evidence,
    )
    if group_visual_state in _DONATION_STATES:
        event_state = {
            "action_predicate": group_visual_state,
            "cause_type": "",
            "is_reference_death": False,
            "actors": list(primary_names),
            "participant_roles": [],
            "objects": [],
            "subject_state": {
                "donation_setup": "组织学生献血的准备阶段",
                "donation_rush": "有庆抢先赶往医院",
                "donation_wait": "验血等待/认错/获准进入",
                "donation_match": "血型匹配、兴奋呼喊",
            }[group_visual_state],
            "required_observable_evidence": [
                "单一真实空间（学校集合/医院入口/验血区/门口），禁止把学校与医院揉进同一画面",
                "事件阶段按字幕呈现，克制不血腥",
            ],
            "forbidden_contradictory_state": ["复合语义空间（学校+医院+验血+奔跑同时同框）"],
        }
    elif group_visual_state == "alive_active":
        event_state = {
            "action_predicate": "alive_interaction",
            "cause_type": "",
            "is_reference_death": False,
            "actors": list(primary_names),
            "participant_roles": [],
            "objects": [],
            "subject_state": "人物在场、清醒互动",
            "required_observable_evidence": [
                "字幕点名的清醒人物在{location}互动（说话/走动/站）".format(location=group_location or "场景中"),
                "克制、非血腥",
            ],
            "forbidden_contradictory_state": ["尸体或倒地姿态", "人物已离开的空屋"],
        }
    else:
        event_state = raw_event_state.to_dict() if raw_event_state is not None else None
        if (
            event_state is not None
            and event_state.get("action_predicate") == "death_aftermath"
            and str(event_state.get("cause_type") or "") in {"", "unknown"}
        ):
            inferred_cause = infer_death_cause(group_text)
            if inferred_cause:
                event_state["cause_type"] = inferred_cause
                event_state["subject_state"] = f"非血腥的死亡后果（cause={inferred_cause}）"
    action_semantics = dict(first.scene_state.get("action_semantics", {}))
    expected_count = len(primary_ids)
    group_objects = _ordered_union(
        item.natural_language for contract in members for item in contract.must_show
        if not str(item.entity_id).startswith("C")
    )
    # Death focus must name the deceased, not every group actor, and the cause
    # objects (beans/needle), not unrelated props.
    death_members = [
        contract for contract in members
        if contract.visual_event_state is not None
        and contract.visual_event_state.action_predicate == "death_aftermath"
    ]
    if death_members:
        focus_subjects = _ordered_union(
            item.natural_language for contract in death_members for item in contract.must_show
            if str(item.entity_id).startswith("C")
        ) or primary_names
        focus_objects = _ordered_union(
            item.natural_language for contract in death_members for item in contract.must_show
            if not str(item.entity_id).startswith("C")
        ) or group_objects
    else:
        focus_subjects, focus_objects = primary_names, group_objects
    return {
        "group_id": str(group.get("group_id", "")),
        "caption_ids": caption_ids,
        "start": float(group.get("start", 0.0)),
        "end": float(group.get("end", 0.0)),
        "group_caption_text": group_text,
        "narrative_function": narrative_function,
        "visual_mode": visual_mode,
        "primary_subjects": primary_names,
        "primary_subject_ids": primary_ids,
        "supporting_subjects": supporting_names,
        "supporting_subject_ids": supporting_ids,
        "event": str(event_state.get("action_predicate", "")) if event_state else "",
        "event_phase": {
            "hard_split_event": str(action_semantics.get("hard_split_event", "none")),
            "event_instance_id": str(action_semantics.get("event_instance_id", "")),
        },
        "visual_state": group_visual_state,
        "presence_mode": (
            "memory"
            if any(contract.presence_mode == "memory" for contract in members)
            else "current"
        ),
        "location": group_location,
        "time_context": str(time_context_of(members)),
        "must_show": must_show,
        "may_show": may_show,
        "must_not_show": must_not_show,
        "expected_character_count": expected_count,
        "allow_unlisted_narrative_characters": not (visual_mode == "literal" and expected_count > 0),
        "visual_event_state": event_state,
        "group_visual_focus": summarize_group_visual_focus(
            event_state=event_state,
            primary_subjects=focus_subjects,
            location=group_location,
            objects=focus_objects,
        ),
        "caption_contract_bindings": [dict(item) for item in group.get("contract_bindings", [])],
        "caption_group_sha256": str(group.get("caption_group_sha256", "")),
    }


def location_of(contracts: Sequence[CaptionVisualContract]) -> str:
    for contract in contracts:
        if contract.location.strip():
            return contract.location
    return ""


def time_context_of(contracts: Sequence[CaptionVisualContract]) -> str:
    for contract in contracts:
        if contract.time_context.strip():
            return contract.time_context
    return ""


def build_group_visual_contract_document(
    *,
    release_id: str,
    groups: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, CaptionVisualContract],
    captions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Serialize the Group Visual Contract document (04_audio/GROUP_VISUAL_CONTRACT.json)."""

    records = [
        aggregate_group_visual_contract(group=group, contracts=contracts, captions=captions)
        for group in groups
    ]
    return {
        "schema_version": GROUP_CONTRACT_SCHEMA,
        "release_id": str(release_id),
        "group_count": len(records),
        "groups": records,
    }


def write_group_visual_contract_document(
    root: str | Path,
    *,
    release_id: str,
    groups: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, CaptionVisualContract],
    captions: Mapping[str, Mapping[str, Any]],
) -> Path:
    document = build_group_visual_contract_document(
        release_id=release_id,
        groups=groups,
        contracts=contracts,
        captions=captions,
    )
    path = Path(root) / "04_audio" / "GROUP_VISUAL_CONTRACT.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_current_group_visual_contract(root: str | Path) -> dict[str, Any]:
    """Load the persisted Group Visual Contract and verify it is current."""

    root = Path(root)
    path = root / "04_audio" / "GROUP_VISUAL_CONTRACT.json"
    if path.is_symlink() or not path.is_file():
        raise CaptionGroupingError("group visual contract is missing or symlinked")
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptionGroupingError(f"group visual contract is unreadable: {error}") from error
    if not isinstance(persisted, Mapping) or persisted.get("schema_version") != GROUP_CONTRACT_SCHEMA:
        raise CaptionGroupingError("group visual contract must use current v1 schema")
    grouping = load_current_caption_grouping_document(root)
    contracts_path = root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    bindings_path = root / "04_audio" / "CAPTION_BINDINGS.json"
    try:
        contracts = {
            caption_id: CaptionVisualContract.from_mapping(payload)
            for caption_id, payload in json.loads(
                contracts_path.read_text(encoding="utf-8")
            )["contracts"].items()
        }
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
        raw_captions = bindings.get("captions")
        captions = raw_captions if isinstance(raw_captions, Mapping) else {
            item["caption_id"]: item for item in raw_captions
        }
    except (OSError, json.JSONDecodeError, KeyError, CaptionContractError) as error:
        raise CaptionGroupingError(f"group visual contract inputs are invalid: {error}") from error
    expected = build_group_visual_contract_document(
        release_id=str(bindings.get("release_id") or "unknown"),
        groups=grouping["groups"],
        contracts=contracts,
        captions=captions,
    )
    if persisted != expected:
        raise CaptionGroupingError("persisted group visual contract is stale or partial")
    return expected


def build_group_visual_contract_from_project(
    root: str | Path,
    *,
    validate_only: bool = False,
) -> Path | dict[str, Any]:
    """Build or validate the Group Visual Contract from a project's current artifacts."""

    from .caption_contract import load_caption_visual_contract_document

    root = Path(root)
    grouping = load_current_caption_grouping_document(root)
    contracts = load_caption_visual_contract_document(
        root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    )
    bindings = json.loads(
        (root / "04_audio" / "CAPTION_BINDINGS.json").read_text(encoding="utf-8")
    )
    raw_captions = bindings.get("captions")
    captions = raw_captions if isinstance(raw_captions, Mapping) else {
        item["caption_id"]: item for item in raw_captions
    }
    document = build_group_visual_contract_document(
        release_id=str(bindings.get("release_id") or "unknown"),
        groups=grouping["groups"],
        contracts=contracts,
        captions=captions,
    )
    if validate_only:
        return document
    return write_group_visual_contract_document(
        root,
        release_id=str(bindings.get("release_id") or "unknown"),
        groups=grouping["groups"],
        contracts=contracts,
        captions=captions,
    )


__all__ = [
    "GROUP_CONTRACT_SCHEMA",
    "aggregate_group_visual_contract",
    "build_group_visual_contract_document",
    "build_group_visual_contract_from_project",
    "load_current_group_visual_contract",
    "summarize_group_visual_focus",
    "write_group_visual_contract_document",
]
