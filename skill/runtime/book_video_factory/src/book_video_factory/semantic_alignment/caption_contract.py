"""The Caption Visual Contract: the single source of truth for what an image must show.

Background
----------
Every previous attempt at "A/V semantic alignment" failed because the image was
decided by three disconnected signals that never agreed:

* the audio caption text (what the viewer reads),
* an Agent's temporary ``semantic_rationale`` (often boilerplate),
* the ``requiredEntities`` extracted from a different abstraction layer.

None of them was *authoritative*. A caption "凤霞出嫁那天" could be illustrated
by "老人与牛在田里" because nothing forced the image to prove it depicted 凤霞
and the wedding.

This module makes the **Caption Visual Contract** the only authoritative record
of what each caption requires on screen. It is derived once, from the *locked*
production chain -- the approved script sections (which carry the real
``narrative_function``) and the Phase-2 beats (which carry the real
``requiredEntities``) -- and every later stage (grouping, storyboard, visual
proposition, prompt, image task, semantic review, render gate) reads from it.

Fail-closed (per the remediation spec):
  * A caption that cannot be bound to a real beat/section is rejected -- there
    is no silent ``narrative_function = "plot"`` default and no empty
    ``subjects = []`` default.
  * ``narrative_function`` is propagated exactly from the script section; it is
    never re-defaulted to ``plot`` mid-pipeline.
  * The contract hash (``contract_sha256``) is bound into the prompt, the image
    task and the render gate, so an edited caption invalidates every downstream
    artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .caption_grouping import NARRATIVE_FUNCTIONS, normalize_script_register

# Plot / concrete-register captions must be drawn LITERAL: the frame must show
# the named people and the named event. Reflective registers may be symbolic /
# abstract, but only through the established symbol registry.
_LITERAL_FUNCTIONS = {"opening", "plot"}
_SYMBOLIC_ALLOWED_FUNCTIONS = {"theory", "author_background", "transition", "closing"}
_HARD_SPLIT_EVENTS = frozenset({"none", "death", "birth", "climax", "hero", "high_risk_action", "enter_exit"})


class CaptionContractError(RuntimeError):
    """A caption cannot be given a defensible visual contract."""


def _validated_action_semantics(raw: Any, *, caption_id: str) -> dict[str, Any]:
    """Validate the required, source-evidenced action compatibility declaration."""

    if not isinstance(raw, Mapping):
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics")
    action_key = raw.get("action_key")
    incompatible = raw.get("incompatible_action_keys")
    hard_split_event = raw.get("hard_split_event")
    event_instance_id = raw.get("event_instance_id")
    source_evidence = raw.get("source_evidence")
    if not isinstance(action_key, str) or not action_key.strip():
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics.action_key")
    if not isinstance(incompatible, list) or any(not isinstance(item, str) or not item.strip() for item in incompatible):
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics.incompatible_action_keys")
    if len(set(incompatible)) != len(incompatible) or action_key in incompatible:
        raise CaptionContractError(f"contract for {caption_id} has contradictory action_semantics.incompatible_action_keys")
    if hard_split_event not in _HARD_SPLIT_EVENTS:
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics.hard_split_event")
    if not isinstance(event_instance_id, str) or not event_instance_id.strip():
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics.event_instance_id")
    if not isinstance(source_evidence, Mapping) or not isinstance(source_evidence.get("beat"), Mapping) or not isinstance(source_evidence.get("caption"), Mapping):
        raise CaptionContractError(f"contract for {caption_id} has invalid action_semantics.source_evidence")
    return {
        "action_key": action_key.strip(),
        "incompatible_action_keys": list(incompatible),
        "hard_split_event": hard_split_event,
        "event_instance_id": event_instance_id.strip(),
        "source_evidence": {"beat": dict(source_evidence["beat"]), "caption": dict(source_evidence["caption"])},
    }


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _short_name(full_natural_language: str, fallback: str) -> str:
    """Reduce an anchor's descriptive natural-language to a short referent name.

    Anchors store "30-70岁江南农民，晒黑皮肤皱纹深，穿粗布短褂…"; the contract
    needs the *name* ("福贵"), not the whole description. The leading character
    before a separator is the name.
    """

    head = re.split(r"[，,、。；;：:·\s（(【\[]", _norm(full_natural_language).strip(), maxsplit=1)[0].strip()
    return head or str(fallback).strip()


def _entity_display(entity_id: str, name_maps: Iterable[Mapping[str, str]]) -> str:
    """Resolve an entity id (C002 / OBJ_BOOK / SCENE_FIELD) to a human name."""

    for table in name_maps:
        candidate = table.get(str(entity_id))
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    # character/object anchors often carry a descriptive natural_language; the
    # short name is the leading token. If the anchor maps don't cover this id,
    # fall back to the locked alias table so the contract still names the
    # referent readably instead of leaking a raw id like "OBJ_OX".
    alias = _alias_display(str(entity_id))
    if alias:
        return alias
    return str(entity_id)


def _alias_display(entity_id: str) -> str:
    """Longest spoken alias registered for an entity id (e.g. OBJ_OX -> 牛)."""

    aliases = _ENTITY_ALIASES.get(str(entity_id))
    if aliases:
        return max(aliases, key=len)
    return ""


@dataclass(frozen=True)
class CaptionEntityEvidence:
    """One visual entity, with the caption evidence that permits its use."""

    entity_id: str
    natural_language: str
    reason: str
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "natural_language": self.natural_language,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CaptionEntityEvidence":
        if not isinstance(raw, Mapping):
            raise CaptionContractError("caption entity evidence must be a mapping")
        entity_id = raw.get("entity_id")
        natural_language = raw.get("natural_language")
        reason = raw.get("reason")
        evidence = raw.get("evidence")
        if not all(isinstance(value, str) and value.strip() for value in (entity_id, natural_language, reason)):
            raise CaptionContractError("caption entity evidence requires entity_id, natural_language, and reason")
        if not isinstance(evidence, Mapping) or not evidence:
            raise CaptionContractError(f"caption entity evidence for {entity_id!r} requires nonempty evidence")
        return cls(
            entity_id=entity_id.strip(),
            natural_language=natural_language.strip(),
            reason=reason.strip(),
            evidence=dict(evidence),
        )


@dataclass(frozen=True)
class CaptionVisualContract:
    """The authoritative record of what one display caption demands on screen.

    Every field below is derived from the locked script section + beat that the
    caption covers. The contract is the only legal input to the visual
    proposition, prompt and review stages.
    """

    caption_id: str
    caption_text: str
    caption_text_sha256: str
    section_id: str
    source_beat_ids: tuple[str, ...]
    narrative_function: str

    subjects: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    location: str = ""
    time_context: str = ""
    story_objects: tuple[str, ...] = ()

    scene_state: Mapping[str, Any] = field(default_factory=dict)
    must_show: tuple[CaptionEntityEvidence, ...] = ()
    may_show: tuple[CaptionEntityEvidence, ...] = ()
    must_not_show_as_primary: tuple[CaptionEntityEvidence, ...] = ()

    visual_focus: str = ""
    visual_mode: str = "literal"
    semantic_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_id": self.caption_id,
            "caption_text": self.caption_text,
            "caption_text_sha256": self.caption_text_sha256,
            "section_id": self.section_id,
            "source_beat_ids": list(self.source_beat_ids),
            "narrative_function": self.narrative_function,
            "subjects": list(self.subjects),
            "actions": list(self.actions),
            "location": self.location,
            "time_context": self.time_context,
            "story_objects": list(self.story_objects),
            "scene_state": dict(self.scene_state),
            "must_show": [item.to_dict() for item in self.must_show],
            "may_show": [item.to_dict() for item in self.may_show],
            "must_not_show_as_primary": [item.to_dict() for item in self.must_not_show_as_primary],
            "visual_focus": self.visual_focus,
            "visual_mode": self.visual_mode,
            "semantic_signature": self.semantic_signature,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CaptionVisualContract":
        if not isinstance(raw, Mapping):
            raise CaptionContractError("caption visual contract record must be a mapping")
        text = str(raw.get("caption_text", ""))
        text_hash = raw.get("caption_text_sha256")
        if not isinstance(text_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", text_hash or ""):
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has no valid caption_text_sha256")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != text_hash:
            raise CaptionContractError(f"contract caption_text_sha256 does not match caption_text for {raw.get('caption_id')}")
        section_id = raw.get("section_id")
        source_beat_ids = raw.get("source_beat_ids")
        scene_state = raw.get("scene_state")
        if not isinstance(section_id, str) or not section_id.strip():
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has no section_id")
        if not isinstance(source_beat_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in source_beat_ids
        ):
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has invalid source_beat_ids")
        if not isinstance(scene_state, Mapping) or not {
            "visible_character_ids", "location_id", "time_context", "action_state", "continuity_state"
        }.issubset(scene_state):
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has invalid scene_state")
        if "action_semantics" not in scene_state:
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has missing action_semantics")
        normalized_scene_state = dict(scene_state)
        normalized_scene_state["action_semantics"] = _validated_action_semantics(
            scene_state["action_semantics"], caption_id=str(raw.get("caption_id", ""))
        )
        nf = raw.get("narrative_function")
        if nf not in NARRATIVE_FUNCTIONS:
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has invalid narrative_function {nf!r}")
        return cls(
            caption_id=str(raw.get("caption_id", "")),
            caption_text=text,
            caption_text_sha256=text_hash,
            section_id=section_id.strip(),
            source_beat_ids=tuple(source_beat_ids),
            narrative_function=str(nf),
            subjects=tuple(str(item) for item in raw.get("subjects", []) if str(item).strip()),
            actions=tuple(str(item) for item in raw.get("actions", []) if str(item).strip()),
            location=str(raw.get("location", "")),
            time_context=str(raw.get("time_context", "")),
            story_objects=tuple(str(item) for item in raw.get("story_objects", []) if str(item).strip()),
            scene_state=normalized_scene_state,
            must_show=tuple(CaptionEntityEvidence.from_mapping(item) for item in raw.get("must_show", [])),
            may_show=tuple(CaptionEntityEvidence.from_mapping(item) for item in raw.get("may_show", [])),
            must_not_show_as_primary=tuple(
                CaptionEntityEvidence.from_mapping(item)
                for item in raw.get("must_not_show_as_primary", [])
            ),
            visual_focus=str(raw.get("visual_focus", "")),
            visual_mode=str(raw.get("visual_mode", "literal")),
            semantic_signature=str(raw.get("semantic_signature", "")),
        )

    def content_sha256(self) -> str:
        """Stable hash over the contract's semantic content (not its own hash)."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_referent_lexicon(entity_name_maps: Iterable[Mapping[str, str]]) -> list[tuple[str, str, str]]:
    """Build a (term, entity_id, kind) lexicon for scanning caption text.

    ``kind`` is ``"char"`` for character ids, ``"obj"`` for ``OBJ_`` ids and
    ``"scene"`` for ``SCENE_`` ids. The leading short name of each anchor's
    natural language is used as the matchable term (so "福贵", "豆子", "田野"
    match), not the whole descriptive paragraph.
    """

    lexicon: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for table in entity_name_maps:
        for entity_id, name in table.items():
            entity_id = str(entity_id)
            kind = (
                "char"
                if not (entity_id.startswith("OBJ_") or entity_id.startswith("SCENE_"))
                else ("obj" if entity_id.startswith("OBJ_") else "scene")
            )
            term = _short_name(name, entity_id).lower()
            key = (term, entity_id)
            if term and key not in seen:
                seen.add(key)
                lexicon.append((term, entity_id, kind))
    # Longest terms first so "凤霞" wins over a single character.
    lexicon.sort(key=lambda item: -len(item[0]))
    return lexicon


