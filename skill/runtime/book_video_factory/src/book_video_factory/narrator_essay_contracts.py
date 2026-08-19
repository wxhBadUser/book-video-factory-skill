"""narrator-essay 正式合同校验器。

每个合同有：生产者（Phase 3—6 模块）、消费者（gates/装配/写作）、校验入口（本文件）、
workflow gate（release profile required_publish_approvals）、测试（test_narrator_essay_contracts）、
失败行为（抛 ContractError，gate 标 script_contract/content_quality=false，fail closed）。

"""
from __future__ import annotations

import re
from typing import Any

# Phase 1: these are fine-grained beat/section-level functions for the script
# layer. The scene-level NARRATIVE_FUNCTIONS registry (narrative_functions.py)
# is the single authority for the visual-semantic layer (SceneSpec, CaptionCue,
# director stage). These script-section functions are a separate taxonomy and
# must not be confused with the production narrative function registry.
ALLOWED_SCRIPT_SECTION_FUNCTIONS = {
    "hook", "world_setup", "character_entry", "desire", "choice", "reward",
    "cost", "escalation", "transition", "midpoint_requestion", "confession",
    "revelation", "reinterpretation", "theory", "modern_mirror", "ending_image",
}
ALLOWED_VOICE_PROVIDERS = {"edge-tts", "external-recording"}
ALLOWED_CHECK_TYPES = {"exact_check", "heuristic_check", "human_judgment"}
CREATIVE_SCORE_KEYS = (
    "audience_resonance", "textual_evidence", "emotional_range",
    "midpoint_power", "reinterpretation_power", "modern_mapping",
    "visual_potential", "originality", "character_complexity",
    "ending_specificity",
)
EVENT_CARD_REQUIRED = (
    "sensory_objects", "character_actions", "narrator_reaction",
    "hammer_line", "next_question", "source_ids", "visual_function",
)


