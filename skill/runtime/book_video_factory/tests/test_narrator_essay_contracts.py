"""Phase 2 测试：narrator-essay 正式合同校验器。

用合法实例测"通过"，用变异实例测"失败"。骨架 example.json 仅用于
schema_validation 的结构往返测试，不用于本文件的深度业务校验。

覆盖：creative-decision(三路线+10评分)/fate-anchors(8-12锚点/20-40候选/每锚≥2功能)/
event-cards(七要素)/script.narrator-essay(85-95%主播/5-15%对白/中点/翻案/一主概念/
审计+发布两版)/script-beats(外键+narrative_function)/visual-bible(禁混用)/
voice-direction(provider)/content-quality(exact/heuristic/human)。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.narrator_essay_contracts import (  # noqa: E402
    ContractError,
    validate_creative_decision,
    validate_fate_anchors,
    validate_event_cards,
    validate_narrator_essay_script,
    validate_script_beats,
    validate_visual_bible,
    validate_voice_direction,
    validate_content_quality_report,
    validate_narrator_essay_opening,
    validate_metaphor_budget,
    validate_story_first_ratio,
    validate_narrator_self_reveal,
    validate_chat_relaxation,
    validate_evidence_before_thesis,
    validate_series_brand_opener,
    validate_click_question_sharp,
    validate_midpoint_emergent,
    validate_theory_anchored,
    validate_ending_takeaway,
)


def _creative() -> dict:
    scores = {k: 4 for k in (
        "audience_resonance", "textual_evidence", "emotional_range",
        "midpoint_power", "reinterpretation_power", "modern_mapping",
        "visual_potential", "originality", "character_complexity",
        "ending_specificity",
    )}
    return {
        "schema_version": "creative-decision.v1",
        "book_id": "book-test",
        "candidate_routes": [
            {"route_id": f"route-{i}", "click_question": f"问题{i}", "deep_thesis": f"命题{i}",
             "narrative_engine": "desire_escalation", "midpoint_question": "中点",
             "reinterpretation_evidence_ids": ["F001"], "narrator_persona": "先理解后审判",
             "main_concept": "包法利主义", "target_emotional_curve": [], "visual_potential": 4,
             "scores": dict(scores)} for i in range(1, 4)
        ],
        "selected_route_id": "route-1",
        "rejected_route_reasons": [],
        "recommended_duration_minutes": 18,
    }


def _anchors() -> dict:
    return {
        "schema_version": "fate-anchors.v1", "book_id": "book-test",
        "candidate_event_count": 25,
        "anchors": [
            {"anchor_id": f"FA{i:03d}", "chapter_refs": ["ch1"], "event_summary": "事件",
             "character_desire": "欲望", "choice": "选择", "result": "结果", "cost": "代价",
             "functions": ["changes_fate", "reveals_character"], "treatment": "expand",
             "source_ids": ["F001"]} for i in range(10)
        ],
    }


def _cards() -> dict:
    return {
        "schema_version": "event-cards.v1",
        "cards": [{
            "event_card_id": "EC001", "anchor_id": "FA001", "scene_location": "客厅",
            "sensory_objects": ["汇票"], "character_actions": ["签字"], "key_dialogue": "还钱",
            "narrator_reaction": "代价显现", "hammer_line": "债到期了", "next_question": "谁能救她",
            "emotion": "绝望", "visual_function": "death", "source_ids": ["F001"],
        }],
    }


def _beats() -> dict:
    return {
        "schema_version": "script-beats.v1",
        "beats": [{
            "beat_id": "B001", "section_id": "hook", "event_card_id": "EC001",
            "source_text": "原文", "release_text": "口播", "source_ids": ["F001"],
            "narrative_function": "hook", "character_ids": ["C1"], "emotion": "curiosity",
            "intensity": 0.6, "target_cpm": 300, "pause_before": 0.0, "pause_after": 0.3,
            "emphasis_words": ["债"], "visual_function": "contrast", "visual_subject": "汇票",
            "hero_shot": False, "reuse_allowed": False, "bgm_function": "opening_pulse",
            "sfx_function": None, "next_question": "谁", "caption_ids": [], "shot_ids": [],
            "voice_chunk_id": "VC001",
        }],
    }


def _bible() -> dict:
    return {
        "schema_version": "visual-bible.v1", "book_id": "book-test", "core_metaphor": "两座庄园",
        "primary_palette": ["紫灰"], "secondary_palette": ["冷棕"], "forbidden_colors": [],
        "materials": ["湿石"], "period": "19世纪", "geography": "约克郡",
        "lighting_rules": ["低照度"], "composition_rules": ["对称"],
        "repeated_objects": ["窗"], "character_anchors": [], "age_transition_rules": [],
        "motion_grammar": ["推近"], "theory_card_grammar": {}, "forbidden_styles": ["明亮印象派"],
        "hero_shot_count": 12,
    }


def _voice() -> dict:
    return {
        "schema_version": "voice-direction.narrator.v1", "provider": "edge-tts",
        "brand_voice_id": "default-brand-voice", "narrator_persona": "gothic_storyteller",
        "overall_curve": [], "chunks": [{
            "voice_chunk_id": "VC001", "beat_ids": ["B001"], "emotion": "curiosity",
            "target_cpm": 300, "energy": 0.7, "warmth": 0.3, "restraint": 0.6, "irony": 0.0,
            "intimacy": 0.5, "emphasis_words": [], "pause_marks": [], "pronunciation_overrides": {},
        }],
    }


def _quality() -> dict:
    return {
        "schema_version": "content-quality-report.v1", "overall_score": 68, "threshold": 66,
        "checks": [{"check_id": "hook", "check_type": "heuristic_check", "score": 4,
                    "status": "pass", "evidence": [], "notes": ""}],
        "human_review_required": True, "blocking_issues": [],
    }


def _script() -> dict:
    sections = [
        {"section_id": "S01", "narrative_function": "hook", "text": "如果连续失败八十四天，你还会不会再出海？"},
        {"section_id": "S02", "narrative_function": "world_setup", "text": "老人整理好绳索，再次走向海面。"},
        {"section_id": "S03", "narrative_function": "midpoint_requestion", "text": "大鱼终于咬钩，可真正的问题才刚刚出现。"},
        {"section_id": "S04", "narrative_function": "reinterpretation", "text": "鲨鱼夺走鱼肉以后，胜利的意义被重新改写。"},
        {"section_id": "S05", "narrative_function": "ending_image", "text": "老人睡着了，又梦见海滩上的狮子。"},
    ]
    release = {"sections": [dict(item) for item in sections], "text": "".join(item["text"] for item in sections)}
    performance = {"sections": [{**item, "direction": {"energy": 0.6}} for item in sections], "text": release["text"]}
    audit = {"sections": [{**item, "source_ids": ["F001"]} for item in sections], "text": release["text"]}
    return {
        "schema_version": "script.narrator-essay.v1", "book_id": "book-test", "release_id": "v1-r1",
        "narrator_ratio": 0.90, "dialogue_ratio": 0.10, "duration_target_seconds": 990,
        "main_concept_count": 1, "has_midpoint_requestion": True,
        "has_evidence_reinterpretation": True, "reinterpretation_source_ids": ["F001", "Q002"],
        "midpoint_section_id": "S03",
        "performance_version": performance,
        "audit_version": audit,
        "release_version": release,
        "script_text": release["text"],
    }



class CreativeDecisionTests(unittest.TestCase):
    def test_valid_passes(self):  validate_creative_decision(_creative())

    def test_requires_three_routes(self):
        d = _creative(); d["candidate_routes"] = d["candidate_routes"][:2]
        with self.assertRaisesRegex(ContractError, "candidate_routes|三条|3"): validate_creative_decision(d)

    def test_score_out_of_range(self):
        d = _creative(); d["candidate_routes"][0]["scores"]["audience_resonance"] = 6
        with self.assertRaisesRegex(ContractError, "score|评分|1.*5"): validate_creative_decision(d)

    def test_selected_not_among_candidates(self):
        d = _creative(); d["selected_route_id"] = "route-999"
        with self.assertRaisesRegex(ContractError, "selected_route"): validate_creative_decision(d)

    def test_click_equals_thesis(self):
        d = _creative()
        d["candidate_routes"][0]["click_question"] = "颜值"
        d["candidate_routes"][0]["deep_thesis"] = "颜值"
        with self.assertRaisesRegex(ContractError, "click_question|deep_thesis|点击|深层"): validate_creative_decision(d)


class FateAnchorsTests(unittest.TestCase):
    def test_valid_passes(self):  validate_fate_anchors(_anchors())

    def test_anchor_count_out_of_range(self):
        d = _anchors(); d["anchors"] = d["anchors"][:6]
        with self.assertRaisesRegex(ContractError, "anchor|锚点|8|12"): validate_fate_anchors(d)

    def test_candidate_count_out_of_range(self):
        d = _anchors(); d["candidate_event_count"] = 10
        with self.assertRaisesRegex(ContractError, "candidate|候选|20|40"): validate_fate_anchors(d)

    def test_anchor_fewer_than_two_functions(self):
        d = _anchors(); d["anchors"][0]["functions"] = ["changes_fate"]
        with self.assertRaisesRegex(ContractError, "function|功能|2|两"): validate_fate_anchors(d)


class EventCardsTests(unittest.TestCase):
    def test_valid_passes(self):  validate_event_cards(_cards())

    def test_missing_hammer_line(self):
        d = _cards(); d["cards"][0]["hammer_line"] = ""
        with self.assertRaisesRegex(ContractError, "hammer|落锤"): validate_event_cards(d)


class ScriptBeatsTests(unittest.TestCase):
    def test_valid_passes(self):  validate_script_beats(_beats())

    def test_empty_voice_chunk_id(self):
        d = _beats(); d["beats"][0]["voice_chunk_id"] = ""
        with self.assertRaisesRegex(ContractError, "voice_chunk_id"): validate_script_beats(d)

    def test_bad_narrative_function(self):
        d = _beats(); d["beats"][0]["narrative_function"] = "random"
        with self.assertRaisesRegex(ContractError, "narrative_function|function"): validate_script_beats(d)


class VisualBibleTests(unittest.TestCase):
    def test_valid_passes(self):  validate_visual_bible(_bible())

    def test_empty_forbidden_styles(self):
        d = _bible(); d["forbidden_styles"] = []
        with self.assertRaisesRegex(ContractError, "forbidden_style|禁用|混用"): validate_visual_bible(d)


class VoiceDirectionTests(unittest.TestCase):
    def test_valid_passes(self):  validate_voice_direction(_voice())

    def test_unknown_provider(self):
        d = _voice(); d["provider"] = "unknown"
        with self.assertRaisesRegex(ContractError, "provider|edge-tts|external-recording"): validate_voice_direction(d)

    def test_removed_voxcpm_provider_is_rejected(self):
        d = _voice(); d["provider"] = "voxcpm2"
        with self.assertRaisesRegex(ContractError, "provider|edge-tts|external-recording"):
            validate_voice_direction(d)


class ContentQualityTests(unittest.TestCase):
    def test_valid_passes(self):  validate_content_quality_report(_quality())

    def test_bad_check_type(self):
        d = _quality(); d["checks"][0]["check_type"] = "magic"
        with self.assertRaisesRegex(ContractError, "check_type|exact|heuristic|human"): validate_content_quality_report(d)


class NarratorEssayScriptTests(unittest.TestCase):
    def test_valid_passes(self):  validate_narrator_essay_script(_script())

    def test_narrator_ratio_too_low(self):
        s = _script(); s["narrator_ratio"] = 0.70
        with self.assertRaisesRegex(ContractError, "narrator|85|95|主播"): validate_narrator_essay_script(s)

    def test_dialogue_ratio_too_high(self):
        s = _script(); s["dialogue_ratio"] = 0.50
        with self.assertRaisesRegex(ContractError, "dialogue|对白|5|15"): validate_narrator_essay_script(s)

    def test_missing_midpoint(self):
        s = _script(); s["has_midpoint_requestion"] = False
        with self.assertRaisesRegex(ContractError, "midpoint|中点"): validate_narrator_essay_script(s)

    def test_reinterpretation_no_source(self):
        s = _script(); s["reinterpretation_source_ids"] = []
        with self.assertRaisesRegex(ContractError, "reinterpretation|翻案|source|证据"): validate_narrator_essay_script(s)

    def test_too_many_concepts(self):
        s = _script(); s["main_concept_count"] = 3
        with self.assertRaisesRegex(ContractError, "main_concept|主概念|1"): validate_narrator_essay_script(s)

    def test_missing_release_version(self):
        s = _script(); del s["release_version"]
        with self.assertRaisesRegex(ContractError, "release_version|发布稿"): validate_narrator_essay_script(s)


class DiscoveryNarrativeGateTests(unittest.TestCase):
    def test_opening_leak_fails(self):
        p = {"script_text": "今天我不打算审判她。我想清算那套账本。" * 50}
        with self.assertRaisesRegex(ContractError, "deep_thesis_leaked_in_opening"):
            validate_narrator_essay_opening(p)

    def test_opening_clean_passes(self):
        p = {"script_text": "如果你有一段不光彩的过去，要不要告诉最在乎的人？" * 50}
        validate_narrator_essay_opening(p)  # no raise

    def test_metaphor_reveal_too_early_fails(self):
        p = {"script_text": ("账本" + "故事" * 50) * 2, "primary_metaphor": "账本",
             "metaphor_carries_reframing": True, "midpoint_position": 0.50}
        with self.assertRaisesRegex(ContractError, "metaphor_budget"):
            validate_metaphor_budget(p)

    def test_metaphor_reveal_late_passes(self):
        body = "故事" * 130 + "账本" + "故事" * 20
        p = {"script_text": body, "primary_metaphor": "账本",
             "metaphor_carries_reframing": True, "midpoint_position": 0.50}
        validate_metaphor_budget(p)  # reveal ~86% > 65%

    def test_metaphor_forbidden_first_half_fails(self):
        body = "负债" + "故事" * 130 + "账本"
        p = {"script_text": body, "primary_metaphor": "账本",
             "metaphor_carries_reframing": True, "midpoint_position": 0.50}
        with self.assertRaisesRegex(ContractError, "metaphor_budget"):
            validate_metaphor_budget(p)

    def test_story_first_analysis_too_high_fails(self):
        body = ("这意味着她在结构中。" * 30) + ("场景长句描述。" * 5)
        p = {"script_text": body}
        with self.assertRaisesRegex(ContractError, "story_first_ratio"):
            validate_story_first_ratio(p)

    def test_story_first_passes(self):
        body = ("场景里她牵着马走过村庄。" * 40) + ("这意味着结构。" * 2)
        p = {"script_text": body}
        validate_story_first_ratio(p)

    def test_self_reveal_too_few_fails(self):
        p = {"script_text": "请注意这是一个关于道德的故事。" * 30}
        with self.assertRaisesRegex(ContractError, "narrator_self_reveal"):
            validate_narrator_self_reveal(p)

    def test_self_reveal_qualified_passes(self):
        p = {"script_text": ("读到这里，我其实替她憋屈。" + "故事在继续发展着" * 20) * 4}
        validate_narrator_self_reveal(p)  # 4 qualified

    def test_chat_relaxation_dense_fails(self):
        p = {"script_text": ("这意味着结构问题。" * 10)}
        with self.assertRaisesRegex(ContractError, "chat_relaxation"):
            validate_chat_relaxation(p)

    def test_chat_relaxation_natural_passes(self):
        p = {"script_text": ("可问题来了。" + "故事接着发生" * 5 + "但事情还没完。" + "她继续走" * 5) * 3}
        validate_chat_relaxation(p)

    def test_evidence_before_thesis_fails(self):
        p = {"script_text": ("故事前半" * 40) + "账本" + ("证据齐全" * 5),
             "evidence_tokens": ["假爵位", "死马", "Sorrow", "租约"], "thesis_position": 0.85}
        with self.assertRaisesRegex(ContractError, "evidence_before_thesis"):
            validate_evidence_before_thesis(p)

    def test_evidence_before_thesis_passes(self):
        p = {"script_text": ("假爵位死马Sorrow租约" * 30) + "账本",
             "evidence_tokens": ["假爵位", "死马", "Sorrow", "租约"], "thesis_position": 0.85}
        validate_evidence_before_thesis(p)

    def test_brand_opener_missing_fails(self):
        p = {"script_text": "这是一个关于女孩的故事。" * 20}
        with self.assertRaisesRegex(ContractError, "series_brand_opener"):
            validate_series_brand_opener(p)

    def test_brand_opener_present_passes(self):
        p = {"script_text": "有些名著你早知道好却一直没翻。它值得读，可普通人真读不进去。这个系列负责讲懂。" * 5}
        validate_series_brand_opener(p)


class GoldStandardLevelGateTests(unittest.TestCase):
    """范本级门禁：管"像金标准"，不管"及格"。实证测试定位四缺陷——
    点击问题不锋利 / 中点不涌现 / 理论不锚定本书 / 结尾无行动框架。"""

    # ---- G1 点击问题必须锋利：开头 15% 必须有一个"当代可辩论命运问句" ----

    def test_click_question_missing_question_fails(self):
        p = {"script_text": "这本书是海明威写的，讲一个老人出海打鱼的故事。" * 30,
             "click_question": "老人与海讲什么"}
        with self.assertRaisesRegex(ContractError, "click_question_sharp"):
            validate_click_question_sharp(p)

    def test_click_question_reading_pain_only_fails(self):
        p = {"script_text": "名著太难读了，我替你啃下来，这本讲一个老人。" * 40,
             "click_question": "名著为什么难读"}
        with self.assertRaisesRegex(ContractError, "click_question_sharp"):
            validate_click_question_sharp(p)

    def test_click_question_fate_question_passes(self):
        p = {"script_text": "一个人拼了一辈子，到底图什么？这本讲一个老人出海。" * 40,
             "click_question": "拼一辈子到底图什么"}
        validate_click_question_sharp(p)

    # ---- G2 中点必须从结构涌现：40%-55% 出现新问句且 ≠ 开头问句 ----

    def test_midpoint_no_question_fails(self):
        p = {"script_text": ("故事继续推进" * 60) + ("这里讲鲨鱼来了" * 60) + "结束",
             "opening_question": "拼一辈子图什么", "midpoint_position": 0.50}
        with self.assertRaisesRegex(ContractError, "midpoint_emergent"):
            validate_midpoint_emergent(p)

    def test_midpoint_echoes_opening_fails(self):
        p = {"script_text": ("故事前" * 60) + "可拼了一辈子到底图什么？" + ("故事后" * 60),
             "opening_question": "拼了一辈子到底图什么", "midpoint_position": 0.50}
        with self.assertRaisesRegex(ContractError, "midpoint_emergent"):
            validate_midpoint_emergent(p)

    def test_midpoint_emergent_passes(self):
        p = {"script_text": ("故事前" * 60) + "明明赢了，可赢到手不等于结束，他还惦记什么？" + ("故事后" * 60),
             "opening_question": "拼一辈子图什么", "midpoint_position": 0.50}
        validate_midpoint_emergent(p)

    # ---- G3 理论必须锚定本书：理论段出现 primary_metaphor ----

    def test_theory_not_anchored_fails(self):
        p = {"script_text": ("故事" * 80) + "输和被打败是两件事，还没走就先认了才是失败。" + ("故事" * 30),
             "primary_metaphor": "大海"}
        with self.assertRaisesRegex(ContractError, "theory_anchored"):
            validate_theory_anchored(p)

    def test_theory_anchored_passes(self):
        # "大海" 必须出现在 70% 之后，且被"绑定词"（就像/所谓）解释性地用于论点，
        # 而不只是引用原声（他说）。
        p = {"script_text": ("故事" * 100) + "输和被打败是两回事。大海就像你的自性，你被它选中，就必须出海。他出过海。" + ("故事" * 30),
             "primary_metaphor": "大海"}
        validate_theory_anchored(p)

    # ---- G4 结尾必须给可带走框架：结尾含第二人称 + 行动双线 + 本书意象 ----

    def test_ending_no_takeaway_fails(self):
        p = {"script_text": ("故事" * 150) + "所以，勇敢做自己吧，愿你热爱生活。"}
        with self.assertRaisesRegex(ContractError, "ending_takeaway"):
            validate_ending_takeaway(p)

    def test_ending_takeaway_passes(self):
        # 金标准结尾 = 照见观众（你有没有…）+ 直接赠与（愿你…）+ 本书意象。
        p = {"script_text": ("故事" * 150) + "你有没有过这样的时候——明明不累，却觉得自己快撑不住了？如果有，你心里也住着那个追月的人。愿你手中有六便士，去应付生活的苟且；愿你心中有月亮，去抵御岁月的漫长。"}
        validate_ending_takeaway(p)


if __name__ == "__main__":
    unittest.main()
