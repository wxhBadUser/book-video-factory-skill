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

# FIX 2 (pilot R2): registers that may still use Literal when the caption
# itself names concrete, drawable referents. Narrative Function decides how the
# frame is treated, not whether the caption has visible referents.
_CONCRETE_CAPABLE_FUNCTIONS = _LITERAL_FUNCTIONS | {"theory", "author_background", "transition", "closing"}

# FIX 1 (pilot R2): deterministic observable-event lexicon. A concrete plot
# caption must translate into visual evidence the image model can be asked to
# show, otherwise the contract is not defensible and the pipeline fails closed.
_EVENT_RULES: tuple[tuple[tuple[str, ...], str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("死", "撑死", "咽气", "去世", "枪毙", "没了", "离世", "送葬", "埋"),
        "death_aftermath",
        "非血腥的死亡后果（按成因）",
        ("克制、非血腥的死亡后果语境",),
        ("当事人健康站立或微笑",),
    ),
    (
        ("绑在柱子", "绑到柱子", "绑上柱子", "刑柱", "枪决", "执行队", "行刑", "开枪", "枪声"),
        "execution_at_post",
        "刑场执行（绑柱/枪决）",
        (
            "刑场执行状态：被绑在刑柱上或行刑队举枪（按字幕）",
            "绳子和刑柱清晰可见",
            "克制、非血腥",
        ),
        ("当事人被押着行走或自由站立交谈",),
    ),
    (
        ("壮丁", "当兵", "被抓", "拉去"),
        "conscription",
        "被抓壮丁/从军",
        (
            "被抓壮丁、押送或从军的场景（按字幕）",
            "克制、非血腥",
        ),
        (),
    ),
    (
        ("怀孕", "生孩子", "产房", "生啦", "大出血"),
        "pregnancy_birth",
        "怀孕/家人反应",
        (
            "孕妇腹部状态与家人围聚反应（克制）",
            "婚后/家中语境，不出现婚礼现场",
        ),
        ("婚礼现场或无关场景",),
    ),
    (
        ("战场", "战争", "打仗", "围困"),
        "battlefield",
        "战场",
        (
            "战场/战壕/硝烟与军装人群（按字幕）",
            "克制、非血腥",
        ),
        (),
    ),
    (
        ("歪在", "摔在", "倒下", "瘫"),
        "collapse",
        "倒地/瘫软",
        (
            "人物倒地或瘫软（克制、非血腥）",
            "相关物件在场（按字幕）",
        ),
        ("健康站立或正常活动",),
    ),
    (
        ("郎中", "大夫", "医馆", "请医", "求医", "看病", "抓药"),
        "medical_visit",
        "急切求医/求助",
        (
            "求医语境清晰可见：郎中/大夫或医馆（药柜、招牌、诊脉）",
            "人物正在走向或抵达求医地点",
            "神情急切、求助",
        ),
        ("无医疗语境的无目的行走", "人物在街市闲逛"),
    ),
    (
        ("抽血", "献血", "针管", "注射", "血"),
        "blood_loss",
        "持续抽血/失血虚弱",
        (
            "抽血/针管等医疗语境在场（按字幕）",
            "脸色苍白、虚弱（克制、非血腥）",
        ),
        ("健康奔跑或兴奋状态",),
    ),
    (
        ("回来了", "回来", "回家"),
        "return_home",
        "归家/重逢",
        (
            "人物回到家中/村口：正在进门或刚抵达，重逢的空间关系清晰",
            "家人面向归来者、相迎或同框",
            "随身行囊/到达姿态（时代允许时）",
        ),
        ("人物独自站立、无互动", "像家族合影般的静止群像", "同框出现字幕未点名的额外亲属/邻居"),
    ),
    (
        ("下地去了", "走出去", "离开了", "出城"),
        "departure_absence",
        "人物已离开（缺席状态）",
        (
            "人物离开后的空间关系（背影远去，或门开向田野）",
            "留在原处的关键物件清晰可见（按字幕）",
        ),
        ("人物仍坐在原处无所事事",),
    ),
    (
        ("民谣", "收集民谣", "下乡"),
        "folk_song_collection",
        "采风/收集",
        (
            "下乡收集民谣的年轻人在村口或田边与村民交谈、记录",
            "身背布袋或手拿笔记本",
            "旧时代乡村装束",
        ),
        ("现代装束或电子设备",),
    ),
    (
        ("出嫁", "婚礼", "嫁"),
        "wedding",
        "出嫁/婚礼",
        (
            "新娘与嫁衣/花轿清晰可见",
            "婚礼队列或村口喜事气氛（锣鼓、红布）",
        ),
        ("老牛等与字幕无关的物件成为画面主体",),
    ),
    (
        ("炊烟", "袅袅"),
        "smoke_rising",
        "静谧的黄昏/清晨",
        (
            "多股炊烟从农舍屋顶袅袅升起",
            "农舍与屋顶轮廓清晰",
            "乡村远景、天色渐暗或清晨",
        ),
        (),
    ),
    (
        ("只剩", "只剩下", "只剩得"),
        "solitude_leftover",
        "孤寂/相依",
        (
            "空旷场景中仅剩字幕点名的老人与老牛",
            "老人与老牛相互依存的孤独构图",
        ),
        ("其他家人同框成为主体",),
    ),
    (
        ("写《", "写作", "创作", "灵感", "自序", "美国民歌", "民歌"),
        "author_creation_context",
        "创作之前的语境",
        (
            "写作语境：旧式写字台、稿纸、纸笔，或民歌唱片/乐谱",
            "书中故事人物不得成为主体",
        ),
        ("已出版的《活着》成书作为主体", "书中故事人物作为主体"),
    ),
)