class ContractError(ValueError):
    """narrator-essay 合同校验失败。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractError(msg)


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


# --- creative-decision.v1 ---
def validate_creative_decision(payload: dict[str, Any]) -> None:
    routes = payload.get("candidate_routes")
    _require(isinstance(routes, list) and len(routes) == 3,
             "candidate_routes must contain exactly 3 routes (三条路线)")
    for i, route in enumerate(routes):
        _require(isinstance(route, dict), f"candidate_routes[{i}] must be an object")
        _require(_is_nonempty_str(route.get("click_question")),
                 f"candidate_routes[{i}].click_question required")
        _require(_is_nonempty_str(route.get("deep_thesis")),
                 f"candidate_routes[{i}].deep_thesis required")
        _require(
            route.get("click_question") != route.get("deep_thesis"),
            f"candidate_routes[{i}] click_question and deep_thesis must differ (点击问题与深层命题必须分离)",
        )
        scores = route.get("scores")
        _require(isinstance(scores, dict), f"candidate_routes[{i}].scores required")
        for k in CREATIVE_SCORE_KEYS:
            v = scores.get(k)
            _require(isinstance(v, (int, float)) and 1 <= v <= 5,
                     f"candidate_routes[{i}].scores.{k} must be 1..5 (评分)")
    sel = payload.get("selected_route_id")
    ids = [r.get("route_id") for r in routes]
    _require(sel in ids, "selected_route_id must be among candidate_routes (selected_route)")


# --- fate-anchors.v1 ---
def validate_fate_anchors(payload: dict[str, Any]) -> None:
    cec = payload.get("candidate_event_count")
    _require(isinstance(cec, int) and 20 <= cec <= 40,
             "candidate_event_count must be 20..40 (候选事件)")
    anchors = payload.get("anchors")
    _require(isinstance(anchors, list) and 8 <= len(anchors) <= 12,
             "anchors count must be 8..12 (命运锚点)")
    for i, a in enumerate(anchors):
        _require(isinstance(a, dict), f"anchors[{i}] must be an object")
        funcs = a.get("functions")
        _require(isinstance(funcs, list) and len(funcs) >= 2,
                 f"anchors[{i}].functions must have at least 2 (每锚点至少两项功能)")


# --- event-cards.v1 ---
def validate_event_cards(payload: dict[str, Any]) -> None:
    cards = payload.get("cards")
    _require(isinstance(cards, list) and len(cards) >= 1, "cards must be a nonempty list")
    for i, c in enumerate(cards):
        _require(isinstance(c, dict), f"cards[{i}] must be an object")
        for field in EVENT_CARD_REQUIRED:
            val = c.get(field)
            ok = (
                (isinstance(val, list) and len(val) >= 1)
                or _is_nonempty_str(val)
            )
            _require(ok, f"cards[{i}].{field} required (事件卡七要素: {field})")


# --- script-beats.v1 ---
def validate_script_beats(payload: dict[str, Any]) -> None:
    beats = payload.get("beats")
    _require(isinstance(beats, list) and len(beats) >= 1, "beats must be a nonempty list")
    for i, b in enumerate(beats):
        _require(isinstance(b, dict), f"beats[{i}] must be an object")
        _require(_is_nonempty_str(b.get("voice_chunk_id")),
                 f"beats[{i}].voice_chunk_id required (beat→voice 外键)")
        nf = b.get("narrative_function")
        _require(nf in ALLOWED_SCRIPT_SECTION_FUNCTIONS,
                 f"beats[{i}].narrative_function '{nf}' not allowed (narrative_function)")


# --- visual-bible.v1 ---
def validate_visual_bible(payload: dict[str, Any]) -> None:
    _require(_is_nonempty_str(payload.get("core_metaphor")),
             "core_metaphor required (核心隐喻)")
    forbidden = payload.get("forbidden_styles")
    _require(isinstance(forbidden, list) and len(forbidden) >= 1,
             "forbidden_styles must list at least one (禁用风格/不可混用)")


# --- voice-direction.narrator.v1 ---
def validate_voice_direction(payload: dict[str, Any]) -> None:
    provider = payload.get("provider")
    _require(provider in ALLOWED_VOICE_PROVIDERS,
             f"provider must be one of edge-tts/external-recording (provider)")
    chunks = payload.get("chunks")
    _require(isinstance(chunks, list) and len(chunks) >= 1, "chunks must be a nonempty list")


# --- content-quality-report.v1 ---
def validate_content_quality_report(payload: dict[str, Any]) -> None:
    checks = payload.get("checks")
    _require(isinstance(checks, list) and len(checks) >= 1, "checks must be a nonempty list")
    for i, c in enumerate(checks):
        _require(isinstance(c, dict), f"checks[{i}] must be an object")
        ct = c.get("check_type")
        _require(ct in ALLOWED_CHECK_TYPES,
                 f"checks[{i}].check_type must be exact_check/heuristic_check/human_judgment")


# --- script.narrator-essay.v1 ---
def validate_narrator_essay_script(payload: dict[str, Any]) -> None:
    nr = payload.get("narrator_ratio")
    _require(isinstance(nr, (int, float)) and 0.85 <= nr <= 0.95,
             "narrator_ratio must be 0.85..0.95 (主播叙述 85%—95%)")
    dr = payload.get("dialogue_ratio")
    _require(isinstance(dr, (int, float)) and 0.05 <= dr <= 0.15,
             "dialogue_ratio must be 0.05..0.15 (对白 5%—15%)")
    mc = payload.get("main_concept_count")
    _require(isinstance(mc, int) and mc <= 1,
             "main_concept_count must be <= 1 (一主概念限制)")
    _require(payload.get("has_midpoint_requestion") is True,
             "has_midpoint_requestion must be true (中点再开题)")
    _require(payload.get("has_evidence_reinterpretation") is True,
             "has_evidence_reinterpretation must be true (证据翻案)")
    src = payload.get("reinterpretation_source_ids")
    _require(isinstance(src, list) and len(src) >= 1,
             "reinterpretation_source_ids must be nonempty (翻案必须有 source 证据)")
    _require(isinstance(payload.get("performance_version"), dict),
             "performance_version required (表演稿)")
    _require(isinstance(payload.get("audit_version"), dict),
             "audit_version required (审计稿)")
    _require(isinstance(payload.get("release_version"), dict),
             "release_version required (发布稿)")

    versions = {
        "performance_version": payload["performance_version"],
        "release_version": payload["release_version"],
        "audit_version": payload["audit_version"],
    }
    graphs: dict[str, list[str]] = {}
    for version_name, version in versions.items():
        sections = version.get("sections")
        _require(isinstance(sections, list) and sections,
                 f"{version_name}.sections must be nonempty")
        ids: list[str] = []
        for index, section in enumerate(sections):
            _require(isinstance(section, dict),
                     f"{version_name}.sections[{index}] must be an object")
            section_id = section.get("section_id")
            _require(_is_nonempty_str(section_id),
                     f"{version_name}.sections[{index}].section_id required")
            _require(section_id not in ids,
                     f"{version_name} duplicate section_id: {section_id}")
            ids.append(section_id)
            _require(_is_nonempty_str(section.get("text")),
                     f"{version_name}.sections[{index}].text required")
            _require(_is_nonempty_str(section.get("narrative_function")),
                     f"{version_name}.sections[{index}].narrative_function required")
        graphs[version_name] = ids
        joined = "".join(str(section["text"]) for section in sections)
        _require(version.get("text") == joined,
                 f"{version_name}.text must equal its ordered section text")
    _require(len({tuple(ids) for ids in graphs.values()}) == 1,
             "performance/release/audit section graph must match")
    midpoint_section_id = payload.get("midpoint_section_id")
    _require(_is_nonempty_str(midpoint_section_id) and midpoint_section_id in graphs["release_version"],
             "midpoint_section_id must exist in the section graph")
    release_text = payload["release_version"]["text"]
    engineering_tag = re.compile(r"【[^】]+】|\[[A-Za-z_ -]+\]|<[^>]+>")
    _require(not engineering_tag.search(release_text),
             "release_version contains an engineering tag")
    _require(payload.get("script_text") == release_text,
             "script_text must equal release_version.text")
    for index, section in enumerate(payload["audit_version"]["sections"]):
        source_ids = section.get("source_ids")
        _require(isinstance(source_ids, list) and source_ids,
                 f"audit_version.sections[{index}].source_ids required")

    # Hard gate: opening must not leak deep_thesis.
    # auto_fail — NOT overridable by the 15-item quality score.
    st = payload.get("script_text")
    if st:
        validate_narrator_essay_opening({
            "script_text": st,
            "opening_fraction": payload.get("opening_fraction", 0.15),
            "deep_thesis_leak_tokens": payload.get("deep_thesis_leak_tokens"),
        })


# --- narrator-essay discovery-narrative text gates (v1.1) ---
#
# 这些门禁来自六案例最关键却被结构训练掩盖的能力："发现式叙事"。
# 它们直接消费口播稿正文（script_text），由 gates / 装配 / 文学审查在 release 前调用。
# 任一 auto_fail 不得被 15 项总分覆盖。

# 开头 15% 若出现以下任一 token，即视为 deep_thesis 泄漏（最高优先级硬门禁）。
# Phase 1 refactoring: book-specific metaphor tokens were removed from the
# generic runtime. These are now supplied per-book by the Creative Route /
# Agent, not baked into the contract module.
DEEP_THESIS_LEAK_TOKENS = [
    "这本书真正写的是", "真正写的是", "其实写的是", "本质上写的是",
    "这本书其实在写", "深层命题", "社会学结论", "心理学结论",
]

# 主隐喻在正式揭示前，前半段禁止持续提醒观众的解释词。
# Phase 1: book-specific tokens removed; per-book forbidden tokens are
# injected by the Creative Route configuration.
FORBIDDEN_FIRST_HALF_TOKENS = []

# 把句子判定为"分析/讲解"的短语标记（用于 Story-First 与聊天松弛度）。
ANALYSIS_MARKERS = [
    "这意味着", "这证明", "这构成", "这揭示", "本质上", "由此可见",
    "换言之", "换句话说", "也就是说", "这本书真正写的是", "其实写的是",
    "请注意", "这就说明", "说到底", "从根上",
]

# 合格的主播露己反应（指向主播自身的感受/困惑，而非结构讲解）。
SELF_REVEAL_QUALIFIED = [
    "读到这里", "说真的", "我后来反复想过", "我心里突然", "替她憋屈",
    "最恨的甚至", "说实话", "我其实", "我第一次看", "我读到这儿",
    "我心里", "我忍不住", "我承认",
]
# 不合格（这些是结构讲解，不算主播反应）。
SELF_REVEAL_STRUCTURAL = [
    "请注意", "你发现没有", "这里我要停下来", "这两件事构成",
    "这两件事真正", "我们回到",
]

# 自然说话连接句（聊天松弛度）。
NATURAL_CONNECTORS = [
    "可问题来了", "但事情还没完", "按说到这里", "没有。", "更麻烦的事情还在后面",
    "你先记住这个人", "可是", "不过", "但偏偏", "偏偏", "说白了", "其实说",
]
ESSAY_DENSE_CONNECTORS = [
    "这意味着", "这证明", "这构成", "这揭示", "本质上", "由此可见", "换言之",
]


def _char_positions(text: str, token: str) -> list[int]:
    out = []
    start = 0
    while True:
        idx = text.find(token, start)
        if idx == -1:
            break
        out.append(idx)
        start = idx + 1
    return out


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?；;\n]", text)
    return [p.strip() for p in parts if p.strip()]


def validate_narrator_essay_opening(payload: dict[str, Any]) -> None:
    """HARD GATE（最高优先级）：开头 15% 正文不得泄漏 deep_thesis。

    auto_fail = deep_thesis_leaked_in_opening；不得靠 15 项总分覆盖。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    frac = float(payload.get("opening_fraction", 0.15))
    tokens = payload.get("deep_thesis_leak_tokens") or DEEP_THESIS_LEAK_TOKENS
    cut = int(len(text) * frac)
    head = text[:cut]
    for tok in tokens:
        if tok in head:
            raise ContractError(
                f"auto_fail deep_thesis_leaked_in_opening: token '{tok}' found in opening "
                f"{int(frac * 100)}% (hard gate, not overridable by total score)"
            )


