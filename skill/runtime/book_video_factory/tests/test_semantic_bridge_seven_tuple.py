"""L10 adversarial test: semantic-bridge seven-tuple + Symbolic anchor + Abstract no-blank.

The ``direct`` (Literal) bridge used to grant passage on a single shared entity
alone. Attack A-bridge showed that ``凤霞出嫁`` (Fengxia marries) plus a
rationale ``凤霞雪地奔跑`` (Fengxia runs in snow) -- which share only the subject
凤霞 -- was accepted as a literal depiction even though the image shows a
*different* event. The seven-tuple joint constraint on the direct path must
reject that.

Separately:
* Symbolic propositions must reference an *established* surrogate (a registered
  trope / prior scene / hash-bound symbol); pointing at unestablished imagery is
  a hallucination of meaning and must be rejected.
* Abstract propositions must never degrade into a pure blank frame: they need a
  mood AND at least one concrete rendering dimension.

Mutation criterion (plan §0): on committed pre-remediation HEAD the
``direct`` path has no event-alignment guard and ``validate_visual_proposition``
neither accepts ``known_symbol_registry`` nor enforces the no-blank rule, so the
REJECTING tests below fail there (return ``direct`` / pass silently / TypeError).
"""

from __future__ import annotations

import unittest

from book_video_factory.semantic_alignment.models import EntityVisibility, VisualProposition
from book_video_factory.semantic_alignment.validation import (
    SemanticContractError,
    evaluate_semantic_bridge,
    validate_visual_proposition,
)


class SevenTupleDirectTests(unittest.TestCase):
    def test_A_bridge_direct_rejected_on_event_divergence(self) -> None:
        # Shared subject 凤霞, but the narration says "marry" and the image
        # rationale says "run in snow" -- a different event. Must NOT be direct.
        with self.assertRaises(SemanticContractError):
            evaluate_semantic_bridge(
                shot_id="as-fengxia",
                required_entities=["凤霞"],
                source_entities=["凤霞"],
                rationale="凤霞雪地奔跑，红棉袄在风里翻飞",
                caption_texts=["凤霞出嫁那天，唢呐声盖过了哭声"],
            )

    def test_legit_direct_still_passes_when_event_overlaps(self) -> None:
        bridge = evaluate_semantic_bridge(
            shot_id="as-fugui",
            required_entities=["福贵", "老牛"],
            source_entities=["福贵", "田埂"],
            rationale="福贵牵着老牛走过田埂，画面给福贵与老牛的背影",
            caption_texts=["福贵牵着老牛走过田埂"],
        )
        self.assertEqual(bridge.mode, "direct")
        self.assertEqual(bridge.subject, "福贵")
        self.assertEqual(bridge.event_alignment, "verified")

    def test_direct_boilerplate_rationale_not_rejected(self) -> None:
        # Boilerplate rationale predates the event-alignment guard; it is still
        # allowed in direct mode (recorded, not fatal) and must not trip the
        # seven-tuple rule. This pins the existing behavior so the upgrade is
        # additive, not a silent regression.
        bridge = evaluate_semantic_bridge(
            shot_id="as-bp",
            required_entities=["凤霞"],
            source_entities=["凤霞"],
            rationale="字幕与画面共享当前场景",
            caption_texts=["凤霞出嫁那天，唢呐声盖过了哭声"],
        )
        self.assertEqual(bridge.mode, "direct")
        self.assertTrue(bridge.rationale_is_boilerplate)
        self.assertEqual(bridge.event_alignment, "boilerplate-skipped")


class SymbolicAnchorTests(unittest.TestCase):
    def _symbolic(self, *, surrogate: str, source: str, rationale: str) -> VisualProposition:
        return VisualProposition(
            mode="Symbolic",
            subject="",
            action="",
            environment="",
            mood="",
            lighting="",
            palette="",
            rationale_text=rationale,
            surrogate_objects=(surrogate,),
            source_terms=(source,),
        )

    def test_symbolic_unestablished_rejected(self) -> None:
        prop = self._symbolic(
            surrogate="红棉袄",
            source="凤霞",
            rationale="用红棉袄象征凤霞的离别",
        )
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(
                prop,
                shot_id="as-sym",
                known_symbol_registry=("月亮", "老牛", "盐路"),
            )

    def test_symbolic_established_passes(self) -> None:
        prop = self._symbolic(
            surrogate="老牛",
            source="福贵",
            rationale="用老牛象征福贵暮年的孤独",
        )
        # Established surrogate -> no raise.
        validate_visual_proposition(
            prop,
            shot_id="as-sym",
            known_symbol_registry=("老牛", "盐路"),
        )

    def test_symbolic_without_registry_still_requires_naming(self) -> None:
        # Backward compatible: when no registry is supplied the stricter anchor
        # check is skipped, but the existing name-both-sides rule still holds.
        prop = self._symbolic(
            surrogate="老牛",
            source="福贵",
            rationale="画面给福贵与老牛",  # names both sides
        )
        validate_visual_proposition(prop, shot_id="as-sym")  # no raise


class AbstractNoBlankTests(unittest.TestCase):
    def _abstract(self, *, mood: str, palette: str = "", environment: str = "", lighting: str = "") -> VisualProposition:
        return VisualProposition(
            mode="Abstract",
            subject="",
            action="",
            environment=environment,
            mood=mood,
            lighting=lighting,
            palette=palette,
            rationale_text="纯氛围镜头，不出现具体叙事指涉",
        )

    def test_abstract_mood_only_rejected(self) -> None:
        # Mood present but no concrete rendering dimension -> blank frame.
        # HEAD allowed this (mood OR palette); the upgrade rejects it.
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(self._abstract(mood="沉静、留白"), shot_id="as-abs")

    def test_abstract_blank_rejected(self) -> None:
        with self.assertRaises(SemanticContractError):
            validate_visual_proposition(
                self._abstract(mood="", palette="", environment="", lighting=""),
                shot_id="as-abs",
            )

    def test_abstract_valid_passes(self) -> None:
        validate_visual_proposition(
            self._abstract(mood="沉静、留白", palette="EARTH_DAY"),
            shot_id="as-abs",
        )


if __name__ == "__main__":
    unittest.main()