# Scene Span event containers.  One container == one stable representative
# frame; micro-actions inside the container never force a new image.  A
# retrospective death mention (death_mention) is narration-only and must NOT
# change the frame by itself.
_EVENT_CONTAINERS: dict[str, str] = {
    "death_aftermath": "death_aftermath",
    "death_mention": "death_mention",
    "execution_at_post": "execution_at_post",
    "conscription": "execution_transport",
    "pregnancy_birth": "family_household",
    "battlefield": "battlefield",
    "collapse": "physical_collapse",
    "medical_visit": "medical_visit",
    "blood_loss": "medical_visit",
    "return_home": "return_home",
    "departure_absence": "departure_absence",
    "folk_song_collection": "folk_song_collection",
    "wedding": "wedding",
    "smoke_rising": "landscape_mood",
    "solitude_leftover": "solitude_leftover",
    "author_creation_context": "author_creation_context",
}

# Inside an execution-at-post, 绑柱 -> 举枪 -> 开枪 -> 倒下 -> 死亡 all share
# ONE event container.  The normalizer folds the generic aftermath/collapse
# rules into execution_at_post so the container does not over-cut the scene.
_EXECUTION_NORMALIZABLE_PREDICATES = frozenset({"death_aftermath", "collapse"})

# FIX (single-frame coverability review): death must be cause-specific, and a
# retrospective mention of a dead person must not force on-screen corpse
# imagery (which would spoil or contradict the surrounding captions).
_DEATH_KEYWORDS: tuple[str, ...] = ("死", "撑死", "咽气", "去世", "枪毙", "没了", "离世", "送葬", "埋")
_DEATH_CAUSE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("撑死", "吃豆", "吃多了", "豆子"), "overeating_beans"),
    (("枪毙", "刑场"), "execution"),
    (("生孩子", "大出血", "产"), "childbirth"),
    (("抽血", "献血", "针管", "注射", "血"), "blood_loss"),
    (("病逝", "病重", "病死", "病"), "illness"),
    (("淹", "车祸", "压死", "砸"), "accident"),
)
_DEATH_REFERENCE_MARKERS: tuple[str, ...] = (
    "写死", "死去", "读到", "送葬", "埋", "想起", "记得", "名字", "家人",
)
_DEATH_ONSCREEN_MARKERS: tuple[str, ...] = (
    "撑死", "咽气", "枪毙", "抽血", "血流", "倒下", "摔在", "歪在", "心跳", "大出血", "断了气",
)
_DEATH_EVIDENCE: dict[str, tuple[str, ...]] = {
    "overeating_beans": (
        "身体完全瘫软/倒伏，姿态与普通睡眠不相容（侧倒床边或地上、被发现在豆旁）",
        "无反应、不省人事的姿势",
        "豆子与事件直接关联：有明显吃过/剩余/散落的豆子（按字幕）",
        "克制、非血腥的死亡后果语境",
    ),
    "blood_loss": (
        "身体虚弱/瘫软倒下，脸色苍白（克制、非血腥）",
        "抽血/针管等医疗语境在场（按字幕）",
        "从兴奋或正常状态转入无反应",
    ),
    "execution": ("刑场/枪决语境，克制不血腥", "当事人倒地或跪伏（按字幕）"),
    "childbirth": ("产房/大出血语境，克制不血腥", "医疗人员或家人围绕"),
    "illness": ("病榻/病卧语境，克制不血腥",),
    "accident": ("事故语境（淹/压/砸），克制不血腥",),
    "unknown": ("克制、非血腥的死亡后果语境",),
}
_DEATH_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "overeating_beans": ("端正坐在椅上像睡着", "平静午睡姿态", "正在进食", "微笑", "看起来清醒"),
    "blood_loss": ("健康奔跑或兴奋状态", "正常进食、说笑"),
    "default": ("当事人健康站立或微笑",),
}

# Single-frame coverability (review): per-caption visual state label used by
# the grouping gate to reject mutually incompatible event phases.
_ALIVE_ACTIVE_KEYWORDS: tuple[str, ...] = (
    "说", "问", "喊", "回答", "跑", "笑", "高兴", "站", "活着", "跳", "唱", "走",
)

# Event-only literal captions ("被抓壮丁", "战场上…") name no entity but are
# perfectly drawable scenes. They get a synthetic must-show referent so the
# classifier's Literal gate is satisfiable without inventing characters.
def _event_only_subject(text: str) -> tuple[str, str, str] | None:
    norm = _norm(text)
    for keywords, entity_id, natural_language in (
        (("壮丁", "当兵", "被抓", "拉去"), "ROLE_CONSORT", "被抓壮丁的农民"),
        (("战场", "战争", "打仗", "围困"), "SCENE_WAR", "战场"),
    ):
        for keyword in keywords:
            if keyword in norm:
                return entity_id, natural_language, keyword
    return None

# FIX 3 (pilot R2): explicit author-creation metadata under a plot-register
# section is a metadata defect, never a silent reinterpretation.
_AUTHOR_CREATION_MARKERS: tuple[str, ...] = (
    "写《",
    "写作",
    "创作",
    "灵感",
    "自序",
    "美国民歌",
    "民歌",
)
_CREATION_BEFORE_TITLE_RE = re.compile(r"写《[^》]+》\s*之前")


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


# 音频旁白把锁定脚本标点全部去掉。与 meaning_blocks._SCRIPT_PUNCT 保持一致
# （audio_stage 依赖 semantic_alignment，故不反向 import）。
_BIND_PUNCT = frozenset("，。、！？；：,.;!?…—–·\"'“”‘’「」『』《》（）〈〉【】")


