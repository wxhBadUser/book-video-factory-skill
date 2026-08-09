"""Part 4 - the image prompt must be driven by the caption, and stay bound to it.

Two production defects are locked down here.

1. Priority inversion. The old prompt led with art direction and buried the
   narration line in the middle, so the model optimised for style and treated
   the caption as flavour text. The caption must come first, then the factual
   proposition, then the required scene/action and referents, then identity
   references, prohibitions, camera, style, and only then the
   symbolic explanation.

2. Silent staleness. A prompt could be generated from caption A and later reused
   after the caption, the proposition or the prompt itself changed. Nothing
   detected it. Every prompt now carries a binding of six identifiers/hashes and
   the binding must fail closed the moment any of them drifts.
"""

from __future__ import annotations

import pytest

from book_video_factory.semantic_alignment.models import EntityVisibility, VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PROMPT_BLOCK_ORDER,
    PromptBindingError,
    PromptSpecError,
    build_aligned_prompt_blocks,
    compute_prompt_binding,
    verify_legacy_prompt_binding,
)


def literal_proposition() -> VisualProposition:
    return VisualProposition(
        mode="Literal",
        subject="福贵",
        action="牵着老牛走过田埂",
        environment="黄昏的稻田与土路",
        mood="疲惫而平静",
        lighting="DUSK_SOFT",
        palette="EARTH_DUSK",
        rationale_text="字幕明确写到福贵牵着老牛走过田埂，画面直接呈现福贵与老牛。",
        entity_visibility=(
            EntityVisibility("福贵", True, "一个瘦削的老年农民，粗布短褂"),
            EntityVisibility("老牛", True, "一头年迈的水牛，脊背凹陷"),
        ),
        source_terms=("福贵", "老牛", "田埂"),
    )


def symbolic_proposition() -> VisualProposition:
    return VisualProposition(
        mode="Symbolic",
        subject="一只空碗",
        action="被放在冰凉的灶台上",
        environment="熄了火的土灶",
        mood="沉默的匮乏",
        lighting="DUSK_SOFT",
        palette="EARTH_DUSK",
        rationale_text="字幕讲的是饥荒带来的失去，画面用灶台上的空碗承载饥荒这一含义。",
        surrogate_objects=("空碗", "冷灶"),
        source_terms=("饥荒", "失去"),
    )


def abstract_proposition() -> VisualProposition:
    return VisualProposition(
        mode="Abstract",
        subject="空旷的田野",
        action="风吹过",
        environment="没有人的黄昏原野",
        mood="悬置的沉思",
        lighting="DUSK_SOFT",
        palette="EARTH_DUSK",
        rationale_text="这句是叙述者的追问，没有可画的指称对象，画面只提供氛围而不声称描绘任何具体人物。",
    )


CAMERA = {
    "shot_size": "medium wide",
    "lens": "40mm",
    "camera_angle": "eye level",
    "composition": "subject on the left third",
    "depth": "shallow depth of field",
}
STYLE = {
    "visual_world": "literary oil painting",
    "palette": "umber and ivory",
    "texture": "linen canvas",
}


