# -*- coding: utf-8 -*-
"""Visual Hold Planner (M3.1 production layer).

Sits between the Group Visual Contract and the Production Image Task:

    Caption -> Caption Group -> Group Visual Contract
      -> VISUAL HOLD PLANNER      <- this module
      -> Production Image Task -> Scene Image -> Timeline

A Visual Hold Segment may contain one or more ADJACENT Caption Groups.  One
hold == one Production Scene Image == one continuous image hold on the
timeline.  Caption Groups are preserved; they no longer force a 1:1 mapping
to an image.

Responsibilities (pure, deterministic, no network / no model):

1. Discourse subject carryover (referent resolution) for elliptical subject,
   action-subject and dialogue-speaker continuations.
2. Visual Hold boundary classification: single-frame validity test with the
   supported merge types and the hard-split conditions from the M3.1 spec.
3. Per-caption coverage planning (DIRECT / SUPPORTED / SYMBOLIC).
4. Participant constraint resolution across the merged hold window so an
   individual caption with characters=[] cannot erase persistent identities
   required by adjacent continuation captions.
5. Raw-cleanliness pre-render gate and variant selection with the priority
   semantic > identity > participant > raw-cleanliness > visual-quality.
6. Visual Hold Plan document serialization (visual_hold_plan.v1 schema).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

COVERAGE_LEVELS: tuple[str, ...] = ("DIRECT", "SUPPORTED", "SYMBOLIC")
REFERENT_TYPES: tuple[str, ...] = (
    "explicit",
    "pronoun",
    "elliptical_subject",
    "dialogue_speaker",
    "inherited_visual_subject",
)
MERGE_TYPES: tuple[str, ...] = (
    "setup_to_action",
    "elliptical_continuation",
    "same_event_micro_actions",
    "uninterrupted_dialogue",
    "event_to_reflective_summary",
    "consecutive_reflection_action",
)
HARD_SPLIT_REASONS: tuple[str, ...] = (
    "location_change",
    "life_stage_jump",
    "narrative_function_change",
    "alive_vs_death",
    "major_time_jump",
    "framing_proposition_change",
)
RAW_CLEANLINESS_LEVELS: tuple[str, ...] = ("CLEAN", "CONTAMINATED", "UNRESOLVED_VERIFY")
# A story-name mention must resolve to exactly ONE active life-stage instance
# for the current narrative time context (M3.2 rule 2).  C001 = young Fugui
# (flashback era); C002 = current middle-age Fugui.  When both appear in one
# current-time caption the active stage wins and the flashback stage is dropped.
FUGUI_LINEAGE_ACTIVE: dict[str, str] = {"C001": "C002"}
_IMPERSONAL_MARKERS = (
    "枪响", "枪声", "天黑了", "下雨", "人群散了", "战争结束了", "结束了",
    "没吃的", "抢空投", "拆房子", "掘坟", "烧棺材板", "被围", "围困",
    "十来万", "大军", "国军",
)

_DIALOGUE_VERBS = ("喊", "说", "哭喊", "叫道", "喊道", "问", "道")
_VOCATIVE_PREFIXES = ("福贵", "家珍", "凤霞", "有庆", "春生", "龙二", "娘", "爹", "二喜", "苦根")
NAME_TO_ID: dict[str, str] = {
    "福贵": "C002",
    "福贵（中年/老年）": "C002",
    "福贵（青年阔少）": "C001",
    "家珍": "C003",
    "凤霞": "C004",
    "有庆": "C005",
    "龙二": "C006",
    "春生": "C007",
    "娘": "ROLE_MOTHER",
    "二喜": "C008",
    "苦根": "C009",
}

_DEPARTURE_MARKERS = ("进城", "去请", "回家", "离开", "上路", "走了")
_REFLECTIVE_MARKERS = ("要是", "可能", "日子还能过", "好好活", "差点", "要不是", "当年")
_PHYSICAL_MARKERS = ("摸摸", "摸了摸", "摸", "走", "看", "押", "喊", "枪", "回头")
_DEATH_MARKERS = ("死了", "枪毙", "枪响", "被俘", "老全死")


@dataclass(frozen=True)
class ReferentResolution:
    caption_id: str
    type: str
    resolved_character_id: str
    source_caption_id: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_id": self.caption_id,
            "type": self.type,
            "resolved_character_id": self.resolved_character_id,
            "source_caption_id": self.source_caption_id,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class CoveragePlanItem:
    caption_id: str
    intended_coverage: str

    def to_dict(self) -> dict[str, Any]:
        return {"caption_id": self.caption_id, "intended_coverage": self.intended_coverage}


@dataclass(frozen=True)
class VisualHold:
    hold_id: str
    start: float
    end: float
    duration: float
    group_ids: tuple[str, ...]
    caption_ids: tuple[str, ...]
    caption_texts: tuple[str, ...]
    visual_proposition: str
    coverage_plan: tuple[CoveragePlanItem, ...]
    required_character_instances: tuple[str, ...]
    participant_constraint: str
    location: str
    time_context: str
    event_state: str
    merge_reason: str
    referent_resolutions: tuple[ReferentResolution, ...]
    persistent_story_characters: tuple[str, ...] = ()
    anonymous_required_roles: tuple[str, ...] = ()
    background_extras_policy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hold_id": self.hold_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "group_ids": list(self.group_ids),
            "caption_ids": list(self.caption_ids),
            "caption_texts": list(self.caption_texts),
            "visual_proposition": self.visual_proposition,
            "coverage_plan": [item.to_dict() for item in self.coverage_plan],
            "required_character_instances": list(self.required_character_instances),
            "participant_constraint": self.participant_constraint,
            "location": self.location,
            "time_context": self.time_context,
            "event_state": self.event_state,
            "merge_reason": self.merge_reason,
            "referent_resolutions": [r.to_dict() for r in self.referent_resolutions],
            "persistent_story_characters": list(self.persistent_story_characters),
            "anonymous_required_roles": list(self.anonymous_required_roles),
            "background_extras_policy": self.background_extras_policy,
        }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_location(location: str) -> str:
    """Collapse war-zone variants (battlefield_wide -> battlefield)."""
    value = location.strip()
    if value == "battlefield_wide":
        return "battlefield"
    return value


def normalize_group(group: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one caption-group record into the internal feature dict."""
    caption_ids = [str(x) for x in group.get("caption_ids", [])]
    start = float(group.get("start", 0.0))
    end = float(group.get("end", start))
    subjects = list(group.get("primary_subject_ids") or group.get("character_ids") or [])
    location = str(group.get("location") or group.get("environment") or "")
    time_context = str(group.get("time_context") or "")
    narrative_function = str(group.get("narrative_function") or "plot")
    event_state = str(group.get("event") or group.get("event_state") or "")
    event_instance = ""
    hard_split_event = "none"
    event_phase = group.get("event_phase")
    if isinstance(event_phase, Mapping):
        event_instance = str(event_phase.get("event_instance_id") or "")
        hard_split_event = str(event_phase.get("hard_split_event") or "none")
    caption_texts = group.get("caption_texts") or []
    if not caption_texts and "group_caption_text" in group:
        caption_texts = [str(group["group_caption_text"])]
    extra_allowed = bool(group.get("extra_allowed", False))
    extra_kind = [str(x) for x in (group.get("extra_kind") or [])]
    if not extra_kind and extra_allowed:
        # pilot SEGMENT_TASKS may omit explicit extra_kind; infer from location
        if location in ("battlefield", "battlefield_wide"):
            extra_kind = ["soldiers", "starving troops"]
        elif location in ("city_street", "village_execution"):
            extra_kind = ["crowd"]
    return {
        "group_id": str(group.get("group_id", "")),
        "caption_ids": caption_ids,
        "start": start,
        "end": end,
        "primary_subject_ids": subjects,
        "location": _normalize_location(location),
        "raw_location": location,
        "time_context": time_context,
        "narrative_function": narrative_function,
        "event_state": event_state,
        "event_instance": event_instance,
        "hard_split_event": hard_split_event,
        "caption_texts": caption_texts,
        "extra_allowed": extra_allowed,
        "extra_kind": extra_kind,
        "raw": dict(group),
    }