def _scan_caption_terms(
    caption_text: str, lexicon: Sequence[tuple[str, str, str]]
) -> dict[str, tuple[str, str]]:
    """Return ``entity_id -> (kind, matched_term)`` for terms found in the caption."""

    norm = _norm(caption_text)
    found: dict[str, tuple[str, str]] = {}
    if not norm:
        return found
    for term, entity_id, kind in lexicon:
        if not term:
            continue
        if term in norm and entity_id not in found:
            found[entity_id] = (kind, term)
    return found


def _best_beat_for_caption(
    caption_text: str,
    beats: Sequence[Mapping[str, Any]],
    section_text_by_id: Mapping[str, str],
) -> dict[str, Any] | None:
    """Bind a caption to the locked beat whose cue best overlaps its text.

    A caption is either an exact cue, a fragment of a cue, or a cue expanded
    into a longer line. We accept any mutual substring and prefer the SHORTEST
    matching cue (the most specific beat) so a caption is not accidentally
    bound to a long section-level beat that merely shares a clause.
    """

    norm = _norm(caption_text)
    if not norm:
        return None
    matches: list[dict[str, Any]] = []
    best_cue_len: int | None = None
    for beat in beats:
        cue = _norm(beat.get("cue", ""))
        if not cue:
            continue
        if norm in cue or cue in norm:
            cue_len = len(cue)
            if best_cue_len is None or cue_len < best_cue_len:
                best_cue_len = cue_len
                matches = [dict(beat)]
            elif cue_len == best_cue_len:
                matches.append(dict(beat))
    if not matches:
        return None
    beat_ids = {str(item.get("beatId") or item.get("id") or "") for item in matches}
    section_ids = {str(item.get("sectionId") or item.get("section_id") or "") for item in matches}
    functions = {
        normalize_script_register(str(item.get("narrative_function") or item.get("narrativeFunction")))
        if item.get("narrative_function") or item.get("narrativeFunction") else None
        for item in matches
    }
    if len(beat_ids) != 1 or len(section_ids) != 1 or len(functions) != 1:
        raise CaptionContractError(
            "ambiguous beat binding: equally specific cues do not resolve one Beat ID, section ID, and narrative_function"
        )
    return matches[0]


# Locked domain referents for this production: entity id -> spoken aliases that
# may appear in a caption. The lexicon scans caption text for these so the
# contract's must_show is derived from what the caption actually NAMES, not only
# from the beat's required-entity ids (which a buggy storyboard can get wrong).
_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "C001": ("福贵",),
    "C002": ("福贵",),
    "C003": ("家珍",),
    "C004": ("凤霞",),
    "C005": ("有庆",),
    "C006": ("龙二",),
    "C007": ("春生",),
    "C008": ("二喜",),
    "C009": ("苦根",),
    "C010": ("老牛", "牛"),
    "OBJ_BEANS": ("豆子", "豆"),
    "OBJ_BOOK": (),
    "OBJ_BRIDAL": ("嫁衣", "花轿", "出嫁", "婚"),
    "OBJ_NEEDLE": ("针", "注射器"),
    "OBJ_DICE": ("骰子",),
    "OBJ_GRAVE": ("坟", "墓"),
    "SCENE_FIELD": ("田野", "田", "田埂"),
    "SCENE_VILLAGE": ("村", "村口"),
    "SCENE_STREET": ("街", "镇", "青石板"),
    "SCENE_GAMBLING": ("赌", "赌场"),
    "SCENE_HOSPITAL": ("医院", "产房"),
    "SCENE_EXECUTION": ("刑场", "枪毙"),
    "SCENE_MOONLIGHT": ("月光", "月光照"),
    "SCENE_DUSK": ("黄昏", "晒场"),
    "SCENE_WHARF": ("码头",),
    # Real-project entity vocabulary seen in shipped beats (Huozhe / 活着).
    "OBJ_OX": ("牛", "耕牛"),
    "OBJ_LAMP": ("煤油灯", "灯"),
    "OBJ_TREE": ("树",),
    "SCENE_NIGHT": ("夜", "夜晚"),
    "SCENE_SCHOOL": ("私塾", "学校"),
    "SCENE_WAR": ("战场", "战争"),
    "SCENE_WEDDING": ("婚礼", "出嫁"),
}


