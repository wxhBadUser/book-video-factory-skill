"""Decide which display captions may legally share one generated image.

Background
----------
The production defect: consecutive display captions were merged onto a single
shot whenever they were merely *adjacent in time*. A single image was then
asked to illustrate 凤霞出嫁 and 雪地里孩子在跑 at once, or a plot beat and an
author-background beat at once. No image can satisfy both, so the second half
of the group always looked wrong.

Contract
--------
Two consecutive captions may share a shot only when **none** of the hard split
triggers fire:

``character_change``
    The set of on-screen characters changed at all (including someone merely
    entering or leaving). A different cast means a different picture.
``location_change``
    The narration moved to a different place.
``time_change``
    The narration moved to a different point in time / time of day.
``narrative_function_change``
    The narration switched register: 剧情(plot) ↔ 理论(theory) ↔
    作者背景(author_background) ↔ 结尾(closing) ↔ 开场(opening) ↔ 过渡(transition).

One duration trigger caps runaway groups:

``duration_limit``  the group would exceed 16 seconds. There is no caption-count cap.

Everything in this module is pure and deterministic: same input, same output,
no clock, no network, no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

NARRATIVE_FUNCTIONS: tuple[str, ...] = (
    "opening",
    "plot",
    "theory",
    "author_background",
    "transition",
    "closing",
)

SPLIT_REASONS: tuple[str, ...] = (
    "character_change",
    "location_change",
    "time_change",
    "narrative_function_change",
    "duration_limit",
)

MAX_IMAGE_GROUP_DURATION = 16.0
DEFAULT_MAX_GROUP_DURATION = MAX_IMAGE_GROUP_DURATION

_EPSILON = 1e-6


class CaptionGroupingError(RuntimeError):
    """A caption grouping is unsafe: one image would have to serve two meanings."""


@dataclass(frozen=True)
class CaptionUnit:
    """One display caption plus the narrative context needed to group it."""

    caption_id: str
    text: str
    start: float
    end: float
    narrative_function: str
    characters: tuple[str, ...] = ()
    location: str = ""
    time_of_day: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_id": self.caption_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "characters": list(self.characters),
            "location": self.location,
            "time_of_day": self.time_of_day,
            "narrative_function": self.narrative_function,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CaptionUnit":
        # B07 (re-review): a caption without an explicitly declared register is
        # a hard error, never silently "plot". The production restorer must tag
        # every caption with its real script-section register; if it did not,
        # the storyboard gate must reject the caption rather than collapse every
        # scene to the same register.
        raw_nf = raw.get("narrative_function") or raw.get("narrativeFunction")
        if not raw_nf or str(raw_nf).strip() not in NARRATIVE_FUNCTIONS:
            caption_id = raw.get("caption_id") or raw.get("captionId") or raw.get("id") or "?"
            raise CaptionGroupingError(
                f"caption {caption_id} is missing or has an unknown narrative_function "
                f"{raw_nf!r}; expected one of {NARRATIVE_FUNCTIONS}"
            )
        return cls(
            caption_id=str(raw.get("caption_id") or raw.get("captionId") or raw.get("id") or ""),
            text=str(raw.get("text", "")),
            start=float(raw.get("start", 0.0)),
            end=float(raw.get("end", 0.0)),
            characters=tuple(str(item) for item in raw.get("characters", ())),
            location=str(raw.get("location", "")),
            time_of_day=str(raw.get("time_of_day") or raw.get("timeOfDay") or ""),
            narrative_function=str(raw_nf).strip(),
        )


# The script package uses a richer narrative_function vocabulary than the
# coarse caption-grouping register. A deterministic, total map keeps the
# grouping gate honest: every script section resolves to exactly one register,
# and an unknown value is rejected rather than silently collapsed to "plot".
_SCRIPT_REGISTER_MAP: dict[str, str] = {
    "hook": "opening",
    "opening": "opening",
    "world_setup": "plot",
    "character_entry": "plot",
    "desire": "plot",
    "choice": "plot",
    "cost": "plot",
    "escalation": "plot",
    "midpoint_requestion": "plot",
    "revelation": "plot",
    "reinterpretation": "plot",
    "modern_mirror": "plot",
    "plot": "plot",
    "theory": "theory",
    "author_background": "author_background",
    "transition": "transition",
    "closing": "closing",
    "ending_image": "closing",
}


def normalize_script_register(narrative_function: str) -> str:
    """Map a script-section narrative_function to a caption-grouping register."""

    if narrative_function not in _SCRIPT_REGISTER_MAP:
        raise CaptionGroupingError(
            f"unknown script narrative_function {narrative_function!r}; "
            f"cannot map to a caption register"
        )
    return _SCRIPT_REGISTER_MAP[narrative_function]


@dataclass(frozen=True)
class CaptionSectionRegister:
    """The register a script section imposes on the captions it covers.

    ``display_start``/``display_end`` are display-character offsets (the same
    coordinate system ``restore_display_captions`` uses internally), so a
    caption is tagged by the section whose span contains its display interval.
    """

    display_start: float
    display_end: float
    narrative_function: str
    characters: tuple[str, ...] = ()
    location: str = ""
    time_of_day: str = ""


def assign_caption_registers(
    items: Sequence[Mapping[str, Any]],
    register: Sequence[CaptionSectionRegister],
) -> list[dict[str, Any]]:
    """Deterministically tag each caption with its section's register.

    Every caption must fall *entirely* within exactly one section's display
    span. A caption that straddles a boundary or lies outside every section is
    rejected (fail closed): a half-tagged caption would let the grouping gate
    silently merge two registers into one image.
    """

    if not items:
        raise CaptionGroupingError("cannot tag an empty caption timeline")
    if not register:
        raise CaptionGroupingError("a section register is required to tag captions")
    tagged: list[dict[str, Any]] = []
    for item in items:
        start = float(item.get("display_start", item.get("start", 0.0)))
        end = float(item.get("display_end", item.get("end", 0.0)))
        covers = [
            entry
            for entry in register
            if entry.display_start - _EPSILON <= start and end <= entry.display_end + _EPSILON
        ]
        if len(covers) != 1:
            raise CaptionGroupingError(
                f"caption [{start:.3f},{end:.3f}] is not covered by exactly one section "
                f"register ({len(covers)} matched); caption tagging must be deterministic"
            )
        entry = covers[0]
        merged = dict(item)
        merged["narrative_function"] = entry.narrative_function
        merged["characters"] = list(entry.characters)
        merged["location"] = entry.location
        merged["time_of_day"] = entry.time_of_day
        tagged.append(merged)
    return tagged


@dataclass(frozen=True)
class CaptionGroup:
    """A set of captions that one image is allowed to illustrate."""

    group_id: str
    caption_ids: tuple[str, ...]
    start: float
    end: float
    narrative_function: str
    characters: tuple[str, ...] = ()
    location: str = ""
    time_of_day: str = ""
    split_reasons: tuple[str, ...] = ()
    scene_state_signature: str = ""
    contract_bindings: tuple[Mapping[str, str], ...] = ()
    split_from_previous: Mapping[str, Any] = field(
        default_factory=lambda: {"required": False, "reasons": []}
    )

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "caption_ids": list(self.caption_ids),
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "narrative_function": self.narrative_function,
            "characters": list(self.characters),
            "location": self.location,
            "time_of_day": self.time_of_day,
            "split_reasons": list(self.split_reasons),
            "scene_state_signature": self.scene_state_signature,
            "contract_bindings": [dict(item) for item in self.contract_bindings],
            "split_from_previous": dict(self.split_from_previous),
        }


def _scene_state_signature(contract: Any) -> str:
    state = getattr(contract, "scene_state", {})
    payload = {
        "visible_character_ids": sorted(str(item) for item in state.get("visible_character_ids", [])),
        "location_id": str(state.get("location_id", "")),
        "time_context": str(state.get("time_context", "")),
        "continuity_state": _visual_continuity_state(state),
        "narrative_function": str(getattr(contract, "narrative_function", "")),
        "visual_mode": str(getattr(contract, "visual_mode", "")),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _visual_continuity_state(scene_state: Mapping[str, Any]) -> dict[str, Any]:
    continuity = scene_state.get("continuity_state", {})
    if not isinstance(continuity, Mapping):
        return {"raw": continuity}
    return {
        str(key): value
        for key, value in continuity.items()
        if str(key) != "pronoun_resolutions"
    }


_HARD_SPLIT_EVENTS = frozenset({"none", "death", "birth", "climax", "hero", "high_risk_action", "enter_exit"})


def _action_semantics(scene_state: Mapping[str, Any]) -> tuple[str, set[str], str, str]:
    raw = scene_state.get("action_semantics")
    if not isinstance(raw, Mapping):
        raise CaptionGroupingError("caption visual contract has invalid action_semantics")
    action_key = raw.get("action_key")
    incompatible = raw.get("incompatible_action_keys")
    hard_split_event = raw.get("hard_split_event")
    event_instance_id = raw.get("event_instance_id")
    source_evidence = raw.get("source_evidence")
    if not isinstance(action_key, str) or not action_key.strip():
        raise CaptionGroupingError("caption visual contract has invalid action_semantics.action_key")
    if not isinstance(incompatible, list) or any(not isinstance(item, str) or not item.strip() for item in incompatible):
        raise CaptionGroupingError("caption visual contract has invalid action_semantics.incompatible_action_keys")
    if len(set(incompatible)) != len(incompatible) or action_key in incompatible:
        raise CaptionGroupingError("caption visual contract has contradictory action_semantics.incompatible_action_keys")
    if hard_split_event not in _HARD_SPLIT_EVENTS:
        raise CaptionGroupingError("caption visual contract has invalid action_semantics.hard_split_event")
    if not isinstance(event_instance_id, str) or not event_instance_id.strip():
        raise CaptionGroupingError("caption visual contract has invalid action_semantics.event_instance_id")
    if not isinstance(source_evidence, Mapping) or not isinstance(source_evidence.get("beat"), Mapping) or not isinstance(source_evidence.get("caption"), Mapping):
        raise CaptionGroupingError("caption visual contract has invalid action_semantics.source_evidence")
    return action_key.strip(), set(incompatible), hard_split_event, event_instance_id.strip()


def _contract_split_reasons(previous: Any, following: Any) -> tuple[str, ...]:
    previous_state = getattr(previous, "scene_state", {})
    following_state = getattr(following, "scene_state", {})
    reasons: list[str] = []
    if {
        str(item) for item in previous_state.get("visible_character_ids", [])
    } != {
        str(item) for item in following_state.get("visible_character_ids", [])
    }:
        reasons.append("character_change")
    if str(previous_state.get("location_id", "")).strip() != str(following_state.get("location_id", "")).strip():
        reasons.append("location_change")
    if str(previous_state.get("time_context", "")).strip() != str(following_state.get("time_context", "")).strip():
        reasons.append("time_change")
    previous_key, previous_incompatible, previous_event, previous_event_instance = _action_semantics(previous_state)
    following_key, following_incompatible, following_event, following_event_instance = _action_semantics(following_state)
    if _visual_continuity_state(previous_state) != _visual_continuity_state(following_state):
        reasons.append("continuity_change")
    if previous_event != following_event:
        reasons.append("hard_split_event")
    elif previous_event != "none" and previous_event_instance != following_event_instance:
        reasons.append("event_instance_change")
    if following_key in previous_incompatible or previous_key in following_incompatible:
        reasons.append("declared_incompatible_action_key")
    same_source_context = (
        tuple(getattr(previous, "source_beat_ids", ())) == tuple(getattr(following, "source_beat_ids", ()))
        and str(getattr(previous, "section_id", "")) == str(getattr(following, "section_id", ""))
    )
    if not same_source_context and previous_key != following_key:
        reasons.append("source_beat_change")
    if str(getattr(previous, "narrative_function", "")) != str(getattr(following, "narrative_function", "")):
        reasons.append("narrative_function_change")
    if str(getattr(previous, "visual_mode", "")) != str(getattr(following, "visual_mode", "")):
        reasons.append("visual_mode_change")
    previous_must_show = {str(item.entity_id) for item in getattr(previous, "must_show", ())}
    previous_prohibited = {str(item.entity_id) for item in getattr(previous, "must_not_show_as_primary", ())}
    following_must_show = {str(item.entity_id) for item in getattr(following, "must_show", ())}
    following_prohibited = {str(item.entity_id) for item in getattr(following, "must_not_show_as_primary", ())}
    if previous_must_show & following_prohibited or following_must_show & previous_prohibited:
        reasons.append("must_show_prohibition_conflict")
    return tuple(reasons)


def _image_group(
    group_id: str,
    members: Sequence[tuple[Mapping[str, Any], Any]],
    split_reasons: tuple[str, ...],
) -> CaptionGroup:
    first_caption, first_contract = members[0]
    state = getattr(first_contract, "scene_state", {})
    return CaptionGroup(
        group_id=group_id,
        caption_ids=tuple(str(item.get("caption_id") or item.get("id") or "") for item, _contract in members),
        start=float(first_caption["start"]),
        end=float(members[-1][0]["end"]),
        narrative_function=str(first_contract.narrative_function),
        characters=tuple(str(item) for item in state.get("visible_character_ids", [])),
        location=str(state.get("location_id", "")),
        time_of_day=str(state.get("time_context", "")),
        split_reasons=split_reasons,
        scene_state_signature=_scene_state_signature(first_contract),
        contract_bindings=tuple(
            {
                "caption_id": str(item.get("caption_id") or item.get("id") or ""),
                "caption_visual_contract_sha256": str(contract.content_sha256()),
            }
            for item, contract in members
        ),
        split_from_previous={"required": bool(split_reasons), "reasons": list(split_reasons)},
    )


def derive_caption_image_groups(
    captions: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, Any],
) -> tuple[CaptionGroup, ...]:
    """Derive a metadata-only image group for ordered caption contracts."""

    if not captions:
        raise CaptionGroupingError("cannot group an empty caption timeline")
    resolved: list[tuple[Mapping[str, Any], Any]] = []
    prior_start: float | None = None
    seen: set[str] = set()
    for item in captions:
        member_id = str(item.get("caption_id") or item.get("id") or "")
        if not member_id or member_id in seen:
            raise CaptionGroupingError("caption IDs must be present and unique")
        member_contract = contracts.get(member_id)
        if member_contract is None:
            raise CaptionGroupingError(f"caption {member_id or '?'} has no visual contract")
        _action_semantics(getattr(member_contract, "scene_state", {}))
        start = float(item["start"])
        end = float(item["end"])
        if end <= start or prior_start is not None and start + _EPSILON < prior_start:
            raise CaptionGroupingError(f"caption {member_id} has an invalid timeline position")
        seen.add(member_id)
        prior_start = start
        resolved.append((item, member_contract))

    groups: list[CaptionGroup] = []
    current: list[tuple[Mapping[str, Any], Any]] = [resolved[0]]
    reasons_for_current: tuple[str, ...] = ()
    for following in resolved[1:]:
        reasons = _contract_split_reasons(current[-1][1], following[1])
        if not reasons and float(following[0]["end"]) - float(current[0][0]["start"]) > MAX_IMAGE_GROUP_DURATION + _EPSILON:
            reasons = ("duration_limit",)
        if reasons:
            groups.append(_image_group(f"G{len(groups) + 1:03d}", current, reasons_for_current))
            current = [following]
            reasons_for_current = reasons
        else:
            current.append(following)
    groups.append(_image_group(f"G{len(groups) + 1:03d}", current, reasons_for_current))
    return tuple(groups)


def build_caption_grouping_audit_document(
    *,
    release_id: str,
    captions: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the complete adjacent-boundary audit from computed image groups."""

    groups = derive_caption_image_groups(captions, contracts)
    group_by_caption = {
        caption_id: group
        for group in groups
        for caption_id in group.caption_ids
    }
    ordered_ids = [str(item.get("caption_id") or item.get("id") or "") for item in captions]
    boundaries: list[dict[str, Any]] = []
    for previous_id, following_id in zip(ordered_ids, ordered_ids[1:]):
        previous_group = group_by_caption[previous_id]
        following_group = group_by_caption[following_id]
        split = previous_group.group_id != following_group.group_id
        split_from_previous = following_group.split_from_previous if split else {}
        boundaries.append({
            "previous_caption_id": previous_id,
            "following_caption_id": following_id,
            "decision": "split" if split else "merge",
            "required": bool(split_from_previous.get("required", False)),
            "reasons": list(split_from_previous.get("reasons", [])),
        })
    reason_distribution: dict[str, int] = {}
    for boundary in boundaries:
        for reason in boundary["reasons"]:
            reason_distribution[reason] = reason_distribution.get(reason, 0) + 1
    return {
        "schema_version": "caption-grouping-audit.v1",
        "release_id": str(release_id),
        "caption_count": len(captions),
        "boundary_count": len(boundaries),
        "merge_count": sum(1 for item in boundaries if item["decision"] == "merge"),
        "split_count": sum(1 for item in boundaries if item["decision"] == "split"),
        "required_split_count": sum(1 for item in boundaries if item["required"]),
        "reason_distribution": dict(sorted(reason_distribution.items())),
        "groups": [group.to_dict() for group in groups],
        "boundaries": boundaries,
    }


