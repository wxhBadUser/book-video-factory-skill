"""Deterministic unit tests for the ``semantic_alignment`` package.

The package must be pure and replayable: the same inputs always yield the same
proposition, the same score, and the same hash. Nothing in here may touch the
network, the clock, or the filesystem.
"""

from __future__ import annotations

import unittest

from book_video_factory.semantic_alignment import (
    EntityVisibility,
    SemanticContractError,
    VisualProposition,
    evaluate_semantic_bridge,
    is_boilerplate_rationale,
    normalize_rationale,
    validate_visual_proposition,
)
from book_video_factory.semantic_alignment.classifier import (
    PropositionClassifierError,
    classify_proposition,
)
from book_video_factory.semantic_alignment.scoring import score_alignment

CHARACTER_ANCHORS = {
    "C001": {"natural_language": "穿粗布短褂的中年农夫福贵", "aliases": ["福贵"]},
    "C002": {"natural_language": "穿蓝布褂的妇人家珍", "aliases": ["家珍"]},
    "C003": {"natural_language": "扎麻花辫的少女凤霞", "aliases": ["凤霞"]},
    "C004": {"natural_language": "赤脚奔跑的男孩有庆", "aliases": ["有庆"]},
}
SCENE_ANCHORS = {
    "S001": {"prompt_subject": "村口的土路", "aliases": ["土路", "村口"]},
    "S002": {"prompt_subject": "田埂与稻田", "aliases": ["田埂", "稻田"]},
    "S003": {"prompt_subject": "县城医院走廊", "aliases": ["医院"]},
}
OBJECT_ANCHORS = {
    "O001": {"prompt_subject": "一头瘦骨嶙峋的老牛", "aliases": ["老牛"]},
    "O002": {"prompt_subject": "一双磨破的布鞋", "aliases": ["布鞋"]},
}


class NormalizationTests(unittest.TestCase):
    def test_normalize_strips_punctuation_not_content(self) -> None:
        self.assertEqual(normalize_rationale("旁白讲，画面用；老牛。"), "旁白讲画面用老牛")
        self.assertEqual(normalize_rationale(""), "")
        self.assertEqual(normalize_rationale(None), "")  # type: ignore[arg-type]

    def test_boilerplate_detection_is_stable(self) -> None:
        positives = [
            "字幕与画面共享当前场景",
            "画面呼应旁白",
            "画面对应旁白的情绪",
            "与旁白语义一致",
            "情绪一致",
            "同上",
            "illustrate the exact current narration beat",
            "Matches the caption",
        ]
        negatives = [
            "旁白讲凤霞出嫁，画面用婚礼队伍的红布来承接这句台词",
            "旁白说老牛，画面改用墙上的空木轭作为替身，让观众看见牛已不在",
        ]
        for text in positives:
            with self.subTest(text=text):
                self.assertTrue(is_boilerplate_rationale(text))
        for text in negatives:
            with self.subTest(text=text):
                self.assertFalse(is_boilerplate_rationale(text))


