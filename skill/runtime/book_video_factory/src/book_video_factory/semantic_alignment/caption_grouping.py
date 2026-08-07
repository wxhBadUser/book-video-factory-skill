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

Two soft triggers cap runaway groups:

``duration_cap``       the group would exceed ``max_group_duration`` seconds.
``caption_count_cap``  the group would exceed ``max_captions_per_group`` captions.

Everything in this module is pure and deterministic: same input, same output,
no clock, no network, no model.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    "duration_cap",
    "caption_count_cap",
)

DEFAULT_MAX_GROUP_DURATION = 12.0
DEFAULT_MAX_CAPTIONS_PER_GROUP = 3

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
    characters: tuple[str, ...] = ()
    location: str = ""
    time_of_day: str = ""
    narrative_function: str = "plot"

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
        return cls(
            caption_id=str(raw.get("caption_id") or raw.get("captionId") or raw.get("id") or ""),
            text=str(raw.get("text", "")),
            start=float(raw.get("start", 0.0)),
            end=float(raw.get("end", 0.0)),
            characters=tuple(str(item) for item in raw.get("characters", ())),
            location=str(raw.get("location", "")),
            time_of_day=str(raw.get("time_of_day") or raw.get("timeOfDay") or ""),
            narrative_function=str(
                raw.get("narrative_function") or raw.get("narrativeFunction") or "plot"
            ),
        )


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "caption_ids": list(self.caption_ids),
            "start": self.start,
            "end": self.end,
            "narrative_function": self.narrative_function,
            "characters": list(self.characters),
            "location": self.location,
            "time_of_day": self.time_of_day,
            "split_reasons": list(self.split_reasons),
        }


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
    max_captions_per_group: int = DEFAULT_MAX_CAPTIONS_PER_GROUP,
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
            if len(current) + 1 > max_captions_per_group:
                reasons.append("caption_count_cap")
            projected = following.end - min(item.start for item in current)
            if projected > max_group_duration + _EPSILON:
                reasons.append("duration_cap")
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
    "DEFAULT_MAX_CAPTIONS_PER_GROUP",
    "DEFAULT_MAX_GROUP_DURATION",
    "NARRATIVE_FUNCTIONS",
    "SPLIT_REASONS",
    "CaptionGroup",
    "CaptionGroupingError",
    "CaptionUnit",
    "audit_shot_caption_groups",
    "group_captions",
    "required_split_reasons",
    "validate_caption_groups",
]