def build_caption_grouping_from_project(
    root: str | Path,
    *,
    validate_only: bool = False,
) -> Path | dict[str, Any]:
    """Build or validate grouping metadata from the existing audio contracts."""

    from .caption_contract import (
        CaptionContractError,
        CaptionVisualContract,
        build_caption_visual_contract_from_project,
        load_caption_visual_contract_document,
    )

    def current_contracts() -> dict[str, Any]:
        try:
            derived = build_caption_visual_contract_from_project(
                root,
                release_id=str(bindings.get("release_id") or "unknown"),
                validate_only=True,
            )
            return {
                caption_id: CaptionVisualContract.from_mapping(payload)
                for caption_id, payload in derived["contracts"].items()
            }
        except (CaptionContractError, OSError, ValueError) as error:
            raise CaptionGroupingError(
                f"current caption visual contract cannot be derived: {error}"
            ) from error

    root = Path(root)
    bindings_path = root / "04_audio" / "CAPTION_BINDINGS.json"
    contracts_path = root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    try:
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptionGroupingError(f"caption bindings are unreadable: {error}") from error
    raw_captions = bindings.get("captions")
    captions = list(raw_captions.values()) if isinstance(raw_captions, Mapping) else raw_captions
    if not isinstance(captions, list) or not captions:
        raise CaptionGroupingError("CAPTION_BINDINGS.json contains no captions")
    try:
        contracts = load_caption_visual_contract_document(contracts_path)
    except (CaptionContractError, OSError, ValueError) as error:
        if not validate_only:
            raise CaptionGroupingError(f"caption visual contract is unreadable: {error}") from error
        contracts = current_contracts()
    else:
        current = current_contracts()
        stale = set(contracts) != set(current) or any(
            contracts[caption_id].content_sha256() != current[caption_id].content_sha256()
            for caption_id in contracts
        )
        if stale:
            if not validate_only:
                raise CaptionGroupingError("persisted caption visual contract is stale")
            contracts = current
    document = build_caption_grouping_audit_document(
        release_id=str(bindings.get("release_id") or "unknown"),
        captions=captions,
        contracts=contracts,
    )
    if validate_only:
        return document
    output_path = root / "04_audio" / "CAPTION_GROUPING_AUDIT.json"
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _check_unit(unit: CaptionUnit) -> None:
    if not unit.caption_id:
        raise CaptionGroupingError("caption unit is missing caption_id")
    if unit.narrative_function not in NARRATIVE_FUNCTIONS:
        raise CaptionGroupingError(
            f"caption {unit.caption_id} has unknown narrative_function "
            f"{unit.narrative_function!r}; expected one of {NARRATIVE_FUNCTIONS}"
        )
    if unit.end <= unit.start:
        raise CaptionGroupingError(
            f"caption {unit.caption_id} has non-positive duration ({unit.start} -> {unit.end})"
        )


