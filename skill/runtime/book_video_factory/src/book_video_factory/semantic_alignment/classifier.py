"""Deterministic Literal / Symbolic / Abstract proposition planner.

Pure Python, no clock, no network, no filesystem. Given the caption text bound
to a shot plus the project's visual anchors, it decides what the image is being
asked to do and writes a rationale that names both sides of the claim.

Decision order (deliberately *not* the naive order):

1. **Abstract first.** Questions, meta-narrator lines and author backstory can
   contain anchor nouns by accident; classifying them Literal is exactly how the
   pipeline ended up drawing a farmer for "余华说他听了一首美国民歌". The design
   requires that the classifier never returns Literal for an Abstract-recommended
   caption, so the Abstract test runs first.
2. **Literal** when a character/scene/object anchor is named in the caption.
3. **Symbolic** when a recognised trope is available.
4. **Abstract** as the fallback.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .models import EntityVisibility, VisualProposition


class PropositionClassifierError(RuntimeError):
    """The classifier cannot produce a defensible proposition from these inputs."""


# Caption markers that force Abstract regardless of any anchor noun present.
ABSTRACT_MARKERS: tuple[str, ...] = (
    "为什么", "凭什么", "究竟", "到底",
    "作者", "原著", "写道", "自述", "灵感", "采访", "民歌", "小说", "这本书",
    "读完", "我们一起", "本期", "节目", "这期", "视频", "下一本",
)
_QUESTION_RE = re.compile(r"[?？]\s*$")

# Symbolic tropes: (required tokens, surrogate subject, mood)
SYMBOLIC_TROPES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("月光", "路"), "月光下的村路", "清冷、辽远"),
    (("月", "坟"), "月色下的土坟", "静默的哀悼"),
    (("雪", "坟"), "雪覆盖的土坟", "静默的哀悼"),
    (("老牛",), "低头吃草的老牛", "迟缓的耐力"),
    (("黄昏", "路"), "黄昏里的一条长路", "疲惫与远行"),
    (("灯",), "夜里一盏昏黄的油灯", "微弱的守望"),
    (("窗",), "旧木窗与窗外的天光", "隔着距离的凝望"),
    (("风",), "风吹过的空旷田野", "无常与流逝"),
    (("河", "水"), "缓慢流动的河面", "时间的流逝"),
)

DEFAULT_LIGHTING = "DUSK_SOFT"
DEFAULT_PALETTE = "EARTH_DUSK"

ABSTRACT_ALLOWED_NARRATIVE_FUNCTIONS = {
    "theory",
    "author_background",
    "transition",
    "closing",
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


# Descriptive prompt subjects read like "福贵，瘦削老年农民": the referent name is
# the head segment before the first separator. Production visual bibles almost
# never ship an ``aliases`` list, so this head is the only matchable name we get.
_NAME_SPLIT_RE = re.compile(r"[，,、。；;：:·\s（(【\[]")


def _leading_name(subject: str) -> str:
    head = _NAME_SPLIT_RE.split(_normalize(subject).strip(), 1)[0].strip()
    return head


def _anchor_terms(anchors: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    """Flatten anchors into ``(anchor_id, term, natural_language)`` triples.

    Every anchor contributes its aliases, its full ``prompt_subject`` *and* the
    leading name extracted from that subject, so a descriptive subject like
    "福贵，瘦削老年农民" still matches a caption that only says 福贵.
    """
    result: list[tuple[str, str, str]] = []
    for anchor_id, raw in sorted(anchors.items()):
        if not isinstance(raw, Mapping):
            continue
        natural = str(raw.get("natural_language") or raw.get("prompt_subject") or "").strip()
        terms: list[str] = []
        for alias in raw.get("aliases", []) or []:
            if isinstance(alias, str) and alias.strip():
                terms.append(alias.strip())
        subject = raw.get("prompt_subject")
        if isinstance(subject, str) and subject.strip():
            terms.append(subject.strip())
            head = _leading_name(subject)
            if head and head not in terms:
                terms.append(head)
        seen_terms: set[str] = set()
        for term in terms:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            result.append((str(anchor_id), term, natural or term))
    return result


def _required_entity_hits(caption: str, entities: Sequence[str]) -> list[tuple[str, str, str]]:
    """Required entities spoken in the caption are authoritative literal referents.

    ``requiredEntities`` comes from the locked source beats, so if that name is
    literally present in the line the viewer reads, the frame is making a literal
    claim about it - independent of whether the visual bible happens to carry a
    matching anchor.
    """
    haystack = _normalize(caption)
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for entity in entities:
        term = str(entity).strip()
        if len(term) < 2 or term in seen:
            continue
        if term in haystack:
            seen.add(term)
            found.append((f"REQUIRED::{term}", term, term))
    return found


def _matched(text: str, triples: Sequence[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    haystack = _normalize(text)
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor_id, term, natural in triples:
        if len(term) < 2:
            continue
        if term in haystack and term not in seen:
            seen.add(term)
            found.append((anchor_id, term, natural))
    return found


def _excerpt(text: str, limit: int = 18) -> str:
    compact = _normalize(text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def classify_proposition(
    *,
    shot_id: str,
    caption_texts: Sequence[str],
    description: str,
    seed_rationale: str,
    source_entities: Sequence[str],
    character_anchors: Mapping[str, Mapping[str, Any]] | None = None,
    scene_anchors: Mapping[str, Mapping[str, Any]] | None = None,
    object_anchors: Mapping[str, Mapping[str, Any]] | None = None,
    lighting: str = DEFAULT_LIGHTING,
    palette: str = DEFAULT_PALETTE,
    narrative_function: str = "",
    visual_mode: str = "",
    symbolic_mapping: Mapping[str, Any] | None = None,
) -> VisualProposition:
    """Classify one shot into a Literal / Symbolic / Abstract visual proposition."""
    captions = [str(text).strip() for text in caption_texts if str(text).strip()]
    if not captions:
        raise PropositionClassifierError(
            f"shot {shot_id} has no caption text, so no visual proposition can be derived"
        )
    caption_blob = "".join(captions)
    entities = [str(item).strip() for item in source_entities if str(item).strip()]
    search_blob = caption_blob + _normalize(description)
    characters = _anchor_terms(character_anchors or {})
    scenes = _anchor_terms(scene_anchors or {})
    objects = _anchor_terms(object_anchors or {})

    mode_hint = str(visual_mode or "").strip().lower()
    if mode_hint and mode_hint not in {"literal", "abstract", "symbolic", "symbolic_or_abstract"}:
        raise PropositionClassifierError(
            f"shot {shot_id} has unsupported Caption Contract visual_mode {visual_mode!r}"
        )
    if mode_hint == "abstract":
        if narrative_function not in ABSTRACT_ALLOWED_NARRATIVE_FUNCTIONS or entities:
            raise PropositionClassifierError(
                f"shot {shot_id} cannot use Abstract for narrative_function {narrative_function!r} "
                "or a contract with required visible entities"
            )
        return VisualProposition(
            mode="Abstract",
            subject="纯气氛，无叙事指称",
            action="",
            environment="",
            mood="沉静、留白",
            lighting=lighting,
            palette=palette,
            rationale_text=(
                f"Caption Contract declares visual_mode=abstract for {narrative_function}; "
                f"「{_excerpt(caption_blob)}」不绑定剧情人物，画面只提供气氛且不得继承 Beat 实体"
            ),
            entity_visibility=(),
            surrogate_objects=(),
            source_terms=(),
        )
    if symbolic_mapping is not None:
        mapping_id = str(symbolic_mapping.get("mapping_id", "")).strip()
        status = str(symbolic_mapping.get("status", "")).strip()
        concept = str(symbolic_mapping.get("source_concept", "")).strip()
        surrogate = str(symbolic_mapping.get("surrogate_object", "")).strip()
        if (
            mode_hint not in {"symbolic", "symbolic_or_abstract"}
            or status != "approved"
            or not mapping_id
            or not concept
            or concept not in caption_blob
            or not surrogate
            or entities
        ):
            raise PropositionClassifierError(
                f"shot {shot_id} symbolic mapping is not approved, caption-grounded, or compatible with an empty must_show set"
            )
        return VisualProposition(
            mode="Symbolic",
            subject=surrogate,
            action="静态构图，让批准的象征物成为唯一主体",
            environment="与批准视觉 Profile 一致的时代环境",
            mood="克制、含蓄",
            lighting=lighting,
            palette=palette,
            rationale_text=(
                f"批准映射 {mapping_id} 将字幕概念「{concept}」绑定为「{surrogate}」；"
                "该映射来自当前视觉 Profile，不引入 Beat 人物"
            ),
            entity_visibility=(),
            surrogate_objects=(surrogate,),
            source_terms=(concept, f"mapping:{mapping_id}"),
        )
    if mode_hint == "symbolic":
        raise PropositionClassifierError(
            f"shot {shot_id} declares Symbolic but has no approved Profile symbolic mapping"
        )
    if mode_hint == "symbolic_or_abstract" and not entities:
        if narrative_function not in ABSTRACT_ALLOWED_NARRATIVE_FUNCTIONS:
            raise PropositionClassifierError(
                f"shot {shot_id} concrete narrative_function {narrative_function!r} cannot fall back to Abstract"
            )
        return VisualProposition(
            mode="Abstract",
            subject="纯气氛，无叙事指称",
            action="",
            environment="",
            mood="沉静、留白",
            lighting=lighting,
            palette=palette,
            rationale_text=(
                f"Caption Contract permits symbolic_or_abstract for {narrative_function}, "
                f"但当前批准 Profile 没有匹配「{_excerpt(caption_blob)}」的象征映射；"
                "因此只给气氛，不添加剧情人物"
            ),
            entity_visibility=(),
            surrogate_objects=(),
            source_terms=(),
        )
    if mode_hint == "literal" and not entities:
        if narrative_function == "opening" and _QUESTION_RE.search(caption_blob):
            return VisualProposition(
                mode="Abstract",
                subject="纯气氛，无叙事指称",
                action="",
                environment="",
                mood="悬置、留白",
                lighting=lighting,
                palette=palette,
                rationale_text=(
                    f"Opening Caption「{_excerpt(caption_blob)}」是明确设问且没有 must_show；"
                    "画面不得继承 Beat 人物，只提供开场悬念气氛"
                ),
                entity_visibility=(),
                surrogate_objects=(),
                source_terms=(),
            )
        raise PropositionClassifierError(
            f"shot {shot_id} declares Literal but its Caption Contract must_show set is empty"
        )

    is_question = bool(_QUESTION_RE.search(caption_blob))
    abstract_hits = [marker for marker in ABSTRACT_MARKERS if marker in caption_blob]
    if is_question or abstract_hits:
        reason = "这句是设问" if is_question else f"这句出现元叙述标记{abstract_hits[0]}"
        return VisualProposition(
            mode="Abstract",
            subject="纯气氛，无叙事指称",
            action="",
            environment="",
            mood="沉静、留白",
            lighting=lighting,
            palette=palette,
            rationale_text=(
                f"{reason}，没有可以入画的具体人物或动作；"
                f"画面只给气氛与色调，避免为「{_excerpt(caption_blob)}」编造不存在的场景"
            ),
            entity_visibility=(),
            surrogate_objects=(),
            source_terms=tuple(entities),
        )

    if not entities:
        raise PropositionClassifierError(
            f"shot {shot_id} has no caption-grounded visible entity; refusing to guess a subject"
        )

    anchor_hits = _matched(search_blob, characters) + _matched(search_blob, objects)
    required_hits = _required_entity_hits(caption_blob, entities)
    scene_hits = _matched(search_blob, scenes)
    # A shot is Literal when the caption names an anchor OR a required source
    # entity. Required entities are checked against the caption only (not the
    # description) so an entity that is merely context never gets forced on
    # screen. Anchor hits come first so their descriptive natural language wins.
    literal_hits = anchor_hits + required_hits
    if literal_hits:
        visibility: list[EntityVisibility] = []
        seen: set[str] = set()
        natural_by_term = {term: natural for _, term, natural in literal_hits + scene_hits}
        caption_norm = _normalize(caption_blob)
        for _, term, natural in literal_hits:
            if term in seen:
                continue
            seen.add(term)
            visibility.append(EntityVisibility(term, True, natural))
        for entity in entities:
            entity = str(entity).strip()
            if not entity or entity in seen:
                continue
            seen.add(entity)
            # Only force visibility for entities actually spoken in this line;
            # other required entities travel as source_terms context, not as
            # on-screen guarantees the frame cannot honour.
            must_show = len(entity) >= 2 and entity in caption_norm
            visibility.append(
                EntityVisibility(entity, must_show, natural_by_term.get(entity, entity))
            )
        environment = scene_hits[0][2] if scene_hits else "与原著时代一致的乡村环境"
        visible_items = [item for item in visibility if item.must_be_visible]
        primary = (visible_items or visibility)[0]
        named = "、".join(item.entity_id for item in visible_items) or primary.entity_id
        return VisualProposition(
            mode="Literal",
            subject=primary.natural_language,
            action=_normalize(caption_blob),
            environment=environment,
            mood="随旁白语气，克制不夸张",
            lighting=lighting,
            palette=palette,
            rationale_text=(
                f"旁白这一句点名了{named}，是可以直接入画的具体指称；"
                f"画面必须让这些对象同时可见，不能用气氛镜替代"
            ),
            entity_visibility=tuple(visibility),
            surrogate_objects=(),
            source_terms=tuple(entities),
        )

    for tokens, surrogate, mood in SYMBOLIC_TROPES:
        if all(token in search_blob for token in tokens):
            source_term = entities[0]
            return VisualProposition(
                mode="Symbolic",
                subject=surrogate,
                action="静态构图，让替身物件占据主体",
                environment=scene_hits[0][2] if scene_hits else "与原著时代一致的乡村环境",
                mood=mood,
                lighting=lighting,
                palette=palette,
                rationale_text=(
                    f"旁白落在「{_excerpt(caption_blob)}」，其中的{source_term}"
                    f"在这一句里没有可直接入画的动作；"
                    f"画面改用{surrogate}作为替身来承接这句台词"
                ),
                entity_visibility=(),
                surrogate_objects=(surrogate,),
                source_terms=tuple(entities),
            )

    # Concrete-event captions must NOT silently degrade into an Abstract
    # "atmosphere" shot when no anchor/trope matches. A plot/opening/character
    # beat that the classifier cannot ground as Literal/Symbolic must be
    # re-proposed (the planner should supply an anchor or trope), not shipped
    # as a blank mood frame. Only genuine meta narration -- theory,
    # author_background, transition, closing -- is permitted to fall back to
    # Abstract (it has no concrete referent to depict).
    if narrative_function and narrative_function not in ABSTRACT_ALLOWED_NARRATIVE_FUNCTIONS:
        raise PropositionClassifierError(
            f"shot {shot_id} caption narrative_function {narrative_function!r} is a concrete event "
            f"but no Literal/Symbolic proposition could be derived; refuse Abstract atmosphere and "
            f"re-propose with a grounding anchor or registered trope"
        )
    return VisualProposition(
        mode="Abstract",
        subject="纯气氛，无叙事指称",
        action="",
        environment="",
        mood="沉静、留白",
        lighting=lighting,
        palette=palette,
        rationale_text=(
            f"「{_excerpt(caption_blob)}」既没有命中任何视觉锚点，也没有可用的意象替身；"
            f"画面只给气氛，避免凭空编造与{entities[0]}无关的场景"
        ),
        entity_visibility=(),
        surrogate_objects=(),
        source_terms=tuple(entities),
    )


__all__ = [
    "ABSTRACT_MARKERS",
    "DEFAULT_LIGHTING",
    "DEFAULT_PALETTE",
    "PropositionClassifierError",
    "SYMBOLIC_TROPES",
    "classify_proposition",
]