class TestBlockOrder:
    def test_declared_order_is_the_documented_priority(self) -> None:
        assert PROMPT_BLOCK_ORDER == (
            "caption",
            "proposition",
            "required_scene",
            "required_visible",
            "anchors",
            "forbidden",
            "camera",
            "style",
            "symbolic_explanation",
        )

    def test_caption_is_the_very_first_block(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            camera=CAMERA,
            style=STYLE,
        )
        assert blocks[0].startswith("[1/9 CAPTION]")
        assert "福贵牵着老牛走过田埂。" in blocks[0]

    def test_blocks_are_emitted_in_priority_order(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            forbidden_entities=("凤霞", "雪地"),
            camera=CAMERA,
            anchors=("福贵：瘦削、灰白短发、粗布短褂",),
            style=STYLE,
        )
        indices = [int(block.split("/", 1)[0].lstrip("[")) for block in blocks]
        assert indices == sorted(indices)
        assert indices[0] == 1

    def test_style_never_precedes_the_caption(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            camera=CAMERA,
            style=STYLE,
        )
        text = "\n".join(blocks)
        assert text.index("福贵牵着老牛走过田埂。") < text.index("literary oil painting")

    def test_forbidden_entities_are_rendered_when_present(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            forbidden_entities=("凤霞", "雪地"),
            camera=CAMERA,
            style=STYLE,
        )
        forbidden = [b for b in blocks if b.startswith("[6/9 FORBIDDEN]")]
        assert forbidden and "凤霞" in forbidden[0] and "雪地" in forbidden[0]

    def test_required_scene_block_names_the_narrative_register(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="余华写这本书的时候只有三十岁。",
            proposition=abstract_proposition(),
            narrative_function="author_background",
            camera=CAMERA,
            style=STYLE,
        )
        block = next(b for b in blocks if b.startswith("[3/9 REQUIRED SCENE]"))
        assert "author_background" in block


class TestModeSpecificRules:
    def test_literal_lists_every_required_referent(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            camera=CAMERA,
            style=STYLE,
        )
        block = next(b for b in blocks if b.startswith("[4/9 REQUIRED VISIBLE]"))
        assert "福贵" in block and "老牛" in block
        assert "一头年迈的水牛" in block

    def test_literal_without_a_visible_referent_is_rejected(self) -> None:
        bare = VisualProposition(
            mode="Literal",
            subject="福贵",
            action="走过田埂",
            environment="稻田",
            mood="平静",
            lighting="DUSK_SOFT",
            palette="EARTH_DUSK",
            rationale_text="字幕写到福贵走过田埂，画面直接呈现福贵在田埂上。",
        )
        with pytest.raises(PromptSpecError):
            build_aligned_prompt_blocks(
                caption_text="福贵走过田埂。",
                proposition=bare,
                narrative_function="plot",
                camera=CAMERA,
                style=STYLE,
            )

    def test_symbolic_emits_the_explanation_block_last(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="那场饥荒带走了太多人。",
            proposition=symbolic_proposition(),
            narrative_function="plot",
            camera=CAMERA,
            style=STYLE,
        )
        assert blocks[-1].startswith("[9/9 SYMBOLIC]")
        assert "空碗" in blocks[-1]
        assert "饥荒" in blocks[-1]

    def test_literal_has_no_symbolic_block(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            narrative_function="plot",
            camera=CAMERA,
            style=STYLE,
        )
        assert not any(b.startswith("[9/9 SYMBOLIC]") for b in blocks)

    def test_abstract_declares_that_no_referent_is_claimed(self) -> None:
        blocks = build_aligned_prompt_blocks(
            caption_text="活着究竟是为了什么？",
            proposition=abstract_proposition(),
            narrative_function="theory",
            camera=CAMERA,
            style=STYLE,
        )
        block = next(b for b in blocks if b.startswith("[4/9 REQUIRED VISIBLE]"))
        assert "no narrative referent" in block.lower()

    def test_abstract_may_not_demand_visible_entities(self) -> None:
        bad = VisualProposition(
            mode="Abstract",
            subject="空旷的田野",
            action="风吹过",
            environment="黄昏原野",
            mood="沉思",
            lighting="DUSK_SOFT",
            palette="EARTH_DUSK",
            rationale_text="这句是叙述者的追问，没有可画的指称对象，画面只提供氛围。",
            entity_visibility=(EntityVisibility("福贵", True, "老农"),),
        )
        with pytest.raises(PromptSpecError):
            build_aligned_prompt_blocks(
                caption_text="活着究竟是为了什么？",
                proposition=bad,
                narrative_function="theory",
                camera=CAMERA,
                style=STYLE,
            )

    def test_empty_caption_is_rejected(self) -> None:
        with pytest.raises(PromptSpecError):
            build_aligned_prompt_blocks(
                caption_text="   ",
                proposition=literal_proposition(),
                narrative_function="plot",
                camera=CAMERA,
                style=STYLE,
            )

    def test_unknown_narrative_function_is_rejected(self) -> None:
        with pytest.raises(PromptSpecError):
            build_aligned_prompt_blocks(
                caption_text="福贵牵着老牛走过田埂。",
                proposition=literal_proposition(),
                narrative_function="vibes",
                camera=CAMERA,
                style=STYLE,
            )