def required_split_reasons(previous: CaptionUnit, following: CaptionUnit) -> tuple[str, ...]:
    """Return every *hard* reason the two captions may not share one image."""

    reasons: list[str] = []
    if set(previous.characters) != set(following.characters):
        reasons.append("character_change")
    if previous.location.strip() != following.location.strip():
        reasons.append("location_change")
    if previous.time_of_day.strip() != following.time_of_day.strip():
        reasons.append("time_change")
    if previous.narrative_function != following.narrative_function:
        reasons.append("narrative_function_change")
    return tuple(reasons)


def group_captions(
    units: Sequence[CaptionUnit] | Iterable[CaptionUnit],
    *,
    max_group_duration: float = DEFAULT_MAX_GROUP_DURATION,
) -> tuple[CaptionGroup, ...]:
    """Split a caption timeline into the coarsest *safe* groups."""

    ordered = list(units)
    if not ordered:
        raise CaptionGroupingError("cannot group an empty caption timeline")
    for unit in ordered:
        _check_unit(unit)
    for previous, following in zip(ordered, ordered[1:]):
        if following.start + _EPSILON < previous.start:
            raise CaptionGroupingError(
                f"caption {following.caption_id} starts before {previous.caption_id}; "
                "the timeline must be sorted"
            )

    groups: list[CaptionGroup] = []
    current: list[CaptionUnit] = [ordered[0]]
    current_reasons: tuple[str, ...] = ()

    def flush(reasons: tuple[str, ...]) -> None:
        head = current[0]
        groups.append(
            CaptionGroup(
                group_id=f"G{len(groups) + 1:03d}",
                caption_ids=tuple(item.caption_id for item in current),
                start=min(item.start for item in current),
                end=max(item.end for item in current),
                narrative_function=head.narrative_function,
                characters=tuple(head.characters),
                location=head.location,
                time_of_day=head.time_of_day,
                split_reasons=reasons,
            )
        )

    for following in ordered[1:]:
        reasons = list(required_split_reasons(current[-1], following))
        if not reasons:
            projected = following.end - min(item.start for item in current)
            if projected > max_group_duration + _EPSILON:
                reasons.append("duration_limit")
        if reasons:
            flush(current_reasons)
            current = [following]
            current_reasons = tuple(reasons)
        else:
            current.append(following)
    flush(current_reasons)
    return tuple(groups)