class VisualPropositionModelTests(unittest.TestCase):
    def _proposition(self) -> VisualProposition:
        return VisualProposition(
            mode="Literal",
            subject="中年农夫",
            action="跪在土坟前",
            environment="村口土路，黄昏",
            mood="压抑",
            lighting="LAMP_WARM",
            palette="EARTH_DAY",
            rationale_text="旁白说福贵跪在爹的坟前，画面必须出现跪着的福贵与土坟",
            entity_visibility=(
                EntityVisibility("C001", True, "穿粗布短褂的中年农夫福贵"),
            ),
            source_terms=("福贵", "坟"),
        )

    def test_round_trip_is_lossless(self) -> None:
        original = self._proposition()
        restored = VisualProposition.from_mapping(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_content_hash_is_stable_and_sensitive(self) -> None:
        first = self._proposition()
        second = self._proposition()
        self.assertEqual(first.content_sha256(), second.content_sha256())
        mutated = VisualProposition.from_mapping({**first.to_dict(), "action": "站在土坟前"})
        self.assertNotEqual(first.content_sha256(), mutated.content_sha256())


class PropositionValidationTests(unittest.TestCase):
    def test_literal_requires_a_visible_source_side_entity(self) -> None:
        proposition = {
            "mode": "Literal",
            "subject": "雪地里的孩子",
            "action": "堆雪人",
            "environment": "冬天的院子",
            "mood": "冷",
            "lighting": "COLD_DAY",
            "palette": "SNOW",
            "rationale_text": "旁白讲凤霞出嫁，这里改画雪地里的孩子来表现时间流逝",
            "entity_visibility": [
                {"entity_id": "雪地", "must_be_visible": True, "natural_language": "厚雪覆盖的院子"}
            ],
        }
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(
                proposition, shot_id="shot-1", source_entities=["凤霞", "婚礼队伍"]
            )

    def test_literal_passes_when_the_named_referent_is_visible(self) -> None:
        proposition = {
            "mode": "Literal",
            "subject": "扎麻花辫的少女",
            "action": "坐在花轿边回头看",
            "environment": "村口土路，黄昏",
            "mood": "克制的喜悦",
            "lighting": "LAMP_WARM",
            "palette": "EARTH_DAY",
            "rationale_text": "旁白说凤霞出嫁，画面必须出现凤霞本人与婚礼队伍",
            "entity_visibility": [
                {"entity_id": "凤霞", "must_be_visible": True, "natural_language": "扎麻花辫的少女凤霞"},
                {"entity_id": "婚礼队伍", "must_be_visible": True, "natural_language": "抬花轿的送亲队伍"},
            ],
        }
        resolved = validate_visual_proposition(
            proposition, shot_id="shot-1", source_entities=["凤霞", "婚礼队伍"]
        )
        self.assertEqual(resolved.mode, "Literal")

    def test_literal_without_any_visible_entity_is_rejected(self) -> None:
        proposition = {
            "mode": "Literal",
            "subject": "村口",
            "action": "人群走过",
            "environment": "黄昏",
            "mood": "热闹",
            "lighting": "LAMP_WARM",
            "palette": "EARTH_DAY",
            "rationale_text": "旁白说凤霞出嫁，这里画一个热闹的村口",
            "entity_visibility": [
                {"entity_id": "凤霞", "must_be_visible": False, "natural_language": "少女凤霞"}
            ],
        }
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(
                proposition, shot_id="shot-1", source_entities=["凤霞"]
            )

    def test_symbolic_requires_both_sides_named_in_the_rationale(self) -> None:
        base = {
            "mode": "Symbolic",
            "subject": "墙上的空木轭",
            "action": "静静挂着",
            "environment": "土墙，暮色",
            "mood": "空",
            "lighting": "DUSK",
            "palette": "EARTH_DUSK",
            "surrogate_objects": ["空木轭"],
            "source_terms": ["老牛"],
        }
        good = dict(base, rationale_text="旁白说老牛已经走了，画面改用墙上的空木轭作为替身")
        self.assertEqual(
            validate_visual_proposition(
                good, shot_id="shot-2", known_symbol_registry=["空木轭"]
            ).mode,
            "Symbolic",
        )
        only_source = dict(base, rationale_text="旁白说老牛已经走了，这里留一个安静的空镜头")
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(only_source, shot_id="shot-2")
        only_surrogate = dict(base, rationale_text="画面用墙上的空木轭，构图安静，色调偏冷")
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(only_surrogate, shot_id="shot-2")

    def test_abstract_cannot_demand_visible_entities(self) -> None:
        proposition = {
            "mode": "Abstract",
            "subject": "atmosphere only",
            "action": "",
            "environment": "",
            "mood": "沉静",
            "lighting": "DUSK",
            "palette": "EARTH_DUSK",
            "rationale_text": "这句是作者背景的自述，没有可画的叙事指称，只给气氛",
            "entity_visibility": [
                {"entity_id": "余华", "must_be_visible": True, "natural_language": "作家余华"}
            ],
        }
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(proposition, shot_id="shot-3")

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(
                {"mode": "Vibes", "rationale_text": "旁白说老牛，画面用空木轭"},
                shot_id="shot-4",
            )

    def test_boilerplate_proposition_rationale_is_rejected_in_every_mode(self) -> None:
        for mode in ("Literal", "Symbolic", "Abstract"):
            with self.subTest(mode=mode), self.assertRaises(SemanticContractError):
                validate_visual_proposition(
                    {"mode": mode, "rationale_text": "字幕与画面共享当前场景"},
                    shot_id="shot-5",
                )


class ClassifierTests(unittest.TestCase):
    def _classify(self, caption: str, *, source_entities=("福贵",), description="") -> VisualProposition:
        return classify_proposition(
            shot_id="shot-x",
            caption_texts=[caption],
            description=description or caption,
            seed_rationale="字幕与画面共享当前场景",
            source_entities=list(source_entities),
            character_anchors=CHARACTER_ANCHORS,
            scene_anchors=SCENE_ANCHORS,
            object_anchors=OBJECT_ANCHORS,
        )

    def test_named_character_in_caption_yields_literal(self) -> None:
        result = self._classify("有庆脱下鞋就往医院跑", source_entities=("有庆", "医院"))
        self.assertEqual(result.mode, "Literal")
        self.assertTrue(any(item.must_be_visible for item in result.entity_visibility))
        self.assertIn("有庆", [item.entity_id for item in result.entity_visibility])

    def test_symbolic_trope_yields_symbolic(self) -> None:
        result = self._classify("月光照在路上，像是撒满了盐", source_entities=("福贵",))
        self.assertEqual(result.mode, "Symbolic")
        self.assertTrue(result.surrogate_objects)

    def test_question_yields_abstract(self) -> None:
        result = self._classify("人为什么还要活着？", source_entities=("福贵",))
        self.assertEqual(result.mode, "Abstract")
        self.assertFalse([item for item in result.entity_visibility if item.must_be_visible])

    def test_author_backstory_never_yields_literal(self) -> None:
        result = self._classify("余华说他听了一首美国民歌", source_entities=("福贵",))
        self.assertEqual(result.mode, "Abstract")

    def test_meta_narrator_line_yields_abstract(self) -> None:
        result = self._classify("我们一起读完五十二本书", source_entities=("福贵",))
        self.assertEqual(result.mode, "Abstract")

    def test_classifier_output_is_deterministic(self) -> None:
        first = self._classify("凤霞出嫁那天队伍走过村口", source_entities=("凤霞", "婚礼队伍"))
        second = self._classify("凤霞出嫁那天队伍走过村口", source_entities=("凤霞", "婚礼队伍"))
        self.assertEqual(first.content_sha256(), second.content_sha256())

    def test_generated_rationale_is_never_boilerplate_and_never_the_caption(self) -> None:
        caption = "凤霞出嫁那天队伍走过村口"
        result = self._classify(caption, source_entities=("凤霞", "婚礼队伍"))
        self.assertFalse(is_boilerplate_rationale(result.rationale_text))
        self.assertNotEqual(result.rationale_text.strip(), caption)

    def test_classifier_result_passes_its_own_validator(self) -> None:
        cases = [
            ("有庆脱下鞋就往医院跑", ("有庆", "医院")),
            ("月光照在路上，像是撒满了盐", ("福贵",)),
            ("人为什么还要活着？", ("福贵",)),
        ]
        for caption, entities in cases:
            with self.subTest(caption=caption):
                result = self._classify(caption, source_entities=entities)
                validate_visual_proposition(
                    result, shot_id="shot-x", source_entities=list(entities),
                    known_symbol_registry=list(result.surrogate_objects),
                )

    def test_missing_source_entities_is_a_hard_error(self) -> None:
        with self.assertRaises(PropositionClassifierError):
            classify_proposition(
                shot_id="shot-y",
                caption_texts=["凤霞出嫁"],
                description="凤霞出嫁",
                seed_rationale="",
                source_entities=[],
                character_anchors=CHARACTER_ANCHORS,
                scene_anchors=SCENE_ANCHORS,
                object_anchors=OBJECT_ANCHORS,
            )

    def test_empty_caption_set_is_a_hard_error(self) -> None:
        with self.assertRaises(PropositionClassifierError):
            classify_proposition(
                shot_id="shot-z",
                caption_texts=[],
                description="",
                seed_rationale="",
                source_entities=["福贵"],
                character_anchors=CHARACTER_ANCHORS,
                scene_anchors=SCENE_ANCHORS,
                object_anchors=OBJECT_ANCHORS,
            )


class RealWorldAnchorShapeTests(unittest.TestCase):
    """Regression: production visual bibles carry descriptive prompt subjects.

    A real ``character_anchors`` entry looks like
    ``{"character_id": "C001", "prompt_subject": "福贵，瘦削老年农民"}`` with no
    ``aliases`` key at all. The first classifier draft only matched the whole
    ``prompt_subject`` string, so the caption 福贵牵着老牛走过田埂 never matched
    福贵, fell through to the 老牛 symbolic trope and produced an atmosphere shot
    for a line that names a person. That is the exact failure mode this whole
    change exists to remove, so it is pinned here.
    """

    BARE_ANCHORS = {
        "CHAR_C001": {
            "prompt_subject": "福贵，瘦削老年农民",
            "natural_language": "福贵，瘦削老年农民",
        },
        "CHAR_C002": {
            "prompt_subject": "家珍，病弱的中年妇人",
            "natural_language": "家珍，病弱的中年妇人",
        },
    }

    def _classify(self, caption: str, *, source_entities, anchors=None, objects=None):
        return classify_proposition(
            shot_id="shot-real",
            caption_texts=[caption],
            description=caption,
            seed_rationale="字幕与画面共享当前场景",
            source_entities=list(source_entities),
            character_anchors=self.BARE_ANCHORS if anchors is None else anchors,
            scene_anchors={},
            object_anchors=objects or {},
        )

    def test_descriptive_prompt_subject_still_matches_the_bare_name(self) -> None:
        result = self._classify("福贵牵着老牛走过田埂。", source_entities=("福贵", "老牛"))
        self.assertEqual(
            result.mode,
            "Literal",
            msg="a caption naming 福贵 must not be downgraded to an atmosphere shot",
        )
        self.assertIn("福贵", [item.entity_id for item in result.entity_visibility])

    def test_symbolic_trope_never_outranks_a_named_character(self) -> None:
        result = self._classify("福贵牵着老牛走过田埂。", source_entities=("福贵", "老牛"))
        self.assertNotEqual(result.mode, "Symbolic")
        self.assertFalse(result.surrogate_objects)

    def test_required_entity_in_the_caption_is_literal_without_any_anchor(self) -> None:
        result = self._classify(
            "家珍把最后一碗米汤端给有庆。",
            source_entities=("家珍", "有庆", "米汤"),
            anchors={},
        )
        self.assertEqual(
            result.mode,
            "Literal",
            msg="required entities are authoritative even when the bible has no anchor",
        )
        visible = {item.entity_id for item in result.entity_visibility if item.must_be_visible}
        self.assertIn("家珍", visible)
        self.assertIn("有庆", visible)

    def test_required_entity_absent_from_the_caption_is_not_forced_visible(self) -> None:
        result = self._classify(
            "月光照在路上，像是撒满了盐。",
            source_entities=("福贵",),
            anchors={},
        )
        self.assertNotEqual(
            result.mode,
            "Literal",
            msg="福贵 is not spoken in this line, so the frame must not claim to show him",
        )

    def test_author_background_still_wins_over_a_named_entity(self) -> None:
        result = self._classify(
            "余华写这本书的时候只有三十岁。",
            source_entities=("福贵",),
        )
        self.assertEqual(result.mode, "Abstract")
        self.assertFalse([item for item in result.entity_visibility if item.must_be_visible])

    def test_every_real_shape_result_passes_its_own_validator(self) -> None:
        captions = [
            ("福贵牵着老牛走过田埂。", ("福贵", "老牛")),
            ("家珍把最后一碗米汤端给有庆。", ("家珍", "有庆")),
            ("月光照在路上，像是撒满了盐。", ("福贵",)),
            ("余华写这本书的时候只有三十岁。", ("福贵",)),
            ("人为什么还要活着？", ("福贵",)),
        ]
        for caption, entities in captions:
            with self.subTest(caption=caption):
                result = self._classify(caption, source_entities=entities)
                validate_visual_proposition(
                    result, shot_id="shot-real",
                    known_symbol_registry=list(result.surrogate_objects),
                )


class BridgeTests(unittest.TestCase):
    def test_direct_bridge_reports_overlap(self) -> None:
        bridge = evaluate_semantic_bridge(
            shot_id="s1",
            required_entities=["福贵", "老牛"],
            source_entities=["福贵", "田埂"],
            rationale="旁白说福贵牵着老牛走过田埂，画面给福贵与老牛的背影",
        )
        self.assertEqual(bridge.mode, "direct")
        self.assertEqual(bridge.shared_entities, ("福贵",))
        self.assertFalse(bridge.rationale_is_boilerplate)

    def test_direct_bridge_still_flags_boilerplate(self) -> None:
        bridge = evaluate_semantic_bridge(
            shot_id="s1",
            required_entities=["福贵"],
            source_entities=["福贵"],
            rationale="字幕与画面共享当前场景",
        )
        self.assertEqual(bridge.mode, "direct")
        self.assertTrue(bridge.rationale_is_boilerplate)


class ScoringTests(unittest.TestCase):
    def _proposition(self, mode: str = "Literal") -> VisualProposition:
        return VisualProposition(
            mode=mode,
            subject="扎麻花辫的少女",
            action="坐在花轿边回头看",
            environment="村口土路，黄昏",
            mood="克制的喜悦",
            lighting="LAMP_WARM",
            palette="EARTH_DAY",
            rationale_text="旁白说凤霞出嫁，画面必须出现凤霞本人与婚礼队伍",
            entity_visibility=(
                EntityVisibility("凤霞", True, "扎麻花辫的少女凤霞"),
                EntityVisibility("婚礼队伍", True, "抬花轿的送亲队伍"),
            ),
            source_terms=("凤霞", "婚礼队伍"),
        )

    def test_score_is_deterministic_and_bounded(self) -> None:
        proposition = self._proposition()
        first = score_alignment(
            proposition=proposition,
            caption_texts=["凤霞出嫁那天队伍走过村口"],
            source_entities=["凤霞", "婚礼队伍"],
        )
        second = score_alignment(
            proposition=proposition,
            caption_texts=["凤霞出嫁那天队伍走过村口"],
            source_entities=["凤霞", "婚礼队伍"],
        )
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["total"], 0.0)
        self.assertLessEqual(first["total"], 1.0)

    def test_matching_proposition_scores_higher_than_mismatched_one(self) -> None:
        good = score_alignment(
            proposition=self._proposition(),
            caption_texts=["凤霞出嫁那天队伍走过村口"],
            source_entities=["凤霞", "婚礼队伍"],
        )
        bad_proposition = VisualProposition(
            mode="Literal",
            subject="雪地里的孩子",
            action="堆雪人",
            environment="冬天的院子",
            mood="冷",
            lighting="COLD_DAY",
            palette="SNOW",
            rationale_text="这里换成雪地里的孩子",
            entity_visibility=(EntityVisibility("雪地", True, "厚雪覆盖的院子"),),
            source_terms=(),
        )
        bad = score_alignment(
            proposition=bad_proposition,
            caption_texts=["凤霞出嫁那天队伍走过村口"],
            source_entities=["凤霞", "婚礼队伍"],
        )
        self.assertGreater(good["total"], bad["total"])

    def test_components_are_reported(self) -> None:
        result = score_alignment(
            proposition=self._proposition(),
            caption_texts=["凤霞出嫁那天队伍走过村口"],
            source_entities=["凤霞", "婚礼队伍"],
        )
        for key in ("entity_coverage", "lexical_overlap", "rationale_substance", "mode_fit"):
            self.assertIn(key, result["components"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
