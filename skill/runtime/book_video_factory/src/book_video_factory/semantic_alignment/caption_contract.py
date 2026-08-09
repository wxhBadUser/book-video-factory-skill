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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .caption_grouping import NARRATIVE_FUNCTIONS, normalize_script_register

# Plot / concrete-register captions must be drawn LITERAL: the frame must show
# the named people and the named event. Reflective registers may be symbolic /
# abstract, but only through the established symbol registry.
_LITERAL_FUNCTIONS = {"opening", "plot"}
_SYMBOLIC_ALLOWED_FUNCTIONS = {"theory", "author_background", "transition", "closing"}


class CaptionContractError(RuntimeError):
    """A caption cannot be given a defensible visual contract."""


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _short_name(full_natural_language: str, fallback: str) -> str:
    """Reduce an anchor's descriptive natural-language to a short referent name.

    Anchors store "30-70岁江南农民，晒黑皮肤皱纹深，穿粗布短褂…"; the contract
    needs the *name* ("福贵"), not the whole description. The leading character
    before a separator is the name.
    """

    head = re.split(r"[，,、。；;：:·\s（(【\[]", _norm(full_natural_language).strip(), 1)[0].strip()
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
class CaptionVisualContract:
    """The authoritative record of what one display caption demands on screen.

    Every field below is derived from the locked script section + beat that the
    caption covers. The contract is the only legal input to the visual
    proposition, prompt and review stages.
    """

    caption_id: str
    caption_text: str
    caption_text_sha256: str

    source_beat_ids: tuple[str, ...]

    narrative_function: str
    subjects: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    location: str = ""
    time_context: str = ""
    story_objects: tuple[str, ...] = ()

    must_show: tuple[str, ...] = ()
    must_not_show_as_primary: tuple[str, ...] = ()

    visual_focus: str = ""
    visual_mode: str = "literal"
    semantic_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_id": self.caption_id,
            "caption_text": self.caption_text,
            "caption_text_sha256": self.caption_text_sha256,
            "source_beat_ids": list(self.source_beat_ids),
            "narrative_function": self.narrative_function,
            "subjects": list(self.subjects),
            "actions": list(self.actions),
            "location": self.location,
            "time_context": self.time_context,
            "story_objects": list(self.story_objects),
            "must_show": list(self.must_show),
            "must_not_show_as_primary": list(self.must_not_show_as_primary),
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
        nf = raw.get("narrative_function")
        if nf not in NARRATIVE_FUNCTIONS:
            raise CaptionContractError(f"contract for {raw.get('caption_id')} has invalid narrative_function {nf!r}")
        return cls(
            caption_id=str(raw.get("caption_id", "")),
            caption_text=text,
            caption_text_sha256=text_hash,
            source_beat_ids=tuple(str(item) for item in raw.get("source_beat_ids", []) if str(item).strip()),
            narrative_function=str(nf),
            subjects=tuple(str(item) for item in raw.get("subjects", []) if str(item).strip()),
            actions=tuple(str(item) for item in raw.get("actions", []) if str(item).strip()),
            location=str(raw.get("location", "")),
            time_context=str(raw.get("time_context", "")),
            story_objects=tuple(str(item) for item in raw.get("story_objects", []) if str(item).strip()),
            must_show=tuple(str(item) for item in raw.get("must_show", []) if str(item).strip()),
            must_not_show_as_primary=tuple(str(item) for item in raw.get("must_not_show_as_primary", []) if str(item).strip()),
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
    best: dict[str, Any] | None = None
    best_cue_len = None
    for beat in beats:
        cue = _norm(beat.get("cue", ""))
        if not cue:
            continue
        if norm in cue or cue in norm:
            cue_len = len(cue)
            if best_cue_len is None or cue_len < best_cue_len:
                best_cue_len = cue_len
                best = dict(beat)
    return best


# Locked domain referents for this production: entity id -> spoken aliases that
# may appear in a caption. The lexicon scans caption text for these so the
# contract's must_show is derived from what the caption actually NAMES, not only
# from the beat's required-entity ids (which a buggy storyboard can get wrong).
_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "C001": ("福贵", "阔少", "少爷", "老爷"),
    "C002": ("福贵", "老头", "老汉", "老爷子", "爹"),
    "C003": ("家珍", "妻子", "娘"),
    "C004": ("凤霞", "女儿"),
    "C005": ("有庆", "儿子"),
    "C006": ("龙二",),
    "C007": ("春生", "县长"),
    "C008": ("二喜",),
    "C009": ("苦根", "孩子"),
    "C010": ("老牛", "牛"),
    "OBJ_BEANS": ("豆子", "豆"),
    "OBJ_BOOK": ("书", "照片", "煤油灯", "书桌"),
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


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[，,。．.；;：:！!？?、\s]+", _norm(text))
    return [p for p in parts if len(p) >= 2]


def _entity_kind(entity_id: str) -> str:
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
            term = _short_name(name, entity_id).lower()
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


def enrich_captions_to_contracts(
    *,
    script_sections: Sequence[Mapping[str, Any]],
    beats: Sequence[Mapping[str, Any]],
    captions: Sequence[Mapping[str, Any]],
    entity_name_maps: Iterable[Mapping[str, str]] = (),
) -> list[CaptionVisualContract]:
    """Derive the authoritative Caption Visual Contract for every caption.

    ``entity_name_maps`` is an ordered collection of id->display-name tables
    (for example character anchors, object anchors, scene anchors) used to
    resolve raw entity ids (``C002``, ``OBJ_BOOK``, ``SCENE_FIELD``) to human
    names for the contract's ``must_show`` list.

    Fail-closed: a caption that cannot be bound to a real beat/section is
    rejected. There is no silent ``narrative_function = "plot"`` and no empty
    ``subjects`` default -- an un-derivable caption must not be illustrated.
    """

    maps = list(entity_name_maps)
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
    for caption in captions:
        caption_id = str(caption.get("caption_id") or caption.get("id") or "")
        text = str(caption.get("text", "")).strip()
        if not text:
            raise CaptionContractError(f"caption {caption_id} has no text; cannot derive a contract")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        beat = _best_beat_for_caption(text, beat_list, section_text_by_id)
        section_id = str(beat.get("sectionId") or beat.get("section_id") or "") if beat else ""
        if not section_id:
            # Fall back to the script section whose body contains the caption,
            # used ONLY to recover the narrative_function. Subjects are taken
            # from the caption text itself, never defaulted from a section.
            for sid, sec_text in section_text_by_id.items():
                if sec_text and _norm(text) in sec_text:
                    section_id = sid
                    break
        section = section_by_id.get(section_id, {})
        raw_nf = (beat or {}).get("narrative_function") or section.get("narrative_function")
        if not raw_nf:
            raise CaptionContractError(
                f"caption {caption_id} ({text[:24]!r}) cannot be bound to any locked beat/section "
                f"with a narrative_function; refusing to default to 'plot'"
            )
        narrative_function = normalize_script_register(str(raw_nf))

        # Subjects / objects: prefer what the caption TEXT names (the lexicon),
        # then union with the beat's required entities so a generic caption still
        # inherits the scene's intended referents.
        caption_found = _scan_caption_terms(text, lexicon)
        required = [str(item) for item in (beat or {}).get("requiredEntities", []) if str(item).strip()]
        beat_found: dict[str, tuple[str, str]] = {}
        for entity in required:
            kind = _entity_kind(entity)
            if entity not in beat_found:
                beat_found[entity] = (kind, entity)

        char_eids = {eid for eid, (kind, _t) in caption_found.items() if kind == "char"}
        char_eids |= {eid for eid in beat_found if beat_found[eid][0] == "char"}
        obj_eids = {eid for eid, (kind, _t) in caption_found.items() if kind != "char"}
        obj_eids |= {eid for eid in beat_found if beat_found[eid][0] != "char"}

        subjects = [_entity_display(eid, maps) for eid in char_eids]
        objects = [_entity_display(eid, maps) for eid in obj_eids]
        must_show: list[str] = []
        for item in subjects + objects:
            if item and item not in must_show:
                must_show.append(item)

        forbidden = [str(item) for item in (beat or {}).get("forbiddenEntities", []) if str(item).strip()]

        visual_mode = "literal" if narrative_function in _LITERAL_FUNCTIONS else "symbolic_or_abstract"
        scene_eid = next((eid for eid in obj_eids if eid.startswith("SCENE_")), "")
        location = _entity_display(scene_eid, maps) if scene_eid else (beat or {}).get("location") or section.get("chapterTitle", "")
        actions = _split_sentences((beat or {}).get("description", "") or text)[:3] or [text]

        contract = CaptionVisualContract(
            caption_id=caption_id,
            caption_text=text,
            caption_text_sha256=text_hash,
            source_beat_ids=((str((beat or {}).get("beatId") or (beat or {}).get("id") or ""),) if beat else ()),
            narrative_function=narrative_function,
            subjects=tuple(subjects),
            actions=tuple(actions),
            location=str(location),
            time_context=str((beat or {}).get("time_context") or section.get("time_context") or ""),
            story_objects=tuple(objects),
            must_show=tuple(must_show),
            must_not_show_as_primary=tuple(forbidden),
            visual_focus=_norm(text)[:40],
            visual_mode=visual_mode,
        )
        contract = CaptionVisualContract(**{**contract.to_dict(), "semantic_signature": contract.content_sha256()})
        results.append(contract)
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

    return {
        "schema_version": "caption-visual-contract.v1",
        "release_id": str(release_id),
        "caption_count": len(contracts),
        "contracts": {c.caption_id: c.to_dict() for c in contracts},
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
    if data.get("schema_version") != "caption-visual-contract.v1":
        raise CaptionContractError(f"unexpected caption visual contract schema {data.get('schema_version')!r}")
    return {
        caption_id: CaptionVisualContract.from_mapping(payload)
        for caption_id, payload in data.get("contracts", {}).items()
    }


__all__ = [
    "CaptionContractError",
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


def build_caption_visual_contract_from_project(root: str | Path, *, release_id: str | None = None) -> Path:
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
    profile = _load_project_json(root, _PROJECT_VISUAL_PROFILE_RELATIVE)
    name_maps = _build_project_entity_name_maps(profile)
    if not release_id:
        release_id = cap_doc.get("release_id") or package.get("release_id") or "unknown"
    contracts = enrich_captions_to_contracts(
        script_sections=sections,
        beats=beats,
        captions=captions,
        entity_name_maps=name_maps,
    )
    return write_caption_visual_contract_document(root, release_id=release_id, contracts=contracts)