def validate_metaphor_budget(payload: dict[str, Any]) -> None:
    """主隐喻预算：揭示位置、前半段禁词、揭示前显式重复次数。"""
    text = payload.get("script_text", "")
    metaphor = payload.get("primary_metaphor", "")
    if not text or not metaphor:
        return
    carries_reframing = bool(payload.get("metaphor_carries_reframing", False))
    min_reveal = 0.65 if carries_reframing else 0.55
    total = len(text)
    positions = _char_positions(text, metaphor)
    if not positions:
        raise ContractError("metaphor_budget: primary_metaphor never explicitly revealed")
    reveal = positions[0] / total
    if reveal < min_reveal:
        raise ContractError(
            f"metaphor_budget: primary_metaphor '{metaphor}' first revealed at {reveal:.1%}, "
            f"must be >= {min_reveal:.0%} (carries_reframing={carries_reframing})"
        )
    # 前半段（中点前）不得出现解释词，也不得出现主隐喻本身。
    mid = float(payload.get("midpoint_position", 0.50))
    head = text[: int(total * mid)]
    for tok in FORBIDDEN_FIRST_HALF_TOKENS:
        if tok in head:
            raise ContractError(
                f"metaphor_budget: forbidden token '{tok}' appears before midpoint "
                f"(first half must keep only the story's own language)"
            )
    # 正式揭示前，主隐喻显式重复 <= 2 次。
    before_mid = [p for p in positions if p < int(total * mid)]
    if len(before_mid) > 2:
        raise ContractError(
            f"metaphor_budget: {len(before_mid)} explicit metaphor mentions before midpoint (>2)"
        )