class TestBinding:
    def make(self, prompt: str = "PROMPT-BODY") -> tuple[dict, str]:
        proposition = literal_proposition()
        binding = compute_prompt_binding(
            caption_ids=("c1", "c2"),
            caption_text="福贵牵着老牛走过田埂。",
            proposition=proposition,
            prompt=prompt,
            scene_id="SC-001",
            beat_ids=("B-001",),
            shot_id="SHOT-001",
        )
        return binding, prompt

    def test_binding_carries_all_six_identifiers(self) -> None:
        binding, _ = self.make()
        for key in (
            "caption_ids",
            "caption_text_sha256",
            "proposition_sha256",
            "prompt_sha256",
            "scene_id",
            "beat_ids",
        ):
            assert key in binding, key
        assert binding["shot_id"] == "SHOT-001"
        assert binding["caption_ids"] == ["c1", "c2"]

    def test_binding_is_deterministic(self) -> None:
        first, _ = self.make()
        second, _ = self.make()
        assert first == second

    def test_verify_accepts_the_unchanged_triple(self) -> None:
        binding, prompt = self.make()
        verify_legacy_prompt_binding(
            binding,
            caption_text="福贵牵着老牛走过田埂。",
            proposition=literal_proposition(),
            prompt=prompt,
        )

    def test_caption_edit_invalidates_the_binding(self) -> None:
        binding, prompt = self.make()
        with pytest.raises(PromptBindingError) as excinfo:
            verify_legacy_prompt_binding(
                binding,
                caption_text="福贵牵着老牛走过田埂，天快黑了。",
                proposition=literal_proposition(),
                prompt=prompt,
            )
        assert "caption" in str(excinfo.value)

    def test_proposition_edit_invalidates_the_binding(self) -> None:
        binding, prompt = self.make()
        mutated = VisualProposition(**{**literal_proposition().__dict__, "action": "坐在门槛上"})
        with pytest.raises(PromptBindingError) as excinfo:
            verify_legacy_prompt_binding(
                binding,
                caption_text="福贵牵着老牛走过田埂。",
                proposition=mutated,
                prompt=prompt,
            )
        assert "proposition" in str(excinfo.value)

    def test_prompt_edit_invalidates_the_binding(self) -> None:
        binding, _ = self.make()
        with pytest.raises(PromptBindingError) as excinfo:
            verify_legacy_prompt_binding(
                binding,
                caption_text="福贵牵着老牛走过田埂。",
                proposition=literal_proposition(),
                prompt="PROMPT-BODY-EDITED",
            )
        assert "prompt" in str(excinfo.value)

    def test_binding_without_captions_is_rejected(self) -> None:
        with pytest.raises(PromptSpecError):
            compute_prompt_binding(
                caption_ids=(),
                caption_text="福贵牵着老牛走过田埂。",
                proposition=literal_proposition(),
                prompt="P",
                scene_id="SC-001",
                beat_ids=("B-001",),
                shot_id="SHOT-001",
            )

    def test_binding_without_beats_is_rejected(self) -> None:
        with pytest.raises(PromptSpecError):
            compute_prompt_binding(
                caption_ids=("c1",),
                caption_text="福贵牵着老牛走过田埂。",
                proposition=literal_proposition(),
                prompt="P",
                scene_id="SC-001",
                beat_ids=(),
                shot_id="SHOT-001",
            )

    def test_verify_rejects_a_binding_missing_a_field(self) -> None:
        binding, prompt = self.make()
        del binding["prompt_sha256"]
        with pytest.raises(PromptBindingError):
            verify_legacy_prompt_binding(
                binding,
                caption_text="福贵牵着老牛走过田埂。",
                proposition=literal_proposition(),
                prompt=prompt,
            )