_CAPTION_LOCAL_ROLES: dict[str, tuple[str, str, str]] = {
    "父亲": ("ROLE_FATHER", "father", "male"),
    "爹": ("ROLE_FATHER", "father", "male"),
    "母亲": ("ROLE_MOTHER", "mother", "female"),
    "娘": ("ROLE_MOTHER", "mother", "female"),
    "妻子": ("ROLE_WIFE", "wife", "female"),
    "女儿": ("ROLE_DAUGHTER", "daughter", "female"),
    "儿子": ("ROLE_SON", "son", "male"),
    "少爷": ("ROLE_YOUNG_MASTER", "young_master", "male"),
    "老爷": ("ROLE_MASTER", "master", "male"),
    "医生": ("ROLE_DOCTOR", "doctor", ""),
    "护士": ("ROLE_NURSE", "nurse", ""),
    "县长": ("ROLE_COUNTY_MAGISTRATE", "county_magistrate", ""),
    "孩子": ("ROLE_CHILD", "child", ""),
}


_CAPTION_LOCAL_GROUPS: dict[str, tuple[str, str]] = {
    "村里人": ("GROUP_VILLAGERS", "villagers"),
}


_UPSTREAM_ROLE_GROUPS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "doctor": ("GROUP_DOCTORS", "doctors", ("SCENE_HOSPITAL",)),
    "nurse": ("GROUP_NURSES", "nurses", ("SCENE_HOSPITAL",)),
}


_PROFILE_GENDER_MARKERS: dict[str, tuple[str, ...]] = {
    "female": ("女子", "女人", "女孩", "少女", "新娘"),
    "male": ("男子", "男人", "男孩", "少年", "少爷"),
}


_CONTEXTUAL_ENTITY_TERMS: dict[str, tuple[str, ...]] = {
    "OBJ_BOOK": ("书", "照片", "书桌"),
}


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[，,。．.；;：:！!？?、\s]+", _norm(text))
    return [p for p in parts if len(p) >= 2]


def _entity_kind(entity_id: str) -> str:
    if entity_id.startswith("GROUP_"):
        return "group"
    if entity_id.startswith("OBJ_"):
        return "obj"
    if entity_id.startswith("SCENE_"):
        return "scene"
    return "char"


def _build_referent_lexicon(
    entity_name_maps: Iterable[Mapping[str, str]],
) -> list[tuple[str, str, str]]:
    """Build a (term, entity_id, kind) lexicon from aliases + provided name maps.

    The locked ``_ENTITY_ALIASES`` supply the spoken terms a caption may use;
    the caller's name maps supply display names and any extra aliases. Longest
    terms are matched first so "凤霞" wins over a single character.
    """

    lexicon: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity_id, aliases in _ENTITY_ALIASES.items():
        kind = _entity_kind(entity_id)
        for alias in aliases:
            term = _norm(alias).lower()
            key = (term, entity_id)
            if term and key not in seen:
                seen.add(key)
                lexicon.append((term, entity_id, kind))
    for table in entity_name_maps:
        for entity_id, name in table.items():
            entity_id = str(entity_id)
            for term in _name_terms(name, entity_id):
                key = (term, entity_id)
                if term and key not in seen:
                    seen.add(key)
                    lexicon.append((term, entity_id, _entity_kind(entity_id)))
    lexicon.sort(key=lambda item: -len(item[0]))
    return lexicon


def _scan_caption_terms(
    caption_text: str, lexicon: Sequence[tuple[str, str, str]]
) -> dict[str, tuple[str, str]]:
    norm = _norm(caption_text)
    found: dict[str, tuple[str, str]] = {}
    if not norm:
        return found
    for term, entity_id, kind in lexicon:
        if not term:
            continue
        if term in norm and entity_id not in found:
            found[entity_id] = (kind, term)
    return found


def _entity_match_terms(entity_id: str, entity_name_maps: Iterable[Mapping[str, str]]) -> tuple[str, ...]:
    """Return all spoken terms that can directly name one locked entity."""

    terms = list(_ENTITY_ALIASES.get(entity_id, ()))
    for names in entity_name_maps:
        name = names.get(entity_id)
        if isinstance(name, str) and name.strip():
            terms.extend(_name_terms(name, entity_id))
    return tuple(dict.fromkeys(_norm(term).lower() for term in terms if _norm(term).strip()))


def _name_terms(name: str, fallback: str) -> tuple[str, ...]:
    """Extract literal, source-supplied aliases, including a book title in 《》."""

    text = _norm(name).strip()
    terms = [_short_name(text, fallback)]
    for title in re.findall(r"《([^》]+)》", text):
        terms.extend((f"《{title}》", title))
    return tuple(dict.fromkeys(term.lower() for term in terms if term.strip()))


def _direct_caption_entities(
    caption_text: str,
    *,
    lexicon: Sequence[tuple[str, str, str]],
    entity_name_maps: Iterable[Mapping[str, str]],
    required_entities: Sequence[str],
) -> dict[str, tuple[str, str]]:
    """Find explicit caption names, preferring the bound beat's entity ids.

    Names such as ``福贵`` can legitimately be aliases for multiple continuity
    identities. When a locked beat supplies one candidate, it disambiguates the
    spoken term; the beat still cannot add an entity that the caption did not
    name.
    """

    normalized = _norm(caption_text).lower()
    found: dict[str, tuple[str, str]] = {}
    claimed_terms: set[str] = set()
    for entity_id in required_entities:
        entity_id = str(entity_id)
        terms = (*_entity_match_terms(entity_id, entity_name_maps), *_CONTEXTUAL_ENTITY_TERMS.get(entity_id, ()))
        for term in dict.fromkeys(terms):
            if term in normalized:
                found[entity_id] = (_entity_kind(entity_id), term)
                claimed_terms.add(term)
                break
    for entity_id, (kind, term) in _scan_caption_terms(caption_text, lexicon).items():
        if term.lower() not in claimed_terms and entity_id not in found:
            found[entity_id] = (kind, term)
    return found


def _caption_span(caption_text: str, term: str) -> dict[str, Any]:
    """Return the exact normalized-text span for a directly matched entity term."""

    normalized_text = _norm(caption_text)
    normalized_term = _norm(term)
    start = normalized_text.lower().find(normalized_term.lower())
    if start < 0:
        raise CaptionContractError(f"caption evidence term {term!r} is absent from its caption text")
    return {"start": start, "end": start + len(normalized_term), "text": normalized_text[start:start + len(normalized_term)]}


def _caption_clauses(caption_text: str, *, end: int | None = None) -> list[dict[str, Any]]:
    """Split locked caption text on punctuation while retaining normalized spans."""

    normalized = _norm(caption_text)
    limit = len(normalized) if end is None else min(end, len(normalized))
    return [
        {"start": match.start(), "end": match.end(), "text": match.group(0)}
        for match in re.finditer(r"[^,，;；.。:：!?！？]+", normalized[:limit])
    ]


def _select_clause_candidates(
    caption_text: str,
    candidates: Sequence[tuple[str, str, str]],
    *,
    end: int | None = None,
) -> tuple[dict[str, Any], list[tuple[str, str, str]]] | None:
    """Return the latest clause containing explicit candidates, never a later entity tie-break."""

    for clause in reversed(_caption_clauses(caption_text, end=end)):
        clause_text = str(clause["text"]).lower()
        matches = [
            candidate for candidate in candidates
            if candidate[2] and candidate[2].lower() in clause_text
        ]
        if matches:
            return clause, matches
    return None


def _candidate_span_in_clause(clause: Mapping[str, Any], term: str) -> dict[str, Any]:
    """Return the exact term span within an already selected clause."""

    text = str(clause["text"])
    start = text.lower().find(_norm(term).lower())
    if start < 0:
        raise CaptionContractError(f"caption clause does not contain antecedent term {term!r}")
    absolute_start = int(clause["start"]) + start
    return {"start": absolute_start, "end": absolute_start + len(term), "text": text[start:start + len(term)]}