def load_caption_contracts(contract_path: str | Path) -> dict[str, Mapping[str, Any]]:
    """Load the on-disk Caption Visual Contract (v2, dict keyed by caption_id)."""
    raw = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    contracts = raw.get("contracts", raw)
    if not isinstance(contracts, Mapping):
        raise ValueError("caption visual contract must be a mapping keyed by caption_id")
    return dict(contracts)


# ---------------------------------------------------------------------------
# 1. Discourse subject carryover
# ---------------------------------------------------------------------------

def _caption_text(contract: Mapping[str, Any]) -> str:
    return str(contract.get("caption_text") or contract.get("text") or "")


def _explicit_subjects(contract: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for item in contract.get("subjects", []):
        if isinstance(item, Mapping):
            cid = str(item.get("character_id") or item.get("entity_id") or "")
            if cid:
                out.append(cid)
        else:
            out.append(str(item))
    scene_state = contract.get("scene_state") or {}
    for cid in scene_state.get("visible_character_ids", []) or []:
        if cid and cid not in out:
            out.append(str(cid))
    return out


def _pronoun_resolutions(contract: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    scene_state = contract.get("scene_state") or {}
    continuity = scene_state.get("continuity_state") or {}
    return list(continuity.get("pronoun_resolutions", []) or [])


def _introduces_dialogue(text: str) -> bool:
    return any(verb in text for verb in _DIALOGUE_VERBS) and ("：" in text or ":" in text)


def _vocative_in(text: str) -> str | None:
    for name in _VOCATIVE_PREFIXES:
        if name in text:
            return name
    return None


def _is_counterfactual(text: str) -> bool:
    return any(k in text for k in ("要是", "要不是", "可能", "当年", "差点"))


def _is_reflective(text: str) -> bool:
    return any(k in text for k in _REFLECTIVE_MARKERS)


def _is_physical(text: str) -> bool:
    return any(k in text for k in _PHYSICAL_MARKERS)


def _is_observer_establishment(text: str) -> bool:
    return "去看" in text or "也去看" in text


_MASS_SCALE_MARKERS = ("十来万", "大军", "国军", "围困", "方圆", "千军万马")


def _is_mass_scale(text: str) -> bool:
    return any(k in text for k in _MASS_SCALE_MARKERS)


def _is_impersonal_or_collective(text: str) -> bool:
    """True when the predicate is an ambient / impersonal / collective event
    that does NOT require a human actor (M3.2 rule 3).

    Examples: 枪响了五声 / 天黑了 / 下雨了 / 人群散了 / 战争结束了 /
    没吃的，抢空投，拆房子，掘坟烧棺材板 / 十来万国军被围…
    """
    return any(k in text for k in _IMPERSONAL_MARKERS)


def _to_id(value: str) -> str:
    """Map a Chinese name / ambiguous token to a canonical character id."""
    return NAME_TO_ID.get(value, value)


def resolve_discourse_referents(
    ordered_captions: Sequence[Mapping[str, Any]],
) -> dict[str, list[ReferentResolution]]:
    """Resolve implicit subjects/speakers for a caption sequence.

    Tracks an ``action_subject`` (implicit sentence subject carried through
    elliptical continuations), a ``recent_explicit`` list (most recent explicit
    characters first) and pending dialogue speaker/addressee.  Handles the
    three hard continuation cases:

    * elliptical subject:   凤霞一年前发了一场高烧， / 再不会说话，也听不见了。
    * action subject:       他摸摸自己的脸， / 摸摸自己的胳膊，都是好好的。
    * dialogue speaker:     他回过头，哭着喊：福贵， / 我是替你去死啊。

    The speaker of a quoted-speech introduction is resolved to the most recent
    explicit character that is NOT the vocative (the contract sometimes
    resolves the speech pronoun to the vocative instead of the actor).
    Fail-closed: a continuation whose subject cannot be recovered is left
    unresolved (no invented subject).
    """
    result: dict[str, list[ReferentResolution]] = {}
    action_subject: str | None = None
    recent_explicit: list[str] = []
    pending_speaker: str | None = None
    pending_addressee: str | None = None
    prev_cid: str | None = None
    prev_text: str = ""

    def add(cid: str, rtype: str, rid: str, source: str, evidence: str) -> None:
        rid = _to_id(rid)
        result.setdefault(cid, []).append(ReferentResolution(
            caption_id=cid, type=rtype, resolved_character_id=rid,
            source_caption_id=source, evidence=evidence,
        ))

    for contract in ordered_captions:
        cid = str(contract.get("caption_id") or contract.get("id") or "")
        text = _caption_text(contract)
        explicit = _explicit_subjects(contract)
        pron = _pronoun_resolutions(contract)

        # M3.2 rule 2: a story-name mention must resolve to exactly ONE active
        # life-stage instance per current-time caption.  When a lineage has
        # several stages in one caption (e.g. contract lists both C001 young
        # Fugui and C002 middle Fugui), keep the active stage only.
        raw_explicit_ids = [_to_id(x) for x in explicit]
        active_ids = list(raw_explicit_ids)
        for variant, active in FUGUI_LINEAGE_ACTIVE.items():
            if variant in active_ids and active in active_ids:
                active_ids = [cid for cid in active_ids if cid != variant]
        explicit_entries = [x for x, rid in zip(explicit, raw_explicit_ids) if rid in active_ids]

        resolved: list[str] = list(active_ids)
        for item in explicit_entries:
            add(cid, "explicit", item, cid, "caption explicitly names character")

        for item in pron:
            rid = str(item.get("resolved_entity_id") or "")
            basis = str(item.get("resolution_basis") or {}).replace("{", "").replace("}", "")
            if rid:
                add(cid, "pronoun", rid, str(item.get("upstream_caption_id") or cid),
                    "pronoun_resolution %s (%s)" % (item.get("pronoun_resolution"), basis))
                if _to_id(rid) not in resolved:
                    resolved.append(_to_id(rid))

        # elliptical / action-subject continuation (no explicit subject and not
        # a mass-scale wide shot whose subject is an anonymous collective, and
        # not an impersonal/collective event that requires no human actor)
        if not explicit and not _is_mass_scale(text) and not _is_impersonal_or_collective(text):
            if action_subject is not None:
                add(cid, "elliptical_subject", action_subject,
                    prev_cid or "", "subject omitted in dependent/continuation clause; inherited from preceding caption")
                if action_subject not in resolved:
                    resolved.append(action_subject)

        # dialogue speaker continuation: previous caption introduced speech
        if _introduces_dialogue(prev_text):
            if pending_speaker is not None:
                add(cid, "dialogue_speaker", pending_speaker,
                    prev_cid or "", "quoted continuation inherits the introducing caption's speaker")
                if pending_speaker not in resolved:
                    resolved.append(pending_speaker)
            if pending_addressee is not None:
                add(cid, "dialogue_speaker", pending_addressee,
                    prev_cid or "", "vocative addressee from introducing caption")
                if pending_addressee not in resolved:
                    resolved.append(pending_addressee)

        # dialogue introducing caption: speaker = most recent explicit character
        # that is NOT the vocative; fall back to the carried action subject.
        if _introduces_dialogue(text):
            vocative = _vocative_in(text)
            voc_id = _to_id(vocative) if vocative else None
            speaker = None
            if voc_id is not None:
                for rid in recent_explicit:
                    if rid != voc_id:
                        speaker = rid
                        break
            if speaker is None:
                speaker = action_subject
            if speaker is not None:
                add(cid, "dialogue_speaker", speaker,
                    prev_cid or "", "speech-act subject = most recent non-vocative actor")
                if speaker not in resolved:
                    resolved.append(speaker)
            if voc_id is not None:
                add(cid, "dialogue_speaker", voc_id,
                    prev_cid or "", "vocative addressee named in the introducing caption")
                if voc_id not in resolved:
                    resolved.append(voc_id)
            pending_speaker = speaker
            pending_addressee = voc_id
        else:
            pending_speaker = None
            pending_addressee = None

        # update running state
        if explicit and not _introduces_dialogue(text):
            action_subject = _to_id(explicit[0])
        if not _introduces_dialogue(text):
            for item in pron:
                rid = str(item.get("resolved_entity_id") or "")
                if rid and item.get("pronoun_resolution") in ("他", "她", "我", "自己"):
                    rid2 = _to_id(rid)
                    if rid2 in resolved:
                        action_subject = rid2
                        break
        for rid in resolved:
            if rid in recent_explicit:
                recent_explicit.remove(rid)
            recent_explicit.insert(0, rid)

        result.setdefault(cid, result.get(cid, []))
        prev_cid = cid
        prev_text = text

    return result



# ---------------------------------------------------------------------------
# 2. Single-frame validity: merge classification
# ---------------------------------------------------------------------------

def _life_stage_signature(group: Mapping[str, Any]) -> str:
    return ",".join(sorted(group["primary_subject_ids"]))


def _life_stage_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """True when a recurring character's registered life stage jumps between
    adjacent groups (e.g. middle-aged Fugui becoming elderly).  Cast changes
    alone are NOT a life-stage jump."""
    stages = (left.get("character_stages") or {})
    if not stages:
        return False
    for cid in set(left["primary_subject_ids"]) & set(right["primary_subject_ids"]):
        ls = stages.get(cid)
        rs = stages.get(cid)
        if ls and rs and ls != rs:
            return True
    return False
def _is_death_state(group: Mapping[str, Any]) -> bool:
    raw_state = group.get("raw", {}).get("visual_event_state") or {}
    if isinstance(raw_state, Mapping):
        predicate = str(raw_state.get("action_predicate") or "")
        if "death" in predicate or "aftermath" in predicate:
            return True
    state = str(group.get("event_state") or "")
    joined = "".join(group.get("caption_texts", []))
    if "death" in state:
        return True
    return any(k in joined for k in _DEATH_MARKERS)


def _is_departure_action(group: Mapping[str, Any]) -> bool:
    joined = "".join(group.get("caption_texts", []))
    return any(k in joined for k in _DEPARTURE_MARKERS)


def _is_observer_establishment_group(group: Mapping[str, Any]) -> bool:
    joined = "".join(group.get("caption_texts", []))
    return _is_observer_establishment(joined)


def _group_text(group: Mapping[str, Any]) -> str:
    return "".join(group.get("caption_texts", []))


def classify_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[bool, str, str]:
    """Decide whether one static frame can serve both adjacent groups.

    Returns (allowed, merge_type_or_empty, reason).  Hard-split conditions are
    checked first; supported merge types from the M3.1 spec are then detected.
    ``left``/``right`` must carry ``resolved_subjects`` (see plan_visual_holds).
    """
    lsub = set(left.get("resolved_subjects", left["primary_subject_ids"]))
    rsub = set(right.get("resolved_subjects", right["primary_subject_ids"]))
    ltext = _group_text(left)
    rtext = _group_text(right)
    lloc = left["location"]
    rloc = right["location"]

    # ---- hard splits ---------------------------------------------------
    if lloc and rloc and lloc != rloc:
        departure_ok = (_is_departure_action(right) and (lsub & rsub)
                        and not _is_death_state(right))
        if not departure_ok:
            return False, "", "location_change"
    if _life_stage_conflict(left, right):
        return False, "", "life_stage_jump"
    if left["narrative_function"] != right["narrative_function"]:
        return False, "", "narrative_function_change"
    if _is_death_state(left) and not _is_death_state(right):
        if not (_is_observer_establishment_group(right) and lloc and lloc == rloc):
            return False, "", "alive_vs_death"
    if _is_death_state(right) and not _is_death_state(left):
        return False, "", "alive_vs_death"
    if _is_counterfactual(rtext) and _is_physical(ltext) and not _is_reflective(ltext):
        return False, "", "major_time_jump"
    # an observer-establishment group ("福贵也去看") ends the wide setup hold;
    # it may merge backward with the watched event but never forward into a new
    # framing (the close interaction).
    if _is_observer_establishment_group(left):
        return False, "", "framing_proposition_change"
    # a mass/wide shot must not merge with a character-interaction shot; but two
    # anonymous mass-action groups (siege + scavenging) may share one frame.
    lexp = set(left["primary_subject_ids"])
    rexp = set(right["primary_subject_ids"])
    lmass = _is_mass_scale(ltext)
    rmass = _is_mass_scale(rtext)
    if (lexp or rexp) and lmass != rmass:
        return False, "", "framing_proposition_change"

    # ---- merge types ---------------------------------------------------
    if _is_departure_action(right) and (lsub & rsub):
        return True, "setup_to_action", "setup state followed by the same protagonist's departure/action"
    if right["event_instance"] and right["event_instance"] == left["event_instance"]:
        return True, "elliptical_continuation", "same event instance; dependent clause continuation"
    if lloc and lloc == rloc:
        if (set(left["primary_subject_ids"]) == set(right["primary_subject_ids"])
                and not (_is_death_state(left) != _is_death_state(right))):
            return True, "same_event_micro_actions", "continuous situation; same subjects at same location"
        if (lsub & rsub) and _is_reflective(rtext) and not _is_physical(ltext):
            return True, "event_to_reflective_summary", "same subject & location; reflective summary hold"
        if lsub & rsub:
            return True, "consecutive_reflection_action", "same subject & location; continuous reflection/action"
    if _is_observer_establishment_group(right) and _is_death_state(left) and lloc and lloc == rloc:
        return True, "same_event_micro_actions", "observer watches the same death/execution event at the same location"
    if left["caption_texts"] and _introduces_dialogue(left["caption_texts"][-1]):
        return True, "uninterrupted_dialogue", "quoted speech continues across the group boundary"
    return False, "", "no_merge_type_matches"


def _attach_resolved_subjects(groups: Sequence[Mapping[str, Any]],
                              referents: Mapping[str, Sequence[ReferentResolution]] | None) -> None:
    for g in groups:
        g["life_stage"] = _life_stage_signature(g)
        g["resolved_subjects"] = list(g["primary_subject_ids"])
    if referents:
        for g in groups:
            for cid in g["caption_ids"]:
                for r in referents.get(cid, []):
                    if r.resolved_character_id not in g["resolved_subjects"]:
                        g["resolved_subjects"].append(r.resolved_character_id)


def propose_holds(
    groups: Sequence[Mapping[str, Any]],
    referents: Mapping[str, Sequence[ReferentResolution]] | None = None,
) -> list[list[str]]:
    """Greedy single-frame-validity merge over adjacent groups."""
    normalized = [normalize_group(g) for g in groups]
    _attach_resolved_subjects(normalized, referents)
    holds: list[list[str]] = []
    current: list[dict[str, Any]] = []
    for g in normalized:
        if current and classify_merge(current[-1], g)[0]:
            current.append(g)
        else:
            if current:
                holds.append([item["group_id"] for item in current])
            current = [g]
    if current:
        holds.append([item["group_id"] for item in current])
    return holds


def validate_hold_boundaries(
    groups: Sequence[Mapping[str, Any]],
    holds: Sequence[Mapping[str, Any]],
    referents: Mapping[str, Sequence[ReferentResolution]] | None = None,
) -> list[str]:
    """Return hard-split violations / structural findings for a hold plan."""
    findings: list[str] = []
    by_id = {g["group_id"]: normalize_group(g) for g in groups}
    _attach_resolved_subjects(list(by_id.values()), referents)
    seen: set[str] = set()
    prev_end: float | None = None
    for hold in holds:
        hold = {"group_ids": list(hold)} if isinstance(hold, (list, tuple)) else hold
        gids = list(hold.get("group_ids", []))
        if not gids:
            findings.append("hold %s with empty group_ids" % hold.get("hold_id", "?"))
            continue
        for gid in gids:
            if gid in seen:
                findings.append("group %s appears more than once" % gid)
            seen.add(gid)
            if gid not in by_id:
                findings.append("unknown group %s" % gid)
        ordered = [by_id[gid] for gid in gids if gid in by_id]
        start = float(hold.get("start") or (ordered[0]["start"] if ordered else 0.0))
        end = float(hold.get("end") or (ordered[-1]["end"] if ordered else start))
        if prev_end is not None and start < prev_end - 1e-6:
            findings.append("hold %s start %.3f overlaps previous end %.3f (VTT timeline altered)" % (
                hold.get("hold_id"), start, prev_end))
        # gaps between holds are legitimate VTT data (no caption spoken); the
        # plan faithfully mirrors group boundaries and never rewrites them.
        prev_end = end
        for a, b in zip(ordered, ordered[1:]):
            allowed, _, reason = classify_merge(a, b)
            if not allowed:
                findings.append("hold %s crosses hard split between %s and %s: %s" % (
                    hold.get("hold_id"), a["group_id"], b["group_id"], reason))
    missing = [g["group_id"] for g in groups if g["group_id"] not in seen]
    if missing:
        findings.append("groups missing from plan: %s" % ",".join(missing))
    return findings


# ---------------------------------------------------------------------------
# 3. Coverage planning
# ---------------------------------------------------------------------------

def build_coverage_plan(
    hold: VisualHold,
    captions: Mapping[str, Mapping[str, Any]],
    referents: Mapping[str, Sequence[ReferentResolution]],
) -> tuple[CoveragePlanItem, ...]:
    items: list[CoveragePlanItem] = []
    for idx, cid in enumerate(hold.caption_ids):
        label = "DIRECT" if idx == 0 else "SUPPORTED"
        items.append(CoveragePlanItem(caption_id=cid, intended_coverage=label))
    return tuple(items)


# ---------------------------------------------------------------------------
# 4. Participant constraint resolution across the hold window
# ---------------------------------------------------------------------------

def resolve_hold_participant_policy(
    hold: VisualHold,
    groups: Sequence[Mapping[str, Any]],
    character_register: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """M3.2 rule 4: separate persistent story characters, anonymous required
    roles and the background-extras policy.  A mass/crowd scene must NOT be
    told "only these characters may appear"; exact persistent-character
    identity enforcement stays strict where it applies."""
    by_id = {g["group_id"]: normalize_group(g) for g in groups}
    members = [by_id[gid] for gid in hold.group_ids if gid in by_id]
    ids: list[str] = []
    for g in members:
        for cid in g["primary_subject_ids"]:
            if cid and cid not in ids and not str(cid).startswith(("ROLE_", "LOCATION_")):
                ids.append(cid)
    for r in hold.referent_resolutions:
        rid = r.resolved_character_id
        if rid and rid not in ids and not str(rid).startswith(("ROLE_", "LOCATION_")):
            ids.append(rid)
    # one character = one active life-stage instance per hold
    for variant, active in FUGUI_LINEAGE_ACTIVE.items():
        if variant in ids and active in ids:
            ids = [cid for cid in ids if cid != variant]

    # anonymous required roles + extras policy from the hold window
    anonymous: list[str] = []
    extras_policy = "forbidden"
    any_mass = any(_is_mass_scale("".join(g["caption_texts"])) for g in members)
    any_crowd = any("crowd" in (g.get("extra_kind") or []) or g.get("extra_allowed") for g in members)
    for g in members:
        for role in (g.get("extra_kind") or []):
            role = str(role)
            if role and role not in anonymous:
                anonymous.append(role)
    if any_mass:
        extras_policy = "required"
        for role in ("soldiers", "starving troops"):
            if role not in anonymous:
                anonymous.append(role)
    elif any_crowd:
        extras_policy = "crowd_allowed"
    else:
        extras_policy = "forbidden"

    register = character_register or {}
    parts = ["%s%s" % (cid, (" (%s)" % register[cid]) if cid in register else "") for cid in ids]
    if extras_policy == "forbidden":
        constraint = (
            "persistent_story_characters: %s；画面中只允许出现以上指定人物，"
            "不得出现无关路人/额外人物；人物必须保持各自 life-stage（禁止加龄/换龄/换脸）。"
            % ("、".join(parts) if parts else "无")
        )
    elif extras_policy == "crowd_allowed":
        constraint = (
            "persistent_story_characters: %s；anonymous_required_roles: %s；"
            "background_extras_policy: crowd_allowed（背景群众可出现但弱于主体）；"
            "指定人物身份与 life-stage 严格保持。"
            % ("、".join(parts) if parts else "无", "、".join(anonymous) if anonymous else "无")
        )
    else:
        constraint = (
            "persistent_story_characters: %s（如画面需要，可弱化/虚化处理）；"
            "anonymous_required_roles: %s；background_extras_policy: required（群戏必需）；"
            "避免把精确人数约束套到战争/刑场群戏。"
            % ("、".join(parts) if parts else "无（可选）", "、".join(anonymous))
        )
    return {
        "persistent_story_characters": tuple(ids),
        "anonymous_required_roles": tuple(anonymous),
        "background_extras_policy": extras_policy,
        "participant_constraint": constraint,
    }


def resolve_hold_participants(
    hold: VisualHold,
    groups: Sequence[Mapping[str, Any]],
    character_register: Mapping[str, str] | None = None,
) -> tuple[tuple[str, ...], str]:
    """Backward-compatible wrapper: returns (persistent_ids, constraint)."""
    policy = resolve_hold_participant_policy(hold, groups, character_register)
    return policy["persistent_story_characters"], policy["participant_constraint"]


# ---------------------------------------------------------------------------
# 5. Raw cleanliness gate + variant selection
# ---------------------------------------------------------------------------

def detect_uniform_black_border(
    image_path: str | Path,
    max_border_ratio: float = 0.02,
) -> dict[str, Any]:
    """Detect a uniform black letterbox border on a frame (M3.2 rule 5).

    Returns {top, bottom, left, right, border_ratio, verdict} where verdict is
    CLEAN (no meaningful border), NORMALIZABLE (border <= max ratio), or
    RAW_CONTAMINATION (border too large for a mere letterbox)."""
    from PIL import Image
    import numpy as np
    img = Image.open(image_path).convert("RGB")
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    dark = (arr.sum(axis=2) < 24)
    top = 0
    while top < h and dark[top, :].mean() > 0.98:
        top += 1
    bottom = 0
    while bottom < h and dark[h - 1 - bottom, :].mean() > 0.98:
        bottom += 1
    left = 0
    while left < w and dark[:, left].mean() > 0.98:
        left += 1
    right = 0
    while right < w and dark[:, w - 1 - right].mean() > 0.98:
        right += 1
    # per-side max band (the user's ~16px letterbox = one band per side); a
    # single band <= max_border_ratio of the frame is a normalizable letterbox,
    # anything larger is a contamination bar.
    band_ratios = [top / max(h, 1), bottom / max(h, 1), left / max(w, 1), right / max(w, 1)]
    border_ratio = max(band_ratios)
    if border_ratio <= 1e-6:
        verdict = "CLEAN"
    elif border_ratio <= max_border_ratio:
        verdict = "NORMALIZABLE"
    else:
        verdict = "RAW_CONTAMINATION"
    return {"top": top, "bottom": bottom, "left": left, "right": right,
            "border_ratio": round(float(border_ratio), 4), "verdict": verdict}


def normalize_frame_hygiene(
    image_path: str | Path,
    out_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    max_border_ratio: float = 0.02,
) -> dict[str, Any]:
    """Deterministic pre-render normalization.

    - uniform black border <= max ratio: crop the border and scale back to the
      exact canvas (1920x1080).
    - border > max ratio: return RAW_CONTAMINATION (caller must BLOCK).
    Records source_sha + normalized_sha.
    """
    import hashlib
    from PIL import Image
    src = Path(image_path)
    raw = src.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    probe = detect_uniform_black_border(src, max_border_ratio=max_border_ratio)
    if probe["verdict"] == "RAW_CONTAMINATION":
        return {"verdict": "RAW_CONTAMINATION", "source_sha256": source_sha,
                "reason": "uniform black border %.2f%% exceeds 2%% threshold" % (probe["border_ratio"] * 100)}
    img = Image.open(src).convert("RGB")
    w, h = img.size
    top, bottom, left, right = probe["top"], probe["bottom"], probe["left"], probe["right"]
    if top or bottom or left or right:
        box = (left, top, max(left, w - right), max(top, h - bottom))
        img = img.crop(box)
    # scale/crop back to the exact canvas, preserving aspect
    scale = max(width / img.width, height / img.height)
    nw, nh = round(img.width * scale), round(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    cx = (nw - width) // 2
    cy = (nh - height) // 2
    img = img.crop((cx, cy, cx + width, cy + height))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    normalized_sha = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "verdict": "NORMALIZED",
        "source_sha256": source_sha,
        "normalized_sha256": normalized_sha,
        "detected_border": probe,
        "out_path": str(out),
    }


def gate_scene_image_cleanliness(level: str) -> tuple[bool, str]:
    """Pre-render gate: contaminated/unverified raw must not enter render."""
    if level == "CLEAN":
        return True, "clean raw allowed"
    if level in ("CONTAMINATED", "UNRESOLVED_VERIFY"):
        return False, "contaminated/unverified raw blocked from render (raw-cleanliness gate)"
    return False, "unknown cleanliness level %r" % (level,)


def continuity_reference_eligible(
    prev_hold: VisualHold,
    hold: VisualHold,
    groups: Sequence[Mapping[str, Any]],
) -> bool:
    """M3.2 rule (F6/F7): continuity reference is OFF normally and becomes
    eligible ONLY for:
      same persistent character + same event instance + adjacent visual holds."""
    if not prev_hold or not hold:
        return False
    by_id = {g["group_id"]: normalize_group(g) for g in groups}
    prev_ids = set(prev_hold.required_character_instances)
    cur_ids = set(hold.required_character_instances)
    if not (prev_ids & cur_ids):
        return False
    # adjacency: prev_hold ends before/at hold start and groups are consecutive
    if prev_hold.end > hold.start + 1e-6:
        return False
    prev_last = by_id.get(prev_hold.group_ids[-1]) if prev_hold.group_ids else None
    cur_first = by_id.get(hold.group_ids[0]) if hold.group_ids else None
    if prev_last is None or cur_first is None:
        return False
    # same event instance OR same location+event state (continuous situation)
    if cur_first["event_instance"] and cur_first["event_instance"] == prev_last["event_instance"]:
        return True
    if (prev_last["location"] and prev_last["location"] == cur_first["location"]
            and prev_last["event_state"] and prev_last["event_state"] == cur_first["event_state"]):
        return True
    return False


def build_hold_reference_pack(
    hold: VisualHold,
    holds: Sequence[VisualHold],
    groups: Sequence[Mapping[str, Any]],
    identity_roots: Mapping[str, str],
    style_master: str,
    selected_frames: Mapping[str, str],
) -> dict[str, Any]:
    """Build the reference pack for one hold's Production Image Task (F8).

    Priority: IDENTITY ROOT > CONTINUITY REFERENCE.
    A continuity reference may only control event-local appearance / hair /
    wardrobe / bindings / scene continuity, never canonical identity.
    """
    refs = [{"role": "style_master", "kind": "style_master", "file": style_master}]
    for cid in hold.required_character_instances:
        if cid in identity_roots:
            refs.append({"role": "identity_master", "kind": "identity_root",
                         "character_id": cid, "file": identity_roots[cid]})
    continuity = None
    for idx, other in enumerate(holds):
        if other.hold_id == hold.hold_id:
            break
        if other.end > hold.start:
            continue
        if continuity_reference_eligible(other, hold, groups):
            if other.hold_id in selected_frames:
                continuity = {"role": "continuity_reference", "kind": "continuity_reference",
                              "source_hold_id": other.hold_id, "file": selected_frames[other.hold_id]}
                break
    if continuity is not None:
        refs.append(continuity)
    return {
        "hold_id": hold.hold_id,
        "reference_pack": refs,
        "continuity_reference": continuity,
        "continuity_priority": "identity_root > continuity_reference",
        "continuity_scope": "event-local appearance / hair / wardrobe / bindings / scene continuity / current facial presentation",
    }


def select_preferred_variant(
    variants: Sequence[Mapping[str, Any]],
    *,
    semantic: Sequence[str] | None = None,
    identity: Sequence[str] | None = None,
    participant: Sequence[str] | None = None,
    cleanliness: Sequence[str] | None = None,
    quality: Sequence[str] | None = None,
) -> tuple[Mapping[str, Any] | None, str]:
    """Pick the best variant with priority:

    semantic > identity > participant > raw cleanliness > visual quality."""
    if not variants:
        return None, "no variants"
    keys = ("semantic", "identity", "participant", "cleanliness", "quality")
    scores = {
        "semantic": list(semantic or [""] * len(variants)),
        "identity": list(identity or [""] * len(variants)),
        "participant": list(participant or [""] * len(variants)),
        "cleanliness": list(cleanliness or ["CLEAN"] * len(variants)),
        "quality": list(quality or [""] * len(variants)),
    }
    best: int | None = None
    for idx in range(len(variants)):
        ok = True
        for key in keys:
            val = scores[key][idx]
            if key == "cleanliness":
                allowed, _ = gate_scene_image_cleanliness(val)
                if not allowed:
                    ok = False
                    break
            elif val == "FAIL":
                ok = False
                break
        if not ok:
            continue
        if best is None:
            best = idx
            continue
        for key in keys:
            a = scores[key][idx]
            b = scores[key][best]
            if a != b:
                if _better(a, b):
                    best = idx
                break
    if best is None:
        return None, "no variant passes the priority gate"
    return variants[best], "selected variant %s" % str(variants[best].get("file", best))


def _better(a: str, b: str) -> bool:
    order = {"PASS": 2, "CLEAN": 2, "": 1, "FAIL": 0, "CONTAMINATED": 0, "UNRESOLVED_VERIFY": 0}
    return order.get(a, 1) > order.get(b, 1)


# ---------------------------------------------------------------------------
# 6. Plan document builder + main entry
# ---------------------------------------------------------------------------

def build_visual_hold_plan_document(
    *,
    groups: Sequence[Mapping[str, Any]],
    holds: Sequence[VisualHold],
    release_id: str,
    source_grouping_sha256: str = "",
    approved: bool = False,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": "visual-hold-plan.v1",
        "release_id": release_id,
        "source_grouping_sha256": source_grouping_sha256,
        "approved": approved,
        "hold_count": len(holds),
        "group_count": len(groups),
        "notes": list(notes),
        "holds": [h.to_dict() for h in holds],
    }


def plan_visual_holds(
    *,
    groups: Sequence[Mapping[str, Any]],
    captions: Mapping[str, Mapping[str, Any]],
    approved_holds: Sequence[Mapping[str, Any]] | None = None,
    character_register: Mapping[str, str] | None = None,
    release_id: str = "r2",
    hold_prefix: str = "H",
) -> tuple[dict[str, Any], list[str]]:
    """Main entry: produce the Visual Hold Plan document."""
    normalized = [normalize_group(g) for g in groups]
    _attach_resolved_subjects(normalized, None)
    if character_register:
        for g in normalized:
            g["character_stages"] = {
                cid: character_register[cid]
                for cid in g["primary_subject_ids"]
                if cid in character_register
            }
    ordered_captions = [
        dict(captions[cid]) for g in normalized for cid in g["caption_ids"] if cid in captions
    ]
    referents = resolve_discourse_referents(ordered_captions)
    _attach_resolved_subjects(normalized, referents)

    if approved_holds is not None:
        normalized_approved = [
            {"group_ids": list(h)} if isinstance(h, (list, tuple)) else dict(h)
            for h in approved_holds
        ]
        boundaries = [list(h.get("group_ids", [])) for h in normalized_approved]
    else:
        boundaries = propose_holds(normalized, referents)

    by_id = {g["group_id"]: g for g in normalized}
    holds: list[VisualHold] = []
    for idx, gids in enumerate(boundaries):
        if not gids:
            continue
        members = [by_id[gid] for gid in gids if gid in by_id]
        start = min(m["start"] for m in members)
        end = max(m["end"] for m in members)
        caption_ids = tuple(cid for m in members for cid in m["caption_ids"])
        caption_texts = tuple(t for m in members for t in m["caption_texts"])
        approved_mapping = normalized_approved[idx] if approved_holds is not None else {}
        merge_reason = str(approved_mapping.get("merge_reason") or "")
        if not merge_reason and len(members) > 1:
            _allowed, _type, _reason = classify_merge(members[-2], members[-1])
            merge_reason = ("%s: %s" % (_type, _reason)) if _allowed else "approved-hold"
        location = str(approved_mapping.get("location") or members[0]["raw_location"] or members[0]["location"])
        time_context = str(approved_mapping.get("time_context") or members[0]["time_context"] or "")
        event_state = str(approved_mapping.get("event_state") or members[0]["event_state"] or "")
        hold = VisualHold(
            hold_id=str(approved_mapping.get("hold_id") or ("%s%02d" % (hold_prefix, idx + 1))),
            start=start,
            end=end,
            duration=round(end - start, 3),
            group_ids=tuple(gids),
            caption_ids=caption_ids,
            caption_texts=caption_texts,
            visual_proposition=str(approved_mapping.get("visual_proposition") or ""),
            coverage_plan=(),
            required_character_instances=(),
            participant_constraint="",
            location=location,
            time_context=time_context,
            event_state=event_state,
            merge_reason=merge_reason,
            referent_resolutions=tuple(r for cid in caption_ids for r in referents.get(cid, [])),
        )
        hold = VisualHold(
            hold_id=hold.hold_id, start=hold.start, end=hold.end, duration=hold.duration,
            group_ids=hold.group_ids, caption_ids=hold.caption_ids, caption_texts=hold.caption_texts,
            visual_proposition=hold.visual_proposition,
            coverage_plan=build_coverage_plan(hold, captions, referents),
            required_character_instances=(),
            participant_constraint="",
            location=hold.location, time_context=hold.time_context, event_state=hold.event_state,
            merge_reason=hold.merge_reason, referent_resolutions=hold.referent_resolutions,
        )
        policy = resolve_hold_participant_policy(hold, normalized, character_register)
        hold = VisualHold(
            hold_id=hold.hold_id, start=hold.start, end=hold.end, duration=hold.duration,
            group_ids=hold.group_ids, caption_ids=hold.caption_ids, caption_texts=hold.caption_texts,
            visual_proposition=hold.visual_proposition, coverage_plan=hold.coverage_plan,
            required_character_instances=policy["persistent_story_characters"],
            participant_constraint=policy["participant_constraint"],
            location=hold.location, time_context=hold.time_context, event_state=hold.event_state,
            merge_reason=hold.merge_reason, referent_resolutions=hold.referent_resolutions,
            persistent_story_characters=policy["persistent_story_characters"],
            anonymous_required_roles=policy["anonymous_required_roles"],
            background_extras_policy=policy["background_extras_policy"],
        )
        holds.append(hold)

    findings = validate_hold_boundaries(normalized, [h.to_dict() for h in holds], referents)
    document = build_visual_hold_plan_document(
        groups=normalized,
        holds=holds,
        release_id=release_id,
        approved=approved_holds is not None,
        notes=findings,
    )
    return document, findings


__all__ = [
    "COVERAGE_LEVELS",
    "REFERENT_TYPES",
    "MERGE_TYPES",
    "HARD_SPLIT_REASONS",
    "RAW_CLEANLINESS_LEVELS",
    "CoveragePlanItem",
    "ReferentResolution",
    "VisualHold",
    "build_coverage_plan",
    "build_hold_reference_pack",
    "build_visual_hold_plan_document",
    "classify_merge",
    "continuity_reference_eligible",
    "detect_uniform_black_border",
    "gate_scene_image_cleanliness",
    "normalize_frame_hygiene",
    "load_caption_contracts",
    "normalize_group",
    "plan_visual_holds",
    "propose_holds",
    "resolve_discourse_referents",
    "resolve_hold_participant_policy",
    "resolve_hold_participants",
    "select_preferred_variant",
    "validate_hold_boundaries",
]