def _bind_norm(text: str) -> str:
    """NFKC + 去标点 + 去空白：用于 caption↔section/beat 的绑定匹配。

    真实旁白流把脚本标点全部去掉；契约绑定若对标点敏感，去标点的 caption
    （如 "因为那片海说"）永远匹配不上锁定文本（"因为那片海，说白了"）。
    """
    return "".join(ch for ch in _norm(text) if ch not in _BIND_PUNCT and not ch.isspace())


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

    aliases = _ENTITY_ALIASES.get(str(entity_id)) or _ROLE_DISPLAY_ALIASES.get(str(entity_id))
    if aliases:
        return max(aliases, key=len)
    return ""


def _scene_terms_in_text(text: str) -> list[str]:
    """Scene display names literally named by the text (e.g. 战场/医院/街市)."""

    norm = _norm(text)
    found: list[str] = []
    for entity_id, aliases in _ENTITY_ALIASES.items():
        if not str(entity_id).startswith("SCENE_"):
            continue
        for alias in aliases:
            if alias and alias in norm:
                display = _alias_display(entity_id) or alias
                if display not in found:
                    found.append(display)
                break
    return found


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
class VisualEventState:
    """FIX 1 (pilot R2): observable visual event state for one caption.

    Entities alone are not enough: a frame may list the right nouns while
    depicting the wrong event ("苦根 + 豆子" but the child is healthy and
    sitting). This record carries the predicate, the state and the concrete
    visual evidence the image must establish, plus states that would
    contradict the caption and are therefore forbidden.
    """

    action_predicate: str
    cause_type: str = ""
    is_reference_death: bool = False
    actors: tuple[str, ...] = ()
    participant_roles: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    subject_state: str = ""
    required_observable_evidence: tuple[str, ...] = ()
    forbidden_contradictory_state: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_predicate": self.action_predicate,
            "cause_type": self.cause_type,
            "is_reference_death": self.is_reference_death,
            "actors": list(self.actors),
            "participant_roles": list(self.participant_roles),
            "objects": list(self.objects),
            "subject_state": self.subject_state,
            "required_observable_evidence": list(self.required_observable_evidence),
            "forbidden_contradictory_state": list(self.forbidden_contradictory_state),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "VisualEventState":
        if not isinstance(raw, Mapping):
            raise CaptionContractError("visual_event_state must be a mapping")
        return cls(
            action_predicate=str(raw.get("action_predicate", "")),
            cause_type=str(raw.get("cause_type", "")),
            is_reference_death=bool(raw.get("is_reference_death", False)),
            actors=tuple(str(item) for item in raw.get("actors", []) if str(item).strip()),
            participant_roles=tuple(str(item) for item in raw.get("participant_roles", []) if str(item).strip()),
            objects=tuple(str(item) for item in raw.get("objects", []) if str(item).strip()),
            subject_state=str(raw.get("subject_state", "")),
            required_observable_evidence=tuple(
                str(item) for item in raw.get("required_observable_evidence", []) if str(item).strip()
            ),
            forbidden_contradictory_state=tuple(
                str(item) for item in raw.get("forbidden_contradictory_state", []) if str(item).strip()
            ),
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
    visual_state: str = "generic_scene"
    presence_mode: str = "current"
    semantic_signature: str = ""
    visual_event_state: VisualEventState | None = None
    # FIX A (pilot R2.1): exact narrative participant cardinality for concrete
    # character scenes. A literal scene that names persistent characters must
    # tell the image model exactly how many story characters may be present.
    expected_visible_character_ids: tuple[str, ...] = ()
    expected_narrative_character_count: int = 0
    allow_unlisted_narrative_characters: bool = True

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
            "visual_state": self.visual_state,
            "presence_mode": self.presence_mode,
            "semantic_signature": self.semantic_signature,
            "visual_event_state": self.visual_event_state.to_dict() if self.visual_event_state is not None else None,
            "expected_visible_character_ids": list(self.expected_visible_character_ids),
            "expected_narrative_character_count": self.expected_narrative_character_count,
            "allow_unlisted_narrative_characters": self.allow_unlisted_narrative_characters,
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
            visual_state=str(raw.get("visual_state", "generic_scene")),
            presence_mode=str(raw.get("presence_mode", "current")),
            semantic_signature=str(raw.get("semantic_signature", "")),
            visual_event_state=(
                VisualEventState.from_mapping(raw["visual_event_state"])
                if isinstance(raw.get("visual_event_state"), Mapping)
                else None
            ),
            expected_visible_character_ids=tuple(
                str(item) for item in raw.get("expected_visible_character_ids", []) if str(item).strip()
            ),
            expected_narrative_character_count=int(raw.get("expected_narrative_character_count", 0) or 0),
            allow_unlisted_narrative_characters=bool(
                raw.get("allow_unlisted_narrative_characters", True)
            ),
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
    *,
    source_section_id: str | None = None,
) -> dict[str, Any] | None:
    """Bind a caption to the locked beat whose cue best overlaps its text.

    A caption is either an exact cue, a fragment of a cue, or a cue expanded
    into a longer line. We accept any mutual substring and prefer the SHORTEST
    matching cue (the most specific beat) so a caption is not accidentally
    bound to a long section-level beat that merely shares a clause.
    """

    norm = _bind_norm(caption_text)
    if not norm:
        return None
    matches: list[dict[str, Any]] = []
    best_cue_len: int | None = None
    for beat in beats:
        cue = _bind_norm(beat.get("cue", ""))
        if not cue:
            continue
        if source_section_id is not None:
            beat_section = str(beat.get("sectionId") or beat.get("section_id") or "")
            if beat_section != source_section_id:
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