def validate_story_first_ratio(payload: dict[str, Any]) -> None:
    """Story-First 强制：前 70% 正文 story>=80% / analysis<=20%；局部 5—7 句最多 1 句解释。"""
    text = payload.get("script_text", "")
    if not text:
        return
    frac = float(payload.get("first_body_fraction", 0.70))
    min_story = float(payload.get("min_story_ratio", 0.80))
    body = text[: int(len(text) * frac)]
    sents = _split_sentences(body)
    if len(sents) < 5:
        return
    analysis = [s for s in sents if any(m in s for m in ANALYSIS_MARKERS)]
    ratio = (len(sents) - len(analysis)) / len(sents)
    if ratio < min_story:
        raise ContractError(
            f"story_first_ratio: story sentences {ratio:.1%} < {min_story:.0%} in first "
            f"{int(frac * 100)}%"
        )
    win = int(payload.get("local_window", 7))
    local_max = int(payload.get("local_analysis_max", 1))
    for i in range(0, len(sents) - win + 1):
        w = sents[i:i + win]
        a = sum(1 for s in w if any(m in s for m in ANALYSIS_MARKERS))
        if a > local_max:
            raise ContractError(
                f"story_first_ratio: window of {win} sentences has {a} analysis sentences "
                f"(>{local_max}) at position {i}"
            )