def _caption_local_roles(caption_text: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Return explicit kinship/status mentions without assigning a persistent identity."""

    roles: list[tuple[str, str, dict[str, Any]]] = []
    normalized = _norm(caption_text)
    seen: set[str] = set()
    for term, (role_id, role, gender) in sorted(_CAPTION_LOCAL_ROLES.items(), key=lambda item: -len(item[0])):
        if role_id in seen or term not in normalized:
            continue
        evidence: dict[str, Any] = {
            "caption_local": True,
            "caption_span": _caption_span(caption_text, term),
            "entity_kind": "character",
            "role": role,
            "text_evidence": term,
        }
        if gender:
            evidence["gender"] = gender
        roles.append((role_id, term, evidence))
        seen.add(role_id)
    return roles


def _caption_local_groups(caption_text: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Return explicit, non-persistent plural groups from the current caption."""

    groups: list[tuple[str, str, dict[str, Any]]] = []
    normalized = _norm(caption_text)
    for term, (group_id, group) in _CAPTION_LOCAL_GROUPS.items():
        if term not in normalized:
            continue
        groups.append((group_id, term, {
            "caption_local": True,
            "caption_span": _caption_span(caption_text, term),
            "entity_kind": "character_group",
            "group": group,
            "number": "plural",
            "text_evidence": term,
        }))
    return groups


def _upstream_role_group_candidates(
    prior_captions: Sequence[tuple[str, str, str, tuple[tuple[str, str, str], ...]]],
    *,
    section_id: str,
    scene_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Return same-section role groups supported by explicit prior captions only."""

    candidates: dict[str, dict[str, Any]] = {}
    for upstream_caption_id, upstream_section_id, upstream_text, _entities in prior_captions:
        if upstream_section_id != section_id:
            continue
        for role_id, term, evidence in _caption_local_roles(upstream_text):
            role = str(evidence["role"])
            group = _UPSTREAM_ROLE_GROUPS.get(role)
            if group is None:
                continue
            group_id, group_category, compatible_scenes = group
            if not scene_ids.intersection(compatible_scenes):
                continue
            candidate = candidates.setdefault(
                group_id,
                {
                    "group": group_category,
                    "role": role,
                    "term": term,
                    "supporting_upstream_captions": [],
                },
            )
            candidate["supporting_upstream_captions"].append({
                "caption_id": upstream_caption_id,
                "caption_text": upstream_text,
                "caption_span": dict(evidence["caption_span"]),
            })
    return candidates


def _is_plural_group_candidate(pronoun: str, kind: str) -> bool:
    """Only masculine-or-mixed 他们 can use the currently modelled neutral groups."""

    return pronoun == "他们" and kind == "group"


def _pronoun_occurrences(caption_text: str) -> list[tuple[str, int, int]]:
    """Return plural-first pronoun matches in textual order with normalized spans."""

    normalized = _norm(caption_text)
    return [
        (match.group(0), match.start(), match.end())
        for match in re.finditer(r"他们|她们|他|她|它", normalized)
    ]


def _gender_conflicts(pronoun: str, gender: Any) -> bool:
    """Whether explicit metadata contradicts a gendered singular pronoun."""

    return (
        (pronoun == "他" and gender == "female")
        or (pronoun == "她" and gender == "male")
        or (pronoun == "她们" and gender == "male")
    )


def _gender_exclusions(
    pronoun: str,
    entities: Sequence[tuple[str, str, str]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return auditable exclusions caused by explicit gender evidence only."""

    exclusions: list[dict[str, Any]] = []
    for entity_id, kind, _term in entities:
        if kind != "char":
            continue
        declared = metadata.get(entity_id, {})
        gender = declared.get("gender") if isinstance(declared, Mapping) else None
        if not _gender_conflicts(pronoun, gender):
            continue
        item: dict[str, Any] = {"entity_id": entity_id, "gender": gender}
        if isinstance(declared.get("gender_evidence"), Mapping):
            item["gender_evidence"] = dict(declared["gender_evidence"])
        exclusions.append(item)
    return sorted(exclusions, key=lambda item: str(item["entity_id"]))


def _pronoun_candidates(
    pronoun: str,
    entities: Sequence[tuple[str, str, str]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return only structurally compatible direct mentions from one caption."""

    candidates: list[tuple[str, str, str]] = []
    for entity_id, kind, term in entities:
        declared = metadata.get(entity_id, {})
        declared_type = declared.get("entity_type") if isinstance(declared, Mapping) else None
        declared_gender = declared.get("gender") if isinstance(declared, Mapping) else None
        if pronoun in {"他", "她"} and kind == "char":
            if _gender_conflicts(pronoun, declared_gender):
                continue
            candidates.append((entity_id, kind, term))
        elif _is_plural_group_candidate(pronoun, kind):
            candidates.append((entity_id, kind, term))
        elif pronoun == "它" and (kind != "char" or declared_type is not None):
            candidates.append((entity_id, kind, term))
    return candidates


def _apply_explicit_gender_match_precedence(
    pronoun: str,
    candidates: Mapping[str, tuple[str, str]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, tuple[str, str]], list[str], list[dict[str, Any]]]:
    """Prefer explicit gender matches without treating unknown gender as a match."""

    expected_gender = {"他": "male", "她": "female"}.get(pronoun)
    if expected_gender is None:
        return dict(candidates), [], []
    explicit_matches: dict[str, tuple[str, str]] = {}
    unknown_candidates: list[str] = []
    match_evidence: list[dict[str, Any]] = []
    for entity_id, candidate in candidates.items():
        kind, _term = candidate
        if kind != "char":
            continue
        declared = metadata.get(entity_id, {})
        gender = declared.get("gender") if isinstance(declared, Mapping) else None
        if gender == expected_gender:
            explicit_matches[entity_id] = candidate
            evidence: dict[str, Any] = {"entity_id": entity_id, "gender": gender}
            if isinstance(declared.get("gender_evidence"), Mapping):
                evidence["gender_evidence"] = dict(declared["gender_evidence"])
            match_evidence.append(evidence)
        elif gender is None:
            unknown_candidates.append(entity_id)
    if explicit_matches:
        return explicit_matches, sorted(unknown_candidates), sorted(match_evidence, key=lambda item: str(item["entity_id"]))
    return dict(candidates), [], []


def _validate_pronoun_metadata(
    *,
    caption_id: str,
    pronoun: str,
    entity_id: str,
    kind: str,
    metadata: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject structured type or gender evidence that contradicts the pronoun."""

    declared = metadata.get(entity_id, {})
    if not isinstance(declared, Mapping):
        raise CaptionContractError(f"entity metadata for {entity_id} must be a mapping")
    declared_type = declared.get("entity_type")
    declared_gender = declared.get("gender")
    if pronoun in {"他", "她", "他们", "她们"}:
        if declared_type is not None and declared_type != "person":
            raise CaptionContractError(
                f"caption {caption_id} pronoun {pronoun!r} type mismatch for {entity_id}: {declared_type!r}"
            )
        if pronoun == "他" and declared_gender is not None and declared_gender != "male":
            raise CaptionContractError(
                f"caption {caption_id} pronoun {pronoun!r} gender mismatch for {entity_id}: {declared_gender!r}"
            )
        if pronoun == "她" and declared_gender is not None and declared_gender != "female":
            raise CaptionContractError(
                f"caption {caption_id} pronoun {pronoun!r} gender mismatch for {entity_id}: {declared_gender!r}"
            )
    elif kind == "char" and (declared_type is None or declared_type == "person"):
        raise CaptionContractError(
            f"caption {caption_id} contains unresolved pronoun {pronoun!r}; "
            f"{entity_id} lacks trusted non-person entity_type metadata"
        )


def _entity_evidence(
    entity_id: str,
    *,
    natural_language: str,
    reason: str,
    evidence: Mapping[str, Any],
) -> CaptionEntityEvidence:
    return CaptionEntityEvidence(
        entity_id=str(entity_id),
        natural_language=natural_language,
        reason=reason,
        evidence=dict(evidence),
    )


def _derive_action_semantics(
    *,
    caption: Mapping[str, Any],
    caption_id: str,
    section_id: str,
    beat: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Derive grouping semantics solely from locked Beat and caption metadata."""

    beat = beat or {}
    beat_id = str(beat.get("beatId") or beat.get("id") or "").strip()
    raw_risk_flags = beat.get("riskFlags", [])
    if not isinstance(raw_risk_flags, list) or any(not isinstance(item, str) or not item.strip() for item in raw_risk_flags):
        raise CaptionContractError(f"caption {caption_id} has invalid Beat riskFlags")
    risk_flags = [item.strip() for item in raw_risk_flags]
    raw_high_risk = beat.get("highRisk", False)
    if not isinstance(raw_high_risk, bool):
        raise CaptionContractError(f"caption {caption_id} has invalid Beat highRisk")
    generation_mode = str(beat.get("generationMode") or "").strip()

    raw_caption_event = caption.get("event_evidence", {})
    if raw_caption_event is None:
        raw_caption_event = {}
    if not isinstance(raw_caption_event, Mapping):
        raise CaptionContractError(f"caption {caption_id} has invalid event_evidence")
    explicit_event = raw_caption_event.get("hard_split_event")
    if explicit_event is not None and explicit_event not in _HARD_SPLIT_EVENTS - {"none"}:
        raise CaptionContractError(f"caption {caption_id} has invalid event_evidence.hard_split_event")
    beat_event = beat.get("hardSplitEvent")
    if beat_event is not None and beat_event not in _HARD_SPLIT_EVENTS - {"none"}:
        raise CaptionContractError(f"caption {caption_id} has invalid Beat hardSplitEvent")
    risk_event = "climax" if "death_climax" in risk_flags else "high_risk_action" if raw_high_risk or risk_flags else "none"
    hard_split_event = str(explicit_event or beat_event or risk_event)
    if explicit_event and beat_event and explicit_event != beat_event:
        raise CaptionContractError(f"caption {caption_id} caption event evidence conflicts with Beat hardSplitEvent")

    raw_action_key = raw_caption_event.get("action_key")
    if raw_action_key is not None and (not isinstance(raw_action_key, str) or not raw_action_key.strip()):
        raise CaptionContractError(f"caption {caption_id} has invalid event_evidence.action_key")
    action_key = str(raw_action_key).strip() if raw_action_key else (
        f"event:{hard_split_event}:{beat_id or section_id}" if hard_split_event != "none" else "same_scene_sequence"
    )
    raw_incompatible = raw_caption_event.get("incompatible_action_keys", [])
    if not isinstance(raw_incompatible, list) or any(not isinstance(item, str) or not item.strip() for item in raw_incompatible):
        raise CaptionContractError(f"caption {caption_id} has invalid event_evidence.incompatible_action_keys")
    incompatible_action_keys = [item.strip() for item in raw_incompatible]
    if len(set(incompatible_action_keys)) != len(incompatible_action_keys) or action_key in incompatible_action_keys:
        raise CaptionContractError(f"caption {caption_id} has contradictory event_evidence.incompatible_action_keys")
    raw_instance_id = raw_caption_event.get("event_instance_id")
    if raw_instance_id is not None and (not isinstance(raw_instance_id, str) or not raw_instance_id.strip()):
        raise CaptionContractError(f"caption {caption_id} has invalid event_evidence.event_instance_id")
    event_instance_id = str(raw_instance_id).strip() if raw_instance_id else (
        f"beat:{beat_id}" if hard_split_event != "none" else f"sequence:{beat_id or section_id}"
    )
    raw_shot_ids = caption.get("shot_ids", [])
    if not isinstance(raw_shot_ids, list) or any(not isinstance(item, str) or not item.strip() for item in raw_shot_ids):
        raise CaptionContractError(f"caption {caption_id} has invalid shot_ids")
    source_evidence: dict[str, Any] = {
        "beat": {"beat_id": beat_id, "risk_flags": risk_flags, "high_risk": raw_high_risk, "generation_mode": generation_mode},
        "caption": {"caption_id": caption_id, "shot_ids": [item.strip() for item in raw_shot_ids], "semantic_rationale": str(caption.get("semantic_rationale") or "")},
    }
    if raw_caption_event:
        source_evidence["caption"]["event_evidence"] = dict(raw_caption_event)
    return {
        "action_key": action_key,
        "incompatible_action_keys": incompatible_action_keys,
        "hard_split_event": hard_split_event,
        "event_instance_id": event_instance_id,
        "source_evidence": source_evidence,
    }


def enrich_captions_to_contracts(
    *,
    script_sections: Sequence[Mapping[str, Any]],
    beats: Sequence[Mapping[str, Any]],
    captions: Sequence[Mapping[str, Any]],
    entity_name_maps: Iterable[Mapping[str, str]] = (),
    entity_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[CaptionVisualContract]:
    """Derive the authoritative Caption Visual Contract for every caption.

    ``entity_name_maps`` is an ordered collection of id->display-name tables
    (for example character anchors, object anchors, scene anchors) used to
    resolve raw entity ids (``C002``, ``OBJ_BOOK``, ``SCENE_FIELD``) to human
    names for the contract's ``must_show`` list.

    The caption is the authority for required visible entities. A beat's
    ``requiredEntities`` may disambiguate an explicit caption name or remain
    ``may_show`` context, but can never force a person into ``must_show``.

    Fail-closed: a caption must bind to a locked script section. A locked beat
    is optional context; when absent, the caption text must occur in exactly one
    locked section and no beat entity may be inherited. The section's narrative
    function is authoritative; a disagreeing beat is a corrupted redundant
    field, not an alternate source of truth.
    """

    maps = list(entity_name_maps)
    metadata = entity_metadata or {}
    section_by_id: dict[str, dict[str, Any]] = {}
    section_text_by_id: dict[str, str] = {}
    for section in script_sections:
        sid = str(section.get("section_id") or section.get("id") or "")
        if not sid:
            continue
        section_by_id[sid] = dict(section)
        section_text_by_id[sid] = _norm(section.get("text", ""))

    lexicon = _build_referent_lexicon(maps)
    beat_list = list(beats)
    results: list[CaptionVisualContract] = []
    prior_caption_entities: list[tuple[str, str, str, tuple[tuple[str, str, str], ...]]] = []
    for caption in captions:
        caption_id = str(caption.get("caption_id") or caption.get("id") or "")
        text = str(caption.get("text", "")).strip()
        if not text:
            raise CaptionContractError(f"caption {caption_id} has no text; cannot derive a contract")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        beat = _best_beat_for_caption(text, beat_list, section_text_by_id)
        if beat is None:
            section_matches = [
                section_id
                for section_id, section_text in section_text_by_id.items()
                if section_text and _norm(text) in section_text
            ]
            if len(section_matches) != 1:
                raise CaptionContractError(
                    f"caption {caption_id} ({text[:24]!r}) cannot be bound to exactly one locked script section"
                )
            section_id = section_matches[0]
        else:
            section_id = str(beat.get("sectionId") or beat.get("section_id") or "")
            if not section_id:
                raise CaptionContractError(f"caption {caption_id} beat has no section_id")
        section = section_by_id.get(section_id, {})
        raw_section_nf = section.get("narrative_function")
        if not raw_section_nf:
            raise CaptionContractError(
                f"caption {caption_id} ({text[:24]!r}) cannot be bound to a locked script section "
                f"with a narrative_function; refusing to default to 'plot'"
            )
        narrative_function = normalize_script_register(str(raw_section_nf))
        beat_context: Mapping[str, Any] = beat or {}
        raw_beat_nf = beat_context.get("narrative_function") or beat_context.get("narrativeFunction")
        if raw_beat_nf and normalize_script_register(str(raw_beat_nf)) != narrative_function:
            raise CaptionContractError(
                f"caption {caption_id} beat narrative_function conflicts with script section {section_id}: "
                f"{raw_beat_nf!r} != {raw_section_nf!r}"
            )

        required = [str(item) for item in beat_context.get("requiredEntities", []) if str(item).strip()]
        caption_found = _direct_caption_entities(
            text,
            lexicon=lexicon,
            entity_name_maps=maps,
            required_entities=required,
        )
        must_show: list[CaptionEntityEvidence] = []
        for entity_id, (_kind, matched_term) in caption_found.items():
            must_show.append(_entity_evidence(
                entity_id,
                natural_language=_entity_display(entity_id, maps),
                reason="caption_named_entity",
                evidence={"text_evidence": matched_term},
            ))
        local_roles = _caption_local_roles(text)
        for role_id, term, evidence in local_roles:
            must_show.append(_entity_evidence(
                role_id,
                natural_language=term,
                reason="caption_local_role",
                evidence=evidence,
            ))
        local_groups = _caption_local_groups(text)
        for group_id, term, evidence in local_groups:
            must_show.append(_entity_evidence(
                group_id,
                natural_language=term,
                reason="caption_local_group",
                evidence=evidence,
            ))

        pronoun_evidence: list[dict[str, Any]] = []
        resolved_entity_ids: set[str] = set()
        for pronoun, pronoun_start, pronoun_end in _pronoun_occurrences(text):
            current_context_entities = [
                (entity_id, _entity_kind(entity_id), "") for entity_id in required
            ]
            current_candidates = _pronoun_candidates(
                pronoun,
                current_context_entities,
                metadata,
            )
            current_candidate_ids = {entity_id for entity_id, _kind, _term in current_candidates}
            resolution_basis: Mapping[str, Any]
            if current_candidate_ids:
                resolution_basis = {
                    "kind": "current_action_required_participant",
                    "source_beat_id": str(beat_context.get("beatId") or beat_context.get("id") or ""),
                    "candidate_entity_ids": sorted(current_candidate_ids),
                }
                gender_exclusions = _gender_exclusions(pronoun, current_context_entities, metadata)
                if gender_exclusions:
                    resolution_basis = {**resolution_basis, "gender_exclusions": gender_exclusions}
                if pronoun in {"他们", "她们"}:
                    same_caption_candidates = [
                        (group_id, "group", term)
                        for group_id, term, _evidence in local_groups
                        if _is_plural_group_candidate(pronoun, "group")
                    ]
                else:
                    same_caption_candidates = [
                        (entity_id, kind, term)
                        for entity_id, (kind, term) in caption_found.items()
                        if entity_id in current_candidate_ids
                    ]
                fallback_due_no_current_candidate = False
            else:
                gender_exclusions = _gender_exclusions(pronoun, current_context_entities, metadata)
                fallback_due_no_current_candidate = beat is not None
                resolution_basis = (
                    {
                        "kind": "upstream_caption_fallback_due_no_compatible_current_participant",
                        "current_beat_id": str(beat_context.get("beatId") or beat_context.get("id") or ""),
                        "section_id": section_id,
                        "rejected_current_candidates": gender_exclusions,
                    }
                    if fallback_due_no_current_candidate
                    else {"kind": "unique_upstream_referent"}
                )
                same_caption_candidates = []
            same_caption_clause = _select_clause_candidates(
                text,
                same_caption_candidates,
                end=pronoun_start,
            )
            if same_caption_clause is not None:
                antecedent_clause, same_caption_candidates = same_caption_clause
            else:
                antecedent_clause = None
                same_caption_candidates = []
            nearest = None
            role_group_resolution: dict[str, Any] | None = None
            if same_caption_candidates:
                upstream_caption_id = caption_id
                upstream_text = text
                candidates = same_caption_candidates
                same_caption = True
            elif current_candidate_ids:
                for upstream_caption_id, _upstream_section_id, upstream_text, entities in reversed(prior_caption_entities):
                    context_candidates = [
                        candidate for candidate in entities
                        if candidate[0] in current_candidate_ids
                        or _is_plural_group_candidate(pronoun, candidate[1])
                    ]
                    selected_clause = _select_clause_candidates(upstream_text, context_candidates)
                    if selected_clause is not None:
                        antecedent_clause, candidates = selected_clause
                        nearest = (upstream_caption_id, upstream_text, candidates)
                        break
                same_caption = False
            else:
                for upstream_caption_id, upstream_section_id, upstream_text, entities in reversed(prior_caption_entities):
                    if fallback_due_no_current_candidate and upstream_section_id != section_id:
                        continue
                    upstream_candidates = [
                        candidate for candidate in _pronoun_candidates(pronoun, entities, metadata)
                        if pronoun not in {"他们", "她们"}
                        or _is_plural_group_candidate(pronoun, candidate[1])
                    ]
                    selected_clause = _select_clause_candidates(upstream_text, upstream_candidates)
                    if selected_clause is not None:
                        antecedent_clause, candidates = selected_clause
                        nearest = (upstream_caption_id, upstream_text, candidates)
                        break
                same_caption = False
                if pronoun in {"他们", "她们"} and nearest is None:
                    scene_ids = {
                        entity_id for entity_id in required
                        if entity_id.startswith("SCENE_")
                    }.union(
                        entity_id for entity_id in caption_found
                        if entity_id.startswith("SCENE_")
                    )
                    role_groups = _upstream_role_group_candidates(
                        prior_caption_entities,
                        section_id=section_id,
                        scene_ids=scene_ids,
                    )
                    if len(role_groups) > 1:
                        raise CaptionContractError(
                            f"caption {caption_id} contains ambiguous plural role groups "
                            f"{sorted(role_groups)} in section {section_id}"
                        )
                    if role_groups:
                        entity_id, role_group_resolution = next(iter(role_groups.items()))
                        support = role_group_resolution["supporting_upstream_captions"][-1]
                        upstream_caption_id = str(support["caption_id"])
                        upstream_text = str(support["caption_text"])
                        candidates = [(entity_id, "group", str(role_group_resolution["term"]))]
            if nearest is None and role_group_resolution is None and not same_caption_candidates:
                if current_candidate_ids:
                    detail = "no current-context upstream evidence"
                elif fallback_due_no_current_candidate:
                    detail = "no same-section upstream evidence after current participant rejection"
                else:
                    detail = "no prior caption evidence"
                raise CaptionContractError(
                    f"caption {caption_id} contains unresolved pronoun {pronoun!r}; {detail}"
                )
            if not same_caption_candidates and role_group_resolution is None:
                upstream_caption_id, upstream_text, candidates = nearest
            candidates_by_id = {entity_id: (kind, term) for entity_id, kind, term in candidates}
            candidates_by_id, unknown_gender_candidates, explicit_gender_matches = _apply_explicit_gender_match_precedence(
                pronoun,
                candidates_by_id,
                metadata,
            )
            if unknown_gender_candidates:
                resolution_basis = {
                    **resolution_basis,
                    "unknown_candidates_excluded_by_explicit_match": unknown_gender_candidates,
                    "explicit_gender_matches": explicit_gender_matches,
                }
            if role_group_resolution is not None:
                antecedent_clause = _select_clause_candidates(upstream_text, candidates)[0]
                resolution_basis = {
                    "kind": "plural_role_group_from_explicit_upstream_roles",
                    "current_beat_id": str(beat_context.get("beatId") or beat_context.get("id") or ""),
                    "section_id": section_id,
                    "scene_id": next(iter(sorted(scene_ids))),
                    "local_role_category": str(role_group_resolution["role"]),
                    "supporting_upstream_captions": role_group_resolution["supporting_upstream_captions"],
                }
                fallback_due_no_current_candidate = False
            if fallback_due_no_current_candidate:
                selected_profile_evidence = [
                    {
                        "entity_id": entity_id,
                        "gender": metadata.get(entity_id, {}).get("gender"),
                        "gender_evidence": dict(metadata[entity_id]["gender_evidence"]),
                    }
                    for entity_id in sorted(candidates_by_id)
                    if isinstance(metadata.get(entity_id), Mapping)
                    and isinstance(metadata[entity_id].get("gender_evidence"), Mapping)
                ]
                if selected_profile_evidence:
                    resolution_basis = {
                        **resolution_basis,
                        "upstream_candidate_profile_evidence": selected_profile_evidence,
                    }
            if pronoun in {"他", "她", "它"} and len(candidates_by_id) != 1:
                source_label = "same caption" if same_caption else f"nearest prior caption {upstream_caption_id}"
                raise CaptionContractError(
                    f"caption {caption_id} contains ambiguous pronoun {pronoun!r}; {source_label} "
                    f"has candidates {sorted(candidates_by_id)}"
                )
            if pronoun in {"他们", "她们"}:
                candidates_by_id = {
                    entity_id: (kind, term)
                    for entity_id, (kind, term) in candidates_by_id.items()
                    if _is_plural_group_candidate(pronoun, kind)
                }
            if pronoun in {"他们", "她们"} and len(candidates_by_id) != 1:
                source_label = "same caption" if same_caption else f"nearest prior caption {upstream_caption_id}"
                raise CaptionContractError(
                    f"caption {caption_id} contains unsupported plural pronoun {pronoun!r}; {source_label} "
                    "does not name one explicit plural group"
                )
            for entity_id, (kind, term) in sorted(candidates_by_id.items()):
                _validate_pronoun_metadata(
                    caption_id=caption_id,
                    pronoun=pronoun,
                    entity_id=entity_id,
                    kind=kind,
                    metadata=metadata,
                )
                evidence = {
                    "resolved_entity_id": entity_id,
                    "pronoun_resolution": pronoun,
                    "resolution_basis": dict(resolution_basis),
                    "text_evidence": pronoun,
                }
                if same_caption:
                    evidence.update({
                        "antecedent_caption_id": caption_id,
                        "antecedent_caption_text": text,
                        "antecedent_caption_span": _candidate_span_in_clause(antecedent_clause, term),
                        "antecedent_clause_text": antecedent_clause["text"],
                        "antecedent_clause_span": antecedent_clause,
                        "pronoun_span": {"start": pronoun_start, "end": pronoun_end, "text": pronoun},
                    })
                else:
                    evidence.update({
                        "pronoun_resolution": pronoun,
                        "upstream_caption_id": upstream_caption_id,
                        "upstream_caption_text": upstream_text,
                        "upstream_caption_span": _candidate_span_in_clause(antecedent_clause, term),
                        "antecedent_clause_text": antecedent_clause["text"],
                        "antecedent_clause_span": antecedent_clause,
                    })
                pronoun_evidence.append(evidence)
                if entity_id not in resolved_entity_ids and not any(item.entity_id == entity_id for item in must_show):
                    must_show.append(_entity_evidence(
                        entity_id,
                        natural_language=term if kind == "group" else _entity_display(entity_id, maps),
                        reason="pronoun_resolution",
                        evidence=evidence,
                    ))
                    resolved_entity_ids.add(entity_id)

        must_show_ids = {item.entity_id for item in must_show}
        may_show = tuple(
            _entity_evidence(
                entity_id,
                natural_language=_entity_display(entity_id, maps),
                reason="beat_required_entity_context",
                evidence={"source_beat_id": str(beat_context.get("beatId") or beat_context.get("id") or ""), "required_entity": entity_id},
            )
            for entity_id in required
            if entity_id not in must_show_ids
        )
        forbidden = [str(item) for item in beat_context.get("forbiddenEntities", []) if str(item).strip()]
        must_not_show = tuple(
            _entity_evidence(
                entity_id,
                natural_language=_entity_display(entity_id, maps),
                reason="beat_forbidden_entity",
                evidence={"source_beat_id": str(beat_context.get("beatId") or beat_context.get("id") or ""), "forbidden_entity": entity_id},
            )
            for entity_id in forbidden
        )

        visual_mode = "literal" if narrative_function in _LITERAL_FUNCTIONS else "symbolic_or_abstract"
        scene_eid = next((eid for eid in caption_found if eid.startswith("SCENE_")), "")
        location_id = scene_eid or _first_scene(required)
        location = _entity_display(location_id, maps) if location_id else beat_context.get("location") or section.get("chapterTitle", "")
        actions = _split_sentences(beat_context.get("description", "") or text)[:3] or [text]
        visible_character_ids = [
            item.entity_id for item in must_show if _entity_kind(item.entity_id) in {"char", "group"}
        ]
        contract = CaptionVisualContract(
            caption_id=caption_id,
            caption_text=text,
            caption_text_sha256=text_hash,
            section_id=section_id,
            source_beat_ids=(str(beat_context.get("beatId") or beat_context.get("id") or ""),) if beat else (),
            narrative_function=narrative_function,
            subjects=tuple(
                item.natural_language for item in must_show if _entity_kind(item.entity_id) in {"char", "group"}
            ),
            actions=tuple(actions),
            location=str(location),
            time_context=str(beat_context.get("time_context") or section.get("time_context") or ""),
            story_objects=tuple(
                item.natural_language for item in must_show if _entity_kind(item.entity_id) not in {"char", "group"}
            ),
            scene_state={
                "visible_character_ids": visible_character_ids,
                "location_id": location_id,
                "time_context": str(beat_context.get("time_context") or section.get("time_context") or ""),
                "action_state": text,
                "continuity_state": {"pronoun_resolutions": pronoun_evidence},
                "action_semantics": _derive_action_semantics(
                    caption=caption,
                    caption_id=caption_id,
                    section_id=section_id,
                    beat=beat,
                ),
            },
            must_show=tuple(must_show),
            may_show=may_show,
            must_not_show_as_primary=must_not_show,
            visual_focus=_norm(text)[:40],
            visual_mode=visual_mode,
        )
        contract = replace(contract, semantic_signature=contract.content_sha256())
        results.append(contract)
        if caption_found or local_groups or local_roles:
            prior_caption_entities.append((
                caption_id,
                section_id,
                text,
                tuple(
                    [
                        (entity_id, kind, term)
                        for entity_id, (kind, term) in caption_found.items()
                    ]
                    + [(group_id, "group", term) for group_id, term, _evidence in local_groups]
                ),
            ))
    return results


def _first_scene(required: Sequence[str]) -> str:
    for entity in required:
        if str(entity).startswith("SCENE_"):
            return str(entity)
    return ""


def build_caption_visual_contract_document(
    *,
    release_id: str,
    contracts: Sequence[CaptionVisualContract],
) -> dict[str, Any]:
    """Serialize the contract set as the ``04_audio/CAPTION_VISUAL_CONTRACT.json`` artifact."""

    validated = [CaptionVisualContract.from_mapping(contract.to_dict()) for contract in contracts]
    return {
        "schema_version": "caption-visual-contract.v2",
        "release_id": str(release_id),
        "caption_count": len(validated),
        "contracts": {contract.caption_id: contract.to_dict() for contract in validated},
    }


def write_caption_visual_contract_document(
    root: str | Path,
    *,
    release_id: str,
    contracts: Sequence[CaptionVisualContract],
) -> Path:
    """Persist the contract set to ``04_audio/CAPTION_VISUAL_CONTRACT.json``."""

    document = build_caption_visual_contract_document(release_id=release_id, contracts=contracts)
    path = Path(root) / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_caption_visual_contract_document(path: str | Path) -> dict[str, CaptionVisualContract]:
    """Load a contract document back into a ``caption_id -> CaptionVisualContract`` map."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "caption-visual-contract.v2":
        raise CaptionContractError(f"unexpected caption visual contract schema {data.get('schema_version')!r}")
    return {
        caption_id: CaptionVisualContract.from_mapping(payload)
        for caption_id, payload in data.get("contracts", {}).items()
    }


__all__ = [
    "CaptionContractError",
    "CaptionEntityEvidence",
    "CaptionVisualContract",
    "build_caption_visual_contract_document",
    "build_caption_visual_contract_from_project",
    "enrich_captions_to_contracts",
    "load_caption_visual_contract_document",
    "write_caption_visual_contract_document",
]


# ---------------------------------------------------------------------------
# Production adapter: build the real project's Caption Visual Contract from the
# locked Phase-2 / Phase-4 artifacts. This is the single source of truth the
# Director and Render stages consume -- it is NOT a stub and must never be fed
# fabricated captions, beats, or sections.
# ---------------------------------------------------------------------------

_PROJECT_SCRIPT_PACKAGE_RELATIVE = "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
_PROJECT_STORYBOARD_BASE_RELATIVE = "STORYBOARD_BASE.json"
_PROJECT_CAPTION_BINDINGS_RELATIVE = "04_audio/CAPTION_BINDINGS.json"
_PROJECT_VISUAL_PROFILE_RELATIVE = "03_images_生成图片/BOOK_VISUAL_PROFILE.json"


def _load_project_json(root: Path, relative: str) -> dict[str, Any]:
    path = Path(root) / relative
    if not path.is_file():
        raise CaptionContractError(f"contract input missing: {relative}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptionContractError(f"contract input unreadable: {relative}: {error}") from error


def _extract_project_script_sections(package: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve the locked script-section source of truth for the contract.

    Accepts both the canonical top-level ``performance_version.sections`` and the
    older ``script.performance_version.sections`` layout. An unknown layout is
    rejected so the contract cannot silently default a section to "plot".
    """

    top = package.get("performance_version")
    if not isinstance(top, dict) or not isinstance(top.get("sections"), list):
        nested = (package.get("script") or {}).get("performance_version")
        top = nested if isinstance(nested, dict) else None
    sections = top.get("sections") if isinstance(top, dict) else None
    if not isinstance(sections, list) or not sections:
        raise CaptionContractError("SCRIPT_PACKAGE.json has no performance_version.sections")
    out: list[dict[str, Any]] = []
    for section in sections:
        out.append({
            "section_id": str(section.get("section_id") or section.get("id") or ""),
            "narrative_function": str(section.get("narrative_function") or ""),
            "text": str(section.get("text") or ""),
        })
    return out


def _build_project_entity_name_maps(profile: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build id->display-name tables from the visual profile anchors.

    character anchors carry ``character_id`` + ``name``; object/scene anchors
    carry ``anchor_id`` + ``name``. The contract's ``must_show`` resolves these
    ids to human names so the Director prompt names the referent, not a code.
    """

    char: dict[str, str] = {}
    for anchor in profile.get("character_anchors", []) or []:
        cid = anchor.get("character_id")
        if cid:
            char[str(cid)] = str(anchor.get("name") or anchor.get("prompt_subject") or cid)
    obj: dict[str, str] = {}
    for anchor in profile.get("object_anchors", []) or []:
        oid = anchor.get("anchor_id") or anchor.get("object_id")
        if oid:
            obj[str(oid)] = str(anchor.get("name") or anchor.get("prompt_subject") or oid)
    scene: dict[str, str] = {}
    for anchor in profile.get("scene_anchors", []) or []:
        sid = anchor.get("anchor_id") or anchor.get("scene_id")
        if sid:
            scene[str(sid)] = str(anchor.get("name") or anchor.get("prompt_subject") or sid)
    return [char, obj, scene]


def _profile_marker_gender(
    anchor: Mapping[str, Any],
    *,
    anchor_id: str,
    profile_sha256: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract one unambiguous lexical gender marker from a locked character anchor."""

    matches: list[tuple[str, str, str, int]] = []
    for field in ("name", "prompt_subject"):
        value = anchor.get(field)
        if not isinstance(value, str):
            continue
        for gender, markers in _PROFILE_GENDER_MARKERS.items():
            for marker in markers:
                start = value.find(marker)
                if start >= 0:
                    matches.append((gender, field, marker, start))
    invariants = anchor.get("invariants", [])
    if isinstance(invariants, list):
        for index, value in enumerate(invariants):
            if not isinstance(value, str):
                continue
            for gender, markers in _PROFILE_GENDER_MARKERS.items():
                for marker in markers:
                    start = value.find(marker)
                    if start >= 0:
                        matches.append((gender, f"invariants[{index}]", marker, start))
    genders = {gender for gender, _field, _marker, _start in matches}
    if len(genders) > 1:
        raise CaptionContractError(f"anchor {anchor_id} has contradictory gender markers")
    if not matches:
        return None, None
    gender, field, marker, start = matches[0]
    return gender, {
        "anchor_id": anchor_id,
        "field": field,
        "marker": marker,
        "profile_sha256": profile_sha256,
        "span": {"start": start, "end": start + len(marker), "text": marker},
    }


def _build_project_entity_metadata(
    profile: Mapping[str, Any],
    *,
    profile_sha256: str = "",
) -> dict[str, dict[str, Any]]:
    """Return explicit fields or auditable profile markers trusted for pronouns."""

    metadata: dict[str, dict[str, Any]] = {}
    for anchors, id_keys, is_character_anchor in (
        (profile.get("character_anchors", []) or [], ("character_id", "anchor_id"), True),
        (profile.get("object_anchors", []) or [], ("object_id", "anchor_id"), False),
        (profile.get("scene_anchors", []) or [], ("scene_id", "anchor_id"), False),
    ):
        for anchor in anchors:
            if not isinstance(anchor, Mapping):
                continue
            entity_id = next((anchor.get(key) for key in id_keys if anchor.get(key)), None)
            values: dict[str, Any] = {
                key: anchor[key]
                for key in ("entity_type", "gender")
                if isinstance(anchor.get(key), str) and anchor[key].strip()
            }
            if entity_id and is_character_anchor:
                anchor_id = str(anchor.get("anchor_id") or entity_id)
                marker_gender, marker_evidence = _profile_marker_gender(
                    anchor,
                    anchor_id=anchor_id,
                    profile_sha256=profile_sha256,
                )
                declared_gender = values.get("gender")
                if declared_gender and marker_gender and declared_gender != marker_gender:
                    raise CaptionContractError(
                        f"anchor {anchor_id} structured gender conflicts with its profile marker"
                    )
                if declared_gender:
                    values["gender_evidence"] = {
                        "anchor_id": anchor_id,
                        "field": "gender",
                        "profile_sha256": profile_sha256,
                        "value": declared_gender,
                    }
                elif marker_gender and marker_evidence:
                    values["gender"] = marker_gender
                    values["gender_evidence"] = marker_evidence
            if entity_id and values:
                metadata[str(entity_id)] = values
    return metadata


def build_caption_visual_contract_from_project(
    root: str | Path,
    *,
    release_id: str | None = None,
    validate_only: bool = False,
) -> Path | dict[str, Any]:
    """Derive and persist the project's Caption Visual Contract.

    Reads the locked ``SCRIPT_PACKAGE.json`` (section narrative_functions),
    ``STORYBOARD_BASE.json`` (Phase-2 beats with required/forbidden entities),
    ``04_audio/CAPTION_BINDINGS.json`` (restored captions) and
    ``BOOK_VISUAL_PROFILE.json`` (entity display names), then writes
    ``04_audio/CAPTION_VISUAL_CONTRACT.json``.

    Fail-closed: a caption that cannot be bound to a locked beat/section is
    rejected by ``enrich_captions_to_contracts`` -- there is no silent
    "plot" default and no empty-subjects default.
    """

    root = Path(root)
    package = _load_project_json(root, _PROJECT_SCRIPT_PACKAGE_RELATIVE)
    sections = _extract_project_script_sections(package)
    beats = _load_project_json(root, _PROJECT_STORYBOARD_BASE_RELATIVE)
    if not isinstance(beats, list):
        raise CaptionContractError("STORYBOARD_BASE.json must be an array of beats")
    cap_doc = _load_project_json(root, _PROJECT_CAPTION_BINDINGS_RELATIVE)
    captions_raw = cap_doc.get("captions", {})
    captions = list(captions_raw.values()) if isinstance(captions_raw, dict) else list(captions_raw)
    if not isinstance(captions, list) or not captions:
        raise CaptionContractError("CAPTION_BINDINGS.json contains no captions")
    profile_path = root / _PROJECT_VISUAL_PROFILE_RELATIVE
    profile = _load_project_json(root, _PROJECT_VISUAL_PROFILE_RELATIVE)
    profile_sha256 = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    name_maps = _build_project_entity_name_maps(profile)
    if not release_id:
        release_id = cap_doc.get("release_id") or package.get("release_id") or "unknown"
    contracts = enrich_captions_to_contracts(
        script_sections=sections,
        beats=beats,
        captions=captions,
        entity_name_maps=name_maps,
        entity_metadata=_build_project_entity_metadata(profile, profile_sha256=profile_sha256),
    )
    document = build_caption_visual_contract_document(release_id=release_id, contracts=contracts)
    if validate_only:
        return document
    return write_caption_visual_contract_document(root, release_id=release_id, contracts=contracts)