# Entity alias lexicon.
#
# Phase 1 refactoring: this dict was previously hardcoded to 《活着》 (C001-C010
# mapped to 福贵/家珍/凤霞/etc.). Per the principle "LLM interprets literature;
# Python validates contracts", book-specific character aliases MUST be supplied
# by the Agent-derived Character Registry / Visual Foundation, never baked into
# the generic runtime. Only book-agnostic structural types remain here; the
# caller injects per-book aliases via entity_name_maps.
_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    # Book-agnostic structural referents kept as part of the generic lexicon.
    # FIX 2 (pilot R2) requires closing/theory captions that name concrete
    # drawable objects (炊烟/农舍/屋顶) to stay Literal, independent of the
    # injected per-book name maps.
    "OBJ_SMOKE": ("炊烟",),
    "OBJ_FARMHOUSE": ("农舍",),
    "OBJ_ROOF": ("屋顶",),
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
    # FIX 1 (pilot R2): 郎中/大夫 are caption-local anonymous medical roles,
    # not persistent identities; they must be drawable without a Cxxx anchor.
    "郎中": ("ROLE_DOCTOR", "doctor", ""),
    "大夫": ("ROLE_DOCTOR", "doctor", ""),
    "护士": ("ROLE_NURSE", "nurse", ""),
    "县长": ("ROLE_COUNTY_MAGISTRATE", "county_magistrate", ""),
    "孩子": ("ROLE_CHILD", "child", ""),
    # FIX 4 (pilot R2): 年轻人/青年 resolve to an anonymous local young-person
    # role so a caption like "一个下乡收集民谣的年轻人" can be generated
    # without requiring a persistent Visual Bible identity.
    "年轻人": ("ROLE_YOUNG_MAN", "young_man", "male"),
    "青年": ("ROLE_YOUNG_MAN", "young_man", "male"),
    # Author role is book-agnostic; the actual author name comes from the
    # project's Book Research / Character Registry, not hardcoded here.
}