def validate_narrator_self_reveal(payload: dict[str, Any]) -> None:
    """主播露己硬要求：完整 17—20 分钟稿件 5—8 处真人反应，其中 ≥3 处暴露自己。

    硬下限 3（暴露自己的反应必须达标），上限放宽到 10——与金标准"5—8 处反应"
    对齐，允许 8 处全为暴露自己；只对"通篇几乎全是反应"（>10）才拦截，防注水。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    sents = _split_sentences(text)
    qualified = 0
    for s in sents:
        if any(q in s for q in SELF_REVEAL_QUALIFIED) and not any(b in s for b in SELF_REVEAL_STRUCTURAL):
            qualified += 1
    if qualified < 3 or qualified > 10:
        raise ContractError(
            f"narrator_self_reveal: {qualified} qualified reactions (need 3-10; "
            f"gold standard wants 5-8 total reactions, ≥3 self-exposing); "
            f"'请注意/你发现没有/这里我要停下来/这两件事构成' are structural, not reactions"
        )


def validate_chat_relaxation(payload: dict[str, Any]) -> None:
    """聊天松弛度：自然连接句占比 >= 30%；禁止连续 3+ 论文式连接。"""
    text = payload.get("script_text", "")
    if not text:
        return
    sents = _split_sentences(text)
    natural = sum(1 for s in sents if any(n in s for n in NATURAL_CONNECTORS))
    dense = sum(1 for s in sents if any(d in s for d in ESSAY_DENSE_CONNECTORS))
    total_conn = natural + dense
    if total_conn == 0:
        return
    natural_ratio = natural / total_conn
    if natural_ratio < 0.30:
        raise ContractError(
            f"chat_relaxation: natural connectors {natural_ratio:.1%} < 30% "
            f"(essay_explanation_density too high)"
        )
    consec = 0
    max_consec = 0
    for s in sents:
        if any(d in s for d in ESSAY_DENSE_CONNECTORS):
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    if max_consec >= 3:
        raise ContractError(
            f"chat_relaxation: {max_consec} consecutive essay-dense connectors (max 2)"
        )


def validate_evidence_before_thesis(payload: dict[str, Any]) -> None:
    """理论必须是发现，不是预设：deep_thesis 关键论据至少 70% 先于理论完整公布。"""
    text = payload.get("script_text", "")
    evidence_tokens = payload.get("evidence_tokens") or []
    if not text or not evidence_tokens:
        return
    thesis_position = float(payload.get("thesis_position",
                                        payload.get("metaphor_reveal_position", 0.70)))
    cut = int(len(text) * thesis_position)
    before = text[:cut]
    published = [t for t in evidence_tokens if t in before]
    ratio = len(published) / len(evidence_tokens)
    if ratio < 0.70:
        raise ContractError(
            f"evidence_before_thesis: only {ratio:.1%} of key arguments published before "
            f"thesis (<70%); discovery narrative requires evidence before theory"
        )


def validate_series_brand_opener(payload: dict[str, Any]) -> None:
    """Series Brand Opener 必须恢复：前 ~45 秒自然完成 名著值得读 + 普通人难读 + 系列讲懂。"""
    text = payload.get("script_text", "")
    if not text:
        return
    window = int(payload.get("brand_opener_window_chars", 340))
    head = text[:window]
    g1 = any(t in head for t in ["名著", "经典", "值得读", "伟大", "好书", "此生必读"])
    g2 = any(t in head for t in ["难读", "读不进去", "读不下去", "啃不动", "普通人", "真读进去", "真正读进去", "没翻开", "拖了很多年", "难的是", "一直想读"])
    g3 = any(t in head for t in ["系列", "栏目", "我们负责", "讲懂", "真正讲", "这档", "这个系列"])
    missing = []
    if not g1:
        missing.append("worth_reading")
    if not g2:
        missing.append("hard_to_read")
    if not g3:
        missing.append("series_promise")
    if missing:
        raise ContractError(
            f"series_brand_opener: missing groups {missing} in first {window} chars "
            f"(need 名著值得读 + 普通人难读 + 系列讲懂)"
        )


# 合格的中点升级标记（"障碍消失/已成功却更不满足/已死冲突才开始"式升级措辞）。
MIDPOINT_UPGRADE_MARKERS = [
    "明明", "已经", "却", "可赢到手", "赢了", "成功", "赢了之后",
    "障碍", "清空", "没有了", "死了", "死后", "已死", "到头来",
    "赢了不等于", "还不等于", "不是结束", "才刚刚开始", "真正",
    # 焦点转移式升级（v1.2 校准）：外部障碍/事实已定，问题却转向别处。
    "反而", "反倒", "怎么越", "怎么反而", "怎么反倒", "全认了",
    "却不", "却越", "越审", "越不像", "原来", "真正的问题",
]

# 阅读痛点词（只谈"名著难读"，不是"人类命运问句"）。
READING_PAIN_TOKENS = [
    "名著", "太难读", "读不进去", "读不下去", "啃不动", "读两页",
    "太难看懂", "值得读", "经典", "好书", "看不完",
]

# 现代解释锚定标记：理论段必须引用本书独有的象征词，而不是纯通用概念。
# Phase 1: book-specific tokens removed from generic runtime; per-book
# anchor hints are injected by the Creative Route / Book Research.
THEORY_ANCHOR_HINT_MARKERS = [
    "大海", "月亮", "六便士", "窄门", "庄园", "窗", "荒原",
    "草地", "玫瑰花", "星球", "鱼", "船", "手",
]


def _split_sentences_with_marks(text: str) -> list[str]:
    """按句子结束符切句，保留结束符（问号等）以便判断问句类型。"""
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p.strip()]


def _window_questions(text: str) -> list[str]:
    """取一个文本窗口里所有含问号的句子（保留问号）。"""
    return [s for s in _split_sentences_with_marks(text) if "？" in s or "?" in s]


def validate_click_question_sharp(payload: dict[str, Any]) -> None:
    """G1 点击问题必须锋利（范本级）：开头必须有"当代可辩论命运问句"，且位置前移。

    旧 skill 稿缺陷：开头停在"名著难读"的阅读痛点，没有观众能当场站队的
    "人类命运问句"（如"拼一辈子到底图什么"）。金标准范本（漂亮朋友"长得好看
    重要吗"）第一句即站队，命运问句不得埋到开头 15% 深处。
    硬门禁：开头无 ? 问句，或问句只谈阅读（名著难读），或命运问句位置 > 12% → 打回。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    frac = float(payload.get("opening_fraction", 0.15))
    head = text[: int(len(text) * frac)]
    questions = _window_questions(head)
    if not questions:
        raise ContractError(
            "click_question_sharp: opening 15% has no '?' — need a debatable fate "
            "question (why/what/how), not just a reading-pain statement"
        )
    # 问句不能只谈"阅读困难"。
    for q in questions:
        if any(t in q for t in READING_PAIN_TOKENS):
            raise ContractError(
                "click_question_sharp: opening question is a reading-pain question "
                "('名著难读'), not a debatable fate question (need '为什么/到底/凭什么')"
            )
    # 至少一个问句包含"为什么/到底/凭什么/该不该/值不值得/图什么"式命运质问词。
    fate_markers = ["为什么", "到底", "凭什么", "该不该", "值不值得", "图什么", "怎么", "为何", "还是", "配不配", "算不算", "是不是", "值不值"]
    if not any(any(m in q for m in fate_markers) for q in questions):
        raise ContractError(
            "click_question_sharp: opening '?' present but not a fate question "
            "(need 为什么/到底/凭什么/该不该/值不值得 等命运质问词)"
        )
    # v1.3：命运问句位置必须前移（≤12%）——金标准范本第 1-2 段即出，埋太深会
    # 浪费开头 45 秒的钩子价值。对标漂亮朋友"长得好看重要吗"第 1 句即出。
    fate_questions = [q for q in questions if any(m in q for m in fate_markers)]
    first_fate_pos = len(text)
    for q in fate_questions:
        pos = text.find(q)
        if pos != -1 and pos < first_fate_pos:
            first_fate_pos = pos
    pos_frac = first_fate_pos / len(text)
    max_pos = float(payload.get("click_question_max_position", 0.12))
    if first_fate_pos == len(text) or pos_frac > max_pos:
        raise ContractError(
            f"click_question_sharp: fate question appears at {pos_frac:.1%} "
            f"(must be ≤ {max_pos:.0%}) — golden-standard openers ask the fate question "
            "in the first 1-2 paragraphs; burying it wastes the hook"
        )