def validate_caption_groups(
    groups: Sequence[CaptionGroup],
    units: Sequence[CaptionUnit],
) -> None:
    """Fail closed when a hand-authored grouping crosses a hard boundary."""

    if not groups:
        raise CaptionGroupingError("caption grouping is empty")
    table = {unit.caption_id: unit for unit in units}
    seen: set[str] = set()
    for group in groups:
        if not group.caption_ids:
            raise CaptionGroupingError(f"group {group.group_id} has no captions")
        for caption_id in group.caption_ids:
            if caption_id in seen:
                raise CaptionGroupingError(
                    f"caption {caption_id} appears in more than one group"
                )
            seen.add(caption_id)
            if caption_id not in table:
                raise CaptionGroupingError(
                    f"group {group.group_id} references unknown caption {caption_id}"
                )
        members = [table[caption_id] for caption_id in group.caption_ids]
        for previous, following in zip(members, members[1:]):
            reasons = required_split_reasons(previous, following)
            if reasons:
                raise CaptionGroupingError(
                    f"group {group.group_id} merges {previous.caption_id} and "
                    f"{following.caption_id} across a required boundary "
                    f"({', '.join(reasons)}); one image cannot serve both"
                )
    missing = sorted(set(table) - seen)
    if missing:
        raise CaptionGroupingError(
            f"caption grouping does not cover every caption; missing {missing}"
        )