# FIX 3 (pilot R2): display aliases for caption-local anonymous roles so a
# pronoun-resolved role renders as a human name, never a raw ROLE_ id.
_ROLE_DISPLAY_ALIASES: dict[str, tuple[str, ...]] = {
    "ROLE_DOCTOR": ("郎中", "大夫", "医生"),
    "ROLE_YOUNG_MAN": ("年轻人", "青年"),
    # ROLE_AUTHOR display name is injected from the project's book metadata.
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

_ANIMAL_MARKERS: tuple[str, ...] = ("老牛", "水牛", "黄牛", "耕牛", "牛犊", "马", "羊", "猪", "狗", "猫", "牲畜", "动物")


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
            if declared_type is not None and declared_type != "person":
                continue
            if _gender_conflicts(pronoun, declared_gender):
                continue
            candidates.append((entity_id, kind, term))
        elif _is_plural_group_candidate(pronoun, kind):
            candidates.append((entity_id, kind, term))
        elif pronoun == "它" and kind != "scene" and (
            kind != "char" or (declared_type is not None and declared_type != "person")
        ):
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


def _active_life_stage_signature(
    visible_ids: Sequence[str],
    character_register: Sequence[Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Return one active life-stage per story name for the visible characters.

    The register order is authoritative: when a caption resolves both a
    younger variant and its older self (e.g. C001/C002 都是“福贵”), the later
    registered stage wins so 青年→中年→老年 is one actor aging, never three
    random men.  Empty when no register is available (safe degradation).
    """

    if not character_register:
        return {}
    wanted = {str(item) for item in visible_ids if str(item).startswith("C")}
    if not wanted:
        return {}
    by_id: dict[str, tuple[str, str]] = {}
    ordered: list[str] = []
    for item in character_register:
        cid = str(item.get("character_id") or "").strip()
        if not cid:
            continue
        name = str(item.get("name") or item.get("prompt_subject") or cid).strip()
        stage = str(item.get("life_stage") or "").strip()
        by_id[cid] = (name, stage)
        if cid not in ordered:
            ordered.append(cid)
    active_by_name: dict[str, str] = {}
    for cid in ordered:
        if cid not in wanted:
            continue
        active_by_name[by_id[cid][0] or cid] = cid
    signature: dict[str, str] = {}
    for cid in sorted(active_by_name.values()):
        stage = by_id[cid][1]
        if stage:
            signature[cid] = stage
    return signature


def _continuity_scene_identity(
    *,
    visual_event_state: VisualEventState,
    action_semantics: Mapping[str, Any],
    location_id: str,
) -> str:
    """Derive the stable Scene Span container for one caption.

    The container is (event class, place), NOT the beat id and NOT the
    event_instance_id: micro-actions inside one event must never split a span,
    while a real event/place change does.
    """

    predicate = visual_event_state.action_predicate
    container = _EVENT_CONTAINERS.get(predicate, "")
    if container and container != "death_mention":
        return f"event:{container}:{location_id or ''}"
    hard = str(action_semantics.get("hard_split_event") or "none")
    if hard != "none":
        return f"event:{hard}:{location_id or ''}"
    return f"place:{location_id or ''}"


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


def _derive_visual_event_state(
    *,
    text: str,
    narrative_function: str,
    visual_mode: str,
    must_show: Sequence[CaptionEntityEvidence],
    location: str,
) -> VisualEventState:
    """FIX 1 (pilot R2): derive the observable visual event state for one caption.

    The rules are deterministic and keyword-driven; the first matching rule
    names the predicate, and every matching rule contributes required evidence
    (deduplicated). A concrete literal caption that yields no observable
    requirement at all fails closed rather than shipping a noun-only frame.
    """

    norm = _norm(text)
    actors = tuple(
        item.natural_language for item in must_show if _entity_kind(item.entity_id) in {"char", "group"}
    )
    roles = tuple(
        item.natural_language for item in must_show if str(item.entity_id).startswith("ROLE_")
    )
    objects = tuple(
        item.natural_language for item in must_show if _entity_kind(item.entity_id) == "obj"
    )
    matched: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    cause_type = ""
    is_reference_death = False
    if any(keyword in norm for keyword in _DEATH_KEYWORDS):
        cause_type = next(
            (
                cause
                for keywords, cause in _DEATH_CAUSE_RULES
                if any(keyword in norm for keyword in keywords)
            ),
            "unknown",
        )
        is_reference_death = (
            any(marker in norm for marker in _DEATH_REFERENCE_MARKERS)
            and not any(marker in norm for marker in _DEATH_ONSCREEN_MARKERS)
        )
        if is_reference_death:
            matched.append((
                "death_mention",
                "提及死亡（不画尸体、不倒伏）",
                (
                    "这是对死亡的回忆或提及，不出现尸体或倒地姿态",
                    "克制呈现，避免提前剧透或血腥",
                ),
                ("尸体或倒地姿态", "血腥特写"),
            ))
        else:
            matched.append((
                "death_aftermath",
                f"非血腥的死亡后果（cause={cause_type}）",
                _DEATH_EVIDENCE.get(cause_type, _DEATH_EVIDENCE["unknown"]),
                _DEATH_FORBIDDEN.get(cause_type, _DEATH_FORBIDDEN["default"]),
            ))
    for keywords, predicate, subject_state, evidence, forbidden in _EVENT_RULES:
        if any(keyword in norm for keyword in keywords) and predicate != "death_aftermath":
            matched.append((predicate, subject_state, evidence, forbidden))
    # 绑柱/枪决/开枪/倒下/死亡 is ONE Scene Span container.  When the
    # execution rule matches, fold generic death-aftermath and collapse into
    # execution_at_post so the event container stays stable inside the
    # execution sequence (用户标准范例：龙二执行段一张图).
    if any(predicate == "execution_at_post" for predicate, _state, _ev, _forb in matched):
        matched = [
            (
                predicate if predicate not in _EXECUTION_NORMALIZABLE_PREDICATES else "execution_at_post",
                state,
                evidence,
                forbidden,
            )
            for predicate, state, evidence, forbidden in matched
        ]
    evidence_seen: set[str] = set()
    evidence: list[str] = []
    forbidden_seen: set[str] = set()
    forbidden_states: list[str] = []
    for _predicate, _subject_state, rule_evidence, rule_forbidden in matched:
        for item in rule_evidence:
            if item not in evidence_seen:
                evidence_seen.add(item)
                evidence.append(item)
        for item in rule_forbidden:
            if item not in forbidden_seen:
                forbidden_seen.add(item)
                forbidden_states.append(item)

    if not matched and visual_mode == "literal":
        concrete_terms = [item.natural_language for item in must_show if item.natural_language]
        if concrete_terms:
            evidence.append("字幕点名的实体必须清晰可见：" + "、".join(concrete_terms))
        if location and location.strip():
            evidence.append("场景按字幕呈现：" + location.strip())
        # Fail-closed is enforced at task-build time (DirectorStageError) so a
        # single undrawable caption cannot block the whole contract document.

    predicate = matched[0][0] if matched else "caption_scene_state"
    subject_state = matched[0][1] if matched else ""
    return VisualEventState(
        action_predicate=predicate,
        cause_type=cause_type,
        is_reference_death=is_reference_death,
        actors=actors,
        participant_roles=roles,
        objects=objects,
        subject_state=subject_state,
        required_observable_evidence=tuple(evidence),
        forbidden_contradictory_state=tuple(forbidden_states),
    )


def merge_visual_event_states(
    contracts: Sequence["CaptionVisualContract"],
) -> dict[str, Any] | None:
    """Merge per-caption Visual Event States into one group-level record."""

    states = [
        contract.visual_event_state
        for contract in contracts
        if contract.visual_event_state is not None
    ]
    if not states:
        return None

    def ordered_union(items: Iterable[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            value = str(item).strip()
            if value and value not in out:
                out.append(value)
        return out

    return {
        # Culmination wins: a setup chain (离开 -> 归来 -> 死亡后果) is
        # summarised by its last non-generic predicate so the frame focus
        # reflects the event's outcome, not its setup.
        "action_predicate": next(
            (
                state.action_predicate
                for state in reversed(states)
                if state.action_predicate and state.action_predicate != "caption_scene_state"
            ),
            states[0].action_predicate,
        ),
        "cause_type": next(
            (state.cause_type for state in states if state.cause_type),
            "",
        ),
        "is_reference_death": any(state.is_reference_death for state in states),
        "actors": ordered_union(actor for state in states for actor in state.actors),
        "participant_roles": ordered_union(role for state in states for role in state.participant_roles),
        "objects": ordered_union(obj for state in states for obj in state.objects),
        # Culmination wins for the subject state too (a death group must not
        # report "归家/重逢" as its state because a return caption came first).
        "subject_state": next(
            (
                state.subject_state
                for state in reversed(states)
                if state.subject_state
            ),
            "",
        ),
        "required_observable_evidence": ordered_union(
            item for state in states for item in state.required_observable_evidence
        ),
        "forbidden_contradictory_state": ordered_union(
            item for state in states for item in state.forbidden_contradictory_state
        ),
    }


def infer_death_cause(text: str) -> str:
    """First cause keyword matched in the text (empty when no death-cause keyword)."""

    norm = _norm(text)
    return next(
        (
            cause
            for keywords, cause in _DEATH_CAUSE_RULES
            if any(keyword in norm for keyword in keywords)
        ),
        "",
    )


def _derive_visual_state(
    *,
    text: str,
    narrative_function: str,
    visual_mode: str,
    event_state: VisualEventState | None,
    must_show: Sequence[CaptionEntityEvidence],
) -> str:
    """One deterministic visual-state label per caption (single-frame coverability)."""

    norm = _norm(text)
    predicate = event_state.action_predicate if event_state is not None else ""
    cause = event_state.cause_type if event_state is not None else ""
    reference = event_state.is_reference_death if event_state is not None else False
    if predicate == "death_mention" or reference:
        return "death_mention"
    if predicate == "death_aftermath":
        return "death_aftermath"
    if any(keyword in norm for keyword in ("怀孕", "生孩子", "产房", "生啦", "大出血")):
        return "pregnancy_birth"
    if predicate == "wedding":
        return "wedding"
    if predicate == "medical_visit":
        return "medical_visit"
    # G061/62 region: donation chain has four physically distinct phases.
    if any(keyword in norm for keyword in ("大出血", "组织", "献血", "集合")):
        return "donation_setup"
    if any(keyword in norm for keyword in ("血型对", "涨红", "兴奋", "跑到门口", "喊")):
        return "donation_match"
    if any(keyword in norm for keyword in ("验血", "血型不对", "认错", "怯生生", "让进去", "十几个")):
        return "donation_wait"
    if any(keyword in norm for keyword in ("脱鞋", "第一个跑到", "拖到一边", "跑")):
        return "donation_rush"
    if any(keyword in norm for keyword in ("战场", "战争", "打仗", "围困", "枪声", "枪毙")):
        return "battlefield"
    if any(keyword in norm for keyword in ("壮丁", "当兵", "拉去", "被抓")):
        return "conscription"
    if (
        (event_state is not None and event_state.action_predicate == "blood_loss")
        or cause == "blood_loss"
        or any(keyword in norm for keyword in ("抽血", "献血"))
    ):
        if any(keyword in norm for keyword in ("脸白", "嘴唇白", "头晕", "虚弱", "苍白", "抽", "不停", "摔倒", "心跳", "死")):
            return "blood_loss"
    if any(keyword in norm for keyword in ("脸白", "嘴唇白", "头晕", "虚弱", "苍白")):
        return "blood_loss"
    if any(keyword in norm for keyword in ("歪在", "摔在", "倒下", "瘫")):
        return "collapse"
    if any(keyword in norm for keyword in _ALIVE_ACTIVE_KEYWORDS) and any(
        str(item.entity_id).startswith("C") for item in must_show
    ):
        # Speech/active captions with a named character ("听到苦根在背后说")
        # are an alive interaction even when a departure/return setup precedes
        # them inside the same caption or group.
        return "alive_active"
    if predicate == "departure_absence":
        return "departure_absence"
    if predicate == "return_home":
        return "return_home"
    if predicate == "folk_song_collection":
        return "folk_collection"
    if predicate == "smoke_rising":
        return "closing_hold"
    if predicate == "solitude_leftover":
        return "solitude"
    if predicate == "author_creation_context":
        return "author_hold"
    if narrative_function in {"theory", "transition", "closing"} and visual_mode == "symbolic_or_abstract":
        return "theory_hold"
    return "generic_scene"


def enrich_captions_to_contracts(
    *,
    script_sections: Sequence[Mapping[str, Any]],
    beats: Sequence[Mapping[str, Any]],
    captions: Sequence[Mapping[str, Any]],
    entity_name_maps: Iterable[Mapping[str, str]] = (),
    entity_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    character_register: Sequence[Mapping[str, Any]] | None = None,
) -> list[CaptionVisualContract]:
    """Derive the authoritative Caption Visual Contract for every caption.

    ``entity_name_maps`` is an ordered collection of id->display-name tables
    (for example character anchors, object anchors, scene anchors) used to
    resolve raw entity ids (``C002``, ``OBJ_BOOK``, ``SCENE_FIELD``) to human
    names for the contract's ``must_show`` list.

    ``character_register`` is the ordered HBG Bridge character list carrying
    ``character_id``/``name``/``life_stage``.  It is used to freeze one active
    life-stage per story name into ``scene_state.continuity_state`` so Scene
    Span building can split 青年→中年→老年 without splitting micro-actions.

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
        section_text_by_id[sid] = _bind_norm(section.get("text", ""))

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

        source_section = str(caption.get("source_section_id") or "").strip() or None
        if source_section is not None:
            # 对齐记录是权威；但文本必须仍出现在该 section（标点不敏感），否则
            # 证据与锁定脚本不一致 → fail-closed。
            if source_section not in section_text_by_id:
                raise CaptionContractError(
                    f"caption {caption_id} source_section_id {source_section!r} is not a locked script section"
                )
            if not section_text_by_id[source_section] or _bind_norm(text) not in section_text_by_id[source_section]:
                raise CaptionContractError(
                    f"caption {caption_id} ({text[:24]!r}) text does not occur in its aligned section {source_section}"
                )
            section_id = source_section
            beat = _best_beat_for_caption(text, beat_list, section_text_by_id, source_section_id=source_section)
        else:
            beat = _best_beat_for_caption(text, beat_list, section_text_by_id)
            if beat is None:
                section_matches = [
                    section_id
                    for section_id, section_text in section_text_by_id.items()
                    if section_text and _bind_norm(text) in section_text
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
        # FIX 3 (pilot R2): author-creation metadata under a plot register is a
        # locked-metadata defect. Do not silently reinterpret it to
        # author_background; name the section so the script metadata is fixed.
        author_marker = next((marker for marker in _AUTHOR_CREATION_MARKERS if marker in text), None)
        if author_marker and narrative_function in _LITERAL_FUNCTIONS:
            raise CaptionContractError(
                f"caption {caption_id} ({text[:24]!r}) contains explicit author-creation "
                f"metadata {author_marker!r} but its locked script section {section_id} "
                f"is registered {raw_section_nf!r} (plot register). Correct the "
                "script/section metadata to author_background before generating; the "
                "pipeline will not silently reinterpret it."
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
        if not must_show:
            event_only = _event_only_subject(text)
            if event_only is not None:
                entity_id, natural_language, matched = event_only
                must_show.append(_entity_evidence(
                    entity_id,
                    natural_language=natural_language,
                    reason="event_only_subject",
                    evidence={"text_evidence": matched, "caption_id": caption_id},
                ))

        # FIX 3 (pilot R2): for "写《活着》之前" style author-background clauses
        # the finished book (and the author's face) must not be primary
        # referents -- the image illustrates the act of creation before the
        # book exists. Demotion happens after pronoun resolution so both stay
        # auditable, then both move to may_show context.
        creation_before = (
            narrative_function == "author_background"
            and bool(_CREATION_BEFORE_TITLE_RE.search(text))
        )
        names_author = (
            narrative_function == "author_background"
            and any(role_id == "ROLE_AUTHOR" for role_id, _term, _evidence in local_roles)
        )
        demoted_referents: list[CaptionEntityEvidence] = []

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
                    # FIX 4 (pilot R2): a caption-local anonymous role named in
                    # the same caption (e.g. 余华 for "余华说…") is a valid
                    # pronoun antecedent and must not fall through to a story
                    # character from the Beat context. The role must appear
                    # BEFORE the pronoun inside the SAME clause ("他爹是老爷，他
                    # 是少爷" keeps 爹/老爷/少爷 as predicates, not antecedents).
                    local_role_lookup = {role_id: evidence for role_id, _term, evidence in local_roles}
                    pronoun_clause = next(
                        (
                            clause for clause in _caption_clauses(text)
                            if clause["start"] <= pronoun_start < clause["end"]
                        ),
                        None,
                    )
                    same_caption_candidates = same_caption_candidates + [
                        (role_id, "char", term)
                        for role_id, term, _evidence in local_roles
                        if not _gender_conflicts(
                            pronoun,
                            local_role_lookup.get(role_id, {}).get("gender"),
                        )
                        and pronoun_clause is not None
                        and pronoun_clause["start"]
                        <= int(local_role_lookup[role_id]["caption_span"]["start"])
                        < pronoun_start
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

        # FIX 3 (pilot R2): demote the finished book (creation-before) and the
        # author's face (author-background register) after pronoun resolution.
        if creation_before or names_author:
            demoted_referents = [
                item for item in must_show
                if item.entity_id in {"OBJ_BOOK", "ROLE_AUTHOR"}
            ]
            must_show = [
                item for item in must_show
                if item.entity_id not in {"OBJ_BOOK", "ROLE_AUTHOR"}
            ]

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

        # FIX 2 (pilot R2): Narrative Function decides narrative treatment, not
        # whether the caption has visible referents. A closing/theory caption
        # that names concrete drawable referents (炊烟/农舍/屋顶/牛/灯/雨/河)
        # stays Literal; only referent-less captions may fall to symbolic/
        # abstract. Author-creation "before the book exists" captions keep the
        # demoted OBJ_BOOK out of the concreteness decision.
        # FIX 2 (pilot R2): Literal requires concrete referents in the caption
        # itself, for every register. An opening/plot caption that names
        # nothing drawable is not Literal-with-empty-must_show; it falls to
        # symbolic_or_abstract (and is rejected downstream if the register
        # cannot justify an atmosphere frame). "Before the book was written"
        # author-background clauses are always symbolic: the finished book and
        # the author's face are demoted context, not drawable must-show.
        concrete_ids = {
            entity_id for entity_id in caption_found
            if not (demoted_referents and entity_id in {"OBJ_BOOK", "ROLE_AUTHOR"})
        }
        effective_roles = [
            (role_id, term, evidence)
            for role_id, term, evidence in local_roles
            if not ((creation_before or names_author) and role_id == "ROLE_AUTHOR")
        ]
        # FIX 1 (pilot R2): an observable event keyword ("下地去了", "枪毙",
        # "请郎中", "炊烟袅袅") is itself a concrete drawable claim even when
        # the caption names no entity. It keeps the caption Literal so the
        # event evidence is carried into the prompt.
        event_rule_hit = any(
            any(keyword in _norm(text) for keyword in keywords)
            for keywords, _predicate, _state, _evidence, _forbidden in _EVENT_RULES
        )
        concrete_referents = bool(concrete_ids or effective_roles or local_groups or event_rule_hit)
        visual_mode = (
            "symbolic_or_abstract"
            if (creation_before or names_author)
            else ("literal" if concrete_referents else "symbolic_or_abstract")
        )
        scene_eid = next((eid for eid in caption_found if eid.startswith("SCENE_")), "")
        location_id = scene_eid or _first_scene(required)
        location = _entity_display(location_id, maps) if location_id else beat_context.get("location") or section.get("chapterTitle", "")
        if demoted_referents:
            may_show_context = tuple(
                _entity_evidence(
                    item.entity_id,
                    natural_language=item.natural_language,
                    reason="author_creation_temporal_context",
                    evidence={
                        "caption_id": caption_id,
                        "caption_text": text,
                        "note": "named by a 'before writing' clause; context only, never the primary referent",
                    },
                )
                for item in demoted_referents
            )
            may_show = may_show_context + may_show
        actions = _split_sentences(beat_context.get("description", "") or text)[:3] or [text]
        visible_character_ids = [
            item.entity_id for item in must_show if _entity_kind(item.entity_id) in {"char", "group"}
        ]
        visual_event_state = _derive_visual_event_state(
            text=text,
            narrative_function=narrative_function,
            visual_mode=visual_mode,
            must_show=must_show,
            location=str(location),
        )
        visual_state = _derive_visual_state(
            text=text,
            narrative_function=narrative_function,
            visual_mode=visual_mode,
            event_state=visual_event_state,
            must_show=must_show,
        )
        presence_mode = (
            "memory"
            if narrative_function in {"theory", "author_background"}
            and any(str(item.entity_id).startswith("C") for item in must_show)
            else "current"
        )
        # FIX A (pilot R2.1): exact participant cardinality. Persistent
        # characters named by a literal contract are the only narrative
        # characters allowed in the foreground/midground.
        expected_character_ids = tuple(
            sorted({
                item.entity_id for item in must_show
                if item.entity_id.startswith("C")
            })
        )
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
                "continuity_state": {
                    "pronoun_resolutions": pronoun_evidence,
                    "life_stage": _active_life_stage_signature(
                        visible_character_ids, character_register
                    ),
                    "scene_identity": _continuity_scene_identity(
                        visual_event_state=visual_event_state,
                        action_semantics=_derive_action_semantics(
                            caption=caption,
                            caption_id=caption_id,
                            section_id=section_id,
                            beat=beat,
                        ),
                        location_id=location_id,
                    ),
                    "scene_break_before": bool(
                        caption.get("scene_break_before")
                        or beat_context.get("scene_break_before")
                        or beat_context.get("sceneBreakBefore")
                    ),
                },
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
            visual_state=visual_state,
            presence_mode=presence_mode,
            visual_event_state=visual_event_state,
            expected_visible_character_ids=expected_character_ids,
            expected_narrative_character_count=len(expected_character_ids),
            allow_unlisted_narrative_characters=not (
                visual_mode == "literal" and len(expected_character_ids) > 0
            ),
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
    # Canonical serialization: the contract is a hash-tracked artifact, so the
    # byte form must be byte-identical to the Phase-4 finalize writer (_pretty
    # emits sort_keys=True UTF-8 with LF newlines). write_text would translate
    # LF to CRLF on Windows, silently breaking the manifest integrity hash, so
    # write raw bytes instead.
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(payload)
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
_PROJECT_HBG_BRIDGE_INPUT_RELATIVE = "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json"


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
                if "entity_type" not in values:
                    animal_hits = [
                        marker
                        for field in ("prompt_subject", "name")
                        for marker in _ANIMAL_MARKERS
                        if isinstance(anchor.get(field), str) and marker in anchor[field]
                    ]
                    if animal_hits:
                        values["entity_type"] = "animal"
                        values["animal_type_evidence"] = {
                            "anchor_id": anchor_id,
                            "marker": animal_hits[0],
                            "profile_sha256": profile_sha256,
                        }
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
    caption_bindings: Mapping[str, Any] | None = None,
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
    cap_doc = (
        dict(caption_bindings)
        if caption_bindings is not None
        else _load_project_json(root, _PROJECT_CAPTION_BINDINGS_RELATIVE)
    )
    captions_raw = cap_doc.get("captions", {})
    captions = list(captions_raw.values()) if isinstance(captions_raw, dict) else list(captions_raw)
    if not isinstance(captions, list) or not captions:
        raise CaptionContractError("CAPTION_BINDINGS.json contains no captions")
    profile_path = root / _PROJECT_VISUAL_PROFILE_RELATIVE
    profile = _load_project_json(root, _PROJECT_VISUAL_PROFILE_RELATIVE)
    profile_sha256 = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    name_maps = _build_project_entity_name_maps(profile)
    # Life-stage lineage comes from the locked HBG Bridge character register:
    # C001/C002 (青年/中年福贵) must resolve as one actor aging, never as
    # unrelated characters. The register is optional so legacy projects without
    # a bridge input keep working (helper degrades to empty).
    character_register: list[Mapping[str, Any]] = []
    bridge_path = root / _PROJECT_HBG_BRIDGE_INPUT_RELATIVE
    if bridge_path.is_file():
        try:
            bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CaptionContractError(f"contract input unreadable: {_PROJECT_HBG_BRIDGE_INPUT_RELATIVE}: {error}") from error
        characters = bridge.get("characters") if isinstance(bridge, Mapping) else None
        if isinstance(characters, list):
            character_register = [item for item in characters if isinstance(item, Mapping)]
    if not release_id:
        release_id = cap_doc.get("release_id") or package.get("release_id") or "unknown"
    contracts = enrich_captions_to_contracts(
        script_sections=sections,
        beats=beats,
        captions=captions,
        entity_name_maps=name_maps,
        entity_metadata=_build_project_entity_metadata(profile, profile_sha256=profile_sha256),
        character_register=character_register,
    )
    document = build_caption_visual_contract_document(release_id=release_id, contracts=contracts)
    if validate_only:
        return document
    return write_caption_visual_contract_document(root, release_id=release_id, contracts=contracts)