def validate_midpoint_emergent(payload: dict[str, Any]) -> None:
    """G2 中点必须从结构涌现（范本级）：40%-55% 处必须有"新问句"且 ≠ 开头问句。

    旧 skill 稿缺陷：中点为过 55% 契约而挪位，是"为卡区间挪的"，不是从叙事结构
    长出来的真升级。范本中点（窄门"障碍全清空了她还在躲什么"）必须是结构涌现：
    外部障碍消失 / 已成功却更不满足 / 人物已死冲突才开始。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    mid = float(payload.get("midpoint_position", 0.50))
    start = int(len(text) * (mid - 0.05))
    end = int(len(text) * (mid + 0.05))
    window = text[start:end]
    opening_q = (payload.get("opening_question") or "").strip()
    questions = _window_questions(window)
    if not questions:
        # v1.3：中点不要求必有问句——金标准范本（小王子/包法利夫人）中点靠
        # "叙事事件驱动"升级（玫瑰园独特性崩塌/私奔失败），无显式问句。
        # 此时要求窗口内必须有"升级事件标记"（结构转折/焦点转移/已成功却不满足）。
        event_markers = ["却", "明明", "已经", "反而", "反倒", "原来", "竟然", "可",
                         "到头来", "成了", "变了", "没想到", "才发现", "突然", "从这一刻起"]
        if any(m in window for m in event_markers):
            return  # 事件驱动的升级中点，无问句也可接受
        raise ContractError(
            f"midpoint_emergent: midpoint window {int((mid-0.05)*100)}%–"
            f"{int((mid+0.05)*100)}% has neither an escalation '?' nor an escalation event "
            "(却/反而/原来/突然/从这一刻起) — midpoint must re-ask or structurally escalate"
        )
    # 中点问句不得照抄开头问句（必须真升级）。
    for q in questions:
        core = q.replace("？", "").replace("?", "")
        if opening_q and any(len(tok) >= 4 and tok in core for tok in _split_sentences_with_marks(opening_q)):
            raise ContractError(
                "midpoint_emergent: midpoint question echoes the opening question — "
                "midpoint must ESCALATE (障碍消失/已成功却更不满足/已死冲突才开始)"
            )
    # 中点问句至少带一个升级标记，证明它不是"继续叙事"而是"问题升级"。
    if not any(any(m in s for m in MIDPOINT_UPGRADE_MARKERS) for s in questions):
        raise ContractError(
            "midpoint_emergent: midpoint '?' lacks an escalation marker "
            "(明明/已经/却/赢了/死后/障碍清空/真正) — reads as narration, not re-questioning"
        )


def validate_theory_anchored(payload: dict[str, Any]) -> None:
    """G3 理论必须锚定本书（范本级）：理论论点必须由 primary_metaphor 承载。

    旧 skill 稿缺陷：理论是通用励志概念（"输 vs 被打败"），"大海"只在引用原声时
    出现（"他说大海没有亏待我"），不是论点本身锚定大海。范本《六便士》是
    "人格面具=六便士，自性=月亮"——象征词承载论点。禁止纯通用概念硬贴。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    metaphor = (payload.get("primary_metaphor") or "").strip()
    if not metaphor:
        return  # 未提供主象征词时跳过（保持宽松）
    thesis_pos = float(payload.get("thesis_position",
                                   payload.get("metaphor_reveal_position", 0.70)))
    # 理论段取"thesis_pos 之后的全部"，且至少覆盖末尾 500 字（鲁棒于短文本）。
    theory_zone = text[int(len(text) * thesis_pos):]
    if len(theory_zone) < 500:
        theory_zone = text[-500:]
    if metaphor not in theory_zone:
        # 降级匹配：象征词同族词（用于 metaphor 是复合词情况）。
        anchor_hints = [h for h in THEORY_ANCHOR_HINT_MARKERS if h in theory_zone]
        if not anchor_hints:
            raise ContractError(
                f"theory_anchored: theory zone (after {int(thesis_pos*100)}%) never mentions "
                f"the book's own symbol '{metaphor}' — theory must anchor to THIS book's "
                "imagery (like 月亮/六便士), not a universal concept"
            )
        return
    # 象征词出现，但需判断它是否承载论点：
    # 排除"引用原声/撇清式"——象征词若只出现在"他说…""说的是…"里，是引用，不是论点锚。
    # 找象征词 ±15 字上下文，看是否出现"解释性绑定词"（像/正如/所谓/你的/心里的/等于/就是）。
    binding = ["像", "正如", "等于", "就是", "如同", "意味着", "代表着", "是", "也像", "说白了", "所谓", "你的", "心里的"]
    quote_patterns = ["他说", "说的是", "说的不是", "海明威", "那句话", "原文"]
    idx = theory_zone.find(metaphor)
    while idx != -1:
        ctx = theory_zone[max(0, idx - 15): idx + len(metaphor) + 15]
        if any(q in ctx for q in quote_patterns):
            idx = theory_zone.find(metaphor, idx + 1)
            continue
        if any(b in ctx for b in binding):
            return  # 象征词被解释性地用于论证 → 锚定成立
        idx = theory_zone.find(metaphor, idx + 1)
    raise ContractError(
        f"theory_anchored: theory zone mentions symbol '{metaphor}' only as quoted original "
        "voice (他说/说的是), not as the carrier of the thesis — theory must USE this book's "
        "symbol as its argument (like 人格面具=六便士, 自性=月亮)"
    )