def audit_shot_caption_groups(
    storyboard: Sequence[Mapping[str, Any]],
    units: Sequence[CaptionUnit],
) -> list[dict[str, Any]]:
    """Report every place an existing storyboard merged incompatible captions.

    Used by the regression tooling to measure the defect *before* a fix and to
    prove it is gone *after*. Unknown caption ids are skipped rather than
    raising, so the audit can run against partially migrated projects.
    """

    table = {unit.caption_id: unit for unit in units}
    findings: list[dict[str, Any]] = []
    for shot in storyboard:
        shot_id = str(shot.get("id") or shot.get("shot_id") or "")
        raw_ids = shot.get("captionIds") or shot.get("caption_ids") or []
        members = [table[str(cid)] for cid in raw_ids if str(cid) in table]
        for previous, following in zip(members, members[1:]):
            reasons = required_split_reasons(previous, following)
            if reasons:
                findings.append(
                    {
                        "shot_id": shot_id,
                        "boundary_caption_id": following.caption_id,
                        "previous_caption_id": previous.caption_id,
                        "reasons": list(reasons),
                        "previous_text": previous.text,
                        "boundary_text": following.text,
                    }
                )
    return findings


__all__ = [
    "DEFAULT_MAX_GROUP_DURATION",
    "MAX_IMAGE_GROUP_DURATION",
    "NARRATIVE_FUNCTIONS",
    "SPLIT_REASONS",
    "CaptionGroup",
    "CaptionGroupingError",
    "CaptionSectionRegister",
    "CaptionUnit",
    "assign_caption_registers",
    "audit_shot_caption_groups",
    "build_caption_grouping_audit_document",
    "build_caption_grouping_from_project",
    "derive_caption_image_groups",
    "group_captions",
    "normalize_script_register",
    "required_split_reasons",
    "validate_caption_groups",
]