# v1.3 贴概念检测：理论若用"X是…Y是…"整齐并列定义（类似呼啸山庄"本我/超我过度
# 整齐"被金标准批评的写法），即"贴概念"而非"场景承载"，应降级提示。
def validate_theory_not_concept_sticking(payload: dict[str, Any]) -> None:
    """G3b 理论不得"贴概念"：禁止用整齐的并列定义承载理论。

    金标准拆解发现：呼啸山庄"本我/超我"过度整齐是范本要避的写法；窄门用
    "她怕的不是幸福，是身上流着母亲的血"（场景+反转）承载理论。若理论段出现
    "X是…Y是…"式整齐定义（尤其两个抽象概念对举），判为贴概念 → 打回。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    thesis_pos = float(payload.get("thesis_position",
                                   payload.get("metaphor_reveal_position", 0.70)))
    theory_zone = text[int(len(text) * thesis_pos):]
    if len(theory_zone) < 300:
        theory_zone = text[-300:]
    # 整齐对举：两个"…是…，…是…"或"…是…；…是…"并列，且主语是抽象词（不是具体场景）。
    # 例："太阳是规矩，是逼你盖章的光；海是他的本相" —— 两个抽象对举 → 贴概念。
    # 注意：先剔除"不是X，是Y"反转句——那是金标准写法（窄门"她怕的不是幸福，是身上
    # 流着母亲的血"），不是贴概念。
    zone_no_reverse = re.sub(r"不是[^。！？；，]*[，；]是[^。！？；]*", "", theory_zone)
    pairs = re.findall(r"[^。！？；;\n]*是[^。！？；;\n]*[；，][^。！？；;\n]*是[^。！？；;\n]*", zone_no_reverse)
    if not pairs:
        return
    # 判断是否"抽象对举"：主语多为 2-4 字的抽象名词（太阳/海/爱/命运/规则/本相）。
    abstract_nouns = ["太阳", "海", "爱", "命运", "规则", "本相", "生活", "欲望", "自由",
                      "婚姻", "社会", "世界", "人性", "真相", "孤独", "幸福"]
    for pair in pairs:
        if sum(1 for noun in abstract_nouns if noun in pair) >= 2:
            raise ContractError(
                f"theory_not_concept_sticking: theory uses neat parallel definition "
                f"'{pair[:30]}…' (X是…Y是…) — this is concept-sticking (呼啸山庄本我/超我 "
                "overly-neat pattern). Anchor theory via a concrete scene (窄门: 她怕的不是 "
                "幸福, 是身上流着母亲的血), not abstract parallel definitions"
            )


def validate_ending_takeaway(payload: dict[str, Any]) -> None:
    """G4 结尾必须给可带走框架（范本级）：结尾必须直接"赠与"观众一个行动框架。

    旧 skill 稿缺陷：结尾只有情绪反问（"你有没有过…你的那片海还在吗"）——有"你"，
    但只是反问，没直接给观众"愿你…/你可以…"的行动指令。范本《六便士》结尾是
    "愿你手中有六便士，去应付生活的苟且；心中有月亮，去抵御岁月的漫长"——直接赠与。
    禁止通用鸡汤结尾（"勇敢做自己/热爱生活"）。
    """
    text = payload.get("script_text", "")
    if not text:
        return
    # 结尾取"最后 300 字"而非百分比切分——结尾在语义上是末尾段落，与总长无关，
    # 避免短文本被窗口切掉。
    tail = text[-300:]
    if "你" not in tail:
        raise ContractError(
            "ending_takeaway: ending lacks second-person '你' — no turn-to-audience, "
            "no takeaway framework"
        )
    # 必须有一个"直接赠与/祈使"标记（愿你/请你/你可以/希望你/请务必），
    # 而不仅是"你有没有…"式反问。这是"给可带走框架"与"停在情绪反问"的分界。
    imperative = [
        "愿你", "请你", "你可以", "你可以在", "希望你", "请务必", "留给",
        "记得", "别忘了", "给自己", "不必", "不用", "不要忘了", "你也能",
    ]
    if not any(m in tail for m in imperative):
        raise ContractError(
            "ending_takeaway: ending uses '你有没有…' rhetorical questions but gives no "
            "direct imperative takeaway (愿你/请你/你可以/希望你/不必…) — must hand the "
            "audience an actionable frame, not just ask an open-ended question"
        )
    # 行动双线/给框架：结尾须含"…可以…/白天…夜晚…/不必…可以…/手中…心中…"式结构，
    # 或"多个祈使动作并列"（愿你…；也请你…；允许…）——后者是等价的"可带走框架"。
    dual_action = any(m in tail for m in [
        "可以", "不必", "白天", "夜晚", "夜里", "留出", "手中", "心中",
        "一边", "另一", "不一定", "不用", "也能", "也能去",
    ])
    # 多个祈使动词并列（"愿你…也请你…允许…"）也构成可带走框架。
    imperative_verbs = ["愿你", "请你", "也希望你", "允许", "记得", "留一点", "给自己"]
    matched_imp = [m for m in imperative_verbs if m in tail]
    if not dual_action and len(matched_imp) < 2:
        raise ContractError(
            "ending_takeaway: ending lacks an actionable dual-frame "
            "(白天…夜晚…/手中有X心中有Y/愿你…也请你…) — reads as open-ended emotion only"
        )
    # 通用鸡汤检测：出现"做自己/热爱生活/成为自己"式万能收尾且无本书意象。
    generic = any(m in tail for m in ["勇敢做自己", "热爱生活", "成为你自己", "做真正的自己"])
    if generic and not any(m in tail for m in THEORY_ANCHOR_HINT_MARKERS):
        raise ContractError(
            "ending_takeaway: ending is a universal motivational cliché "
            "('做自己/热爱生活') with no THIS-book imagery — forbidden"
        )
    # v1.3 自我照见钩子：金标准范本结尾全部有"照见观众"的戳心话
    # （漂亮朋友"你也有觉得他帅的瞬间吗"、窄门"你有没有为了体面压下去过"），
    # 而不仅是"愿你…"赠与。结尾尾部（最后 400 字）必须含"你有没有/你是不是/你是否"式照见问。
    # 窗口 400 字：范本"照见→赠与→落版"结构中照见常在赠与前，距结尾可能 >200 字。
    reflection = ["你有没有", "你是不是", "你是否", "你是不是也", "你也曾", "你是否也有",
                  "你也遇见过", "你有没有过", "你是否也"]
    self_tail = tail[-400:]
    if not any(r in self_tail for r in reflection):
        raise ContractError(
            "ending_takeaway: ending gives an imperative takeaway but no self-reflection hook "
            "('你有没有…') — golden-standard endings mirror the audience (漂亮朋友: 你也有觉得"
            "他帅的瞬间吗). Add a '你有没有…' turn before the gift"
        )


TEXT_GATE_VALIDATORS = {
    "narrator-essay.opening": validate_narrator_essay_opening,
    "narrator-essay.metaphor-budget": validate_metaphor_budget,
    "narrator-essay.story-first": validate_story_first_ratio,
    "narrator-essay.self-reveal": validate_narrator_self_reveal,
    "narrator-essay.chat-relaxation": validate_chat_relaxation,
    "narrator-essay.evidence-before-thesis": validate_evidence_before_thesis,
    "narrator-essay.brand-opener": validate_series_brand_opener,
    # 范本级门禁（v1.2）：管"像金标准"，不管"及格"。
    "narrator-essay.click-question-sharp": validate_click_question_sharp,
    "narrator-essay.midpoint-emergent": validate_midpoint_emergent,
    "narrator-essay.theory-anchored": validate_theory_anchored,
    "narrator-essay.theory-not-concept-sticking": validate_theory_not_concept_sticking,
    "narrator-essay.ending-takeaway": validate_ending_takeaway,
}


VALIDATORS = {
    "creative-decision.v1": validate_creative_decision,
    "fate-anchors.v1": validate_fate_anchors,
    "event-cards.v1": validate_event_cards,
    "script-beats.v1": validate_script_beats,
    "visual-bible.v1": validate_visual_bible,
    "voice-direction.narrator.v1": validate_voice_direction,
    "content-quality-report.v1": validate_content_quality_report,
    "script.narrator-essay.v1": validate_narrator_essay_script,
    # discovery-narrative text gates (v1.1)
    "narrator-essay.opening": validate_narrator_essay_opening,
    "narrator-essay.metaphor-budget": validate_metaphor_budget,
    "narrator-essay.story-first": validate_story_first_ratio,
    "narrator-essay.self-reveal": validate_narrator_self_reveal,
    "narrator-essay.chat-relaxation": validate_chat_relaxation,
    "narrator-essay.evidence-before-thesis": validate_evidence_before_thesis,
    "narrator-essay.brand-opener": validate_series_brand_opener,
    # 范本级门禁（v1.2）
    "narrator-essay.click-question-sharp": validate_click_question_sharp,
    "narrator-essay.midpoint-emergent": validate_midpoint_emergent,
    "narrator-essay.theory-anchored": validate_theory_anchored,
    "narrator-essay.theory-not-concept-sticking": validate_theory_not_concept_sticking,
    "narrator-essay.ending-takeaway": validate_ending_takeaway,
}
