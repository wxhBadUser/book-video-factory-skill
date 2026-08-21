"""Focused regression tests for the four pilot R2 fixes.

Round-1 manual review verdict (REJECT_FOR_92_SHOT_BATCH) exposed exactly four
production defects; this file locks down their fixes only:

1. Observable Visual Event State  (caption_contract / prompting / compiler)
2. Concrete caption overrides Abstract  (caption_contract / classifier)
3. Semantic-incomplete Caption Groups  (caption_grouping / caption_contract)
4. Caption Contract authoritative over legacy Beat anchorRefs  (compiler)

All tests are deterministic: no network, no model, no randomness.
"""

from __future__ import annotations

import pytest

from book_video_factory.director_stage.compiler import (
    DirectorStageError,
    _contract_anchor_refs,
)
from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError,
    CaptionEntityEvidence,
    CaptionVisualContract,
    _caption_local_roles,
    _derive_visual_event_state,
    enrich_captions_to_contracts,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    derive_caption_image_groups,
    is_incomplete_clause,
)
from book_video_factory.semantic_alignment.classifier import (
    PropositionClassifierError,
    classify_proposition,
)
from book_video_factory.semantic_alignment.validation import (
    validate_visual_proposition,
)
from book_video_factory.semantic_alignment.models import EntityVisibility, VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    build_aligned_prompt_blocks,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def evidence(entity_id: str, natural_language: str) -> CaptionEntityEvidence:
    return CaptionEntityEvidence(
        entity_id=entity_id,
        natural_language=natural_language,
        reason="test",
        evidence={"text_evidence": natural_language},
    )


def contract(
    caption_id: str,
    text: str,
    *,
    narrative_function: str = "plot",
    visual_mode: str = "literal",
    must_show: tuple[CaptionEntityEvidence, ...] = (),
    location_id: str = "",
    action_key: str = "same_scene_sequence",
) -> CaptionVisualContract:
    return CaptionVisualContract(
        caption_id=caption_id,
        caption_text=text,
        caption_text_sha256=__import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
        section_id="S1",
        source_beat_ids=("B1",),
        narrative_function=narrative_function,
        subjects=tuple(item.natural_language for item in must_show),
        actions=(text,),
        location="",
        time_context="",
        story_objects=(),
        scene_state={
            "visible_character_ids": [
                item.entity_id for item in must_show if item.entity_id.startswith("C")
            ],
            "location_id": location_id,
            "time_context": "",
            "action_state": text,
            "continuity_state": {"pronoun_resolutions": []},
            "action_semantics": {
                "action_key": action_key,
                "incompatible_action_keys": [],
                "hard_split_event": "none",
                "event_instance_id": "beat:B1",
                "source_evidence": {"beat": {"beat_id": "B1"}, "caption": {"caption_id": caption_id}},
            },
        },
        must_show=must_show,
        may_show=(),
        must_not_show_as_primary=(),
        visual_focus=text[:20],
        visual_mode=visual_mode,
    )


def enrich_one(
    *,
    section_nf: str,
    section_text: str,
    caption_text: str,
    beat_required: list[str] | None = None,
    name_maps: list[dict[str, str]] | None = None,
) -> list[CaptionVisualContract]:
    beat = {
        "beatId": "B1",
        "cue": caption_text,
        "sectionId": "S1",
        "requiredEntities": beat_required or [],
        "forbiddenEntities": [],
        "riskFlags": [],
        "highRisk": False,
        "generationMode": "single",
    }
    return enrich_captions_to_contracts(
        script_sections=[{"section_id": "S1", "narrative_function": section_nf, "text": section_text}],
        beats=[beat],
        captions=[{"caption_id": "c1", "text": caption_text, "shot_ids": ["shot-1"]}],
        entity_name_maps=name_maps or [],
    )


# ---------------------------------------------------------------------------
# FIX 1 — Observable Visual Event State
# ---------------------------------------------------------------------------


class TestVisualEventState:
    def test_death_event_carries_observable_evidence_and_forbidden_state(self) -> None:
        state = _derive_visual_event_state(
            text="苦根是吃豆子撑死的。",
            narrative_function="plot",
            visual_mode="literal",
            must_show=(evidence("C009", "苦根"), evidence("OBJ_BEANS", "半锅豆子")),
            location="",
        )
        assert state.action_predicate == "death_aftermath"
        assert state.cause_type == "overeating_beans"
        assert any("倒伏" in item or "无反应" in item for item in state.required_observable_evidence)
        assert any("非血腥" in item for item in state.required_observable_evidence)
        assert any("端正坐在椅上像睡着" in item for item in state.forbidden_contradictory_state)

    def test_medical_visit_requires_doctor_context(self) -> None:
        state = _derive_visual_event_state(
            text="他进城去请郎中，",
            narrative_function="plot",
            visual_mode="literal",
            must_show=(evidence("C002", "福贵（中年/老年）"), evidence("ROLE_DOCTOR", "郎中")),
            location="",
        )
        assert state.action_predicate == "medical_visit"
        assert any("医馆" in item or "郎中" in item for item in state.required_observable_evidence)
        assert any("无医疗语境的无目的行走" in item for item in state.forbidden_contradictory_state)

    def test_absence_event_merges_with_the_beans_caption(self) -> None:
        captions = [
            {"caption_id": "c1", "text": "煮了半锅豆子放在床边，", "start": 0.0, "end": 3.0},
            {"caption_id": "c2", "text": "又下地去了。", "start": 3.0, "end": 5.0},
        ]
        contracts = {
            "c1": contract(
                "c1",
                "煮了半锅豆子放在床边，",
                must_show=(evidence("OBJ_BEANS", "半锅豆子"),),
                visual_mode="literal",
            ),
            "c2": contract("c2", "又下地去了。", visual_mode="literal"),
        }
        groups = derive_caption_image_groups(captions, contracts)
        assert len(groups) == 1
        assert groups[0].caption_ids == ("c1", "c2")
        joined_state = _derive_visual_event_state(
            text="煮了半锅豆子放在床边，又下地去了。",
            narrative_function="plot",
            visual_mode="literal",
            must_show=(evidence("OBJ_BEANS", "半锅豆子"),),
            location="",
        )
        assert any("门开向田野" in item or "背影远去" in item for item in joined_state.required_observable_evidence)

    def test_langzhong_is_a_caption_local_doctor_role(self) -> None:
        roles = _caption_local_roles("他进城去请郎中，")
        assert any(role_id == "ROLE_DOCTOR" for role_id, _term, _evidence in roles)

    def test_death_aftermath_must_not_read_as_sleep(self) -> None:
        """Pilot R2.1 (FIX B): lifeless-after-overeating, never a nap."""
        state = _derive_visual_event_state(
            text="苦根是吃豆子撑死的。",
            narrative_function="plot",
            visual_mode="literal",
            must_show=(evidence("C009", "苦根"), evidence("OBJ_BEANS", "半锅豆子")),
            location="",
        )
        assert "overeating_beans" in state.subject_state
        assert any("瘫软" in item or "倒伏" in item for item in state.required_observable_evidence)
        assert any("睡眠不相容" in item for item in state.required_observable_evidence)
        assert any("端正坐在椅上像睡着" in item for item in state.forbidden_contradictory_state)
        assert any("平静午睡姿态" in item for item in state.forbidden_contradictory_state)

    def test_return_home_requires_arrival_cues_and_forbids_extra_people(self) -> None:
        """Pilot R2.1 (FIX A): return_home must show the arriving relationship."""
        state = _derive_visual_event_state(
            text="但他回来了，家珍在，凤霞在，有庆也在。",
            narrative_function="plot",
            visual_mode="literal",
            must_show=(
                evidence("C002", "福贵（中年/老年）"),
                evidence("C003", "家珍"),
                evidence("C004", "凤霞"),
                evidence("C005", "有庆"),
            ),
            location="",
        )
        assert state.action_predicate == "return_home"
        assert any("正在进门或刚抵达" in item for item in state.required_observable_evidence)
        assert any("额外亲属/邻居" in item for item in state.forbidden_contradictory_state)
        assert any("家族合影般的静止群像" in item for item in state.forbidden_contradictory_state)


# ---------------------------------------------------------------------------
# FIX 2 — Concrete caption overrides Abstract
# ---------------------------------------------------------------------------


class TestConcreteCaptionOverridesAbstract:
    def test_closing_smoke_caption_stays_literal(self) -> None:
        results = enrich_one(
            section_nf="ending_image",
            section_text="炊烟在农舍的屋顶袅袅升起，",
            caption_text="炊烟在农舍的屋顶袅袅升起，",
        )
        result = results[0]
        assert result.visual_mode == "literal"
        names = {item.natural_language for item in result.must_show}
        assert {"炊烟", "农舍", "屋顶"} <= names

    def test_theory_caption_with_concrete_wedding_referents_is_literal(self) -> None:
        results = enrich_one(
            section_nf="theory",
            section_text="凤霞出嫁那天，",
            caption_text="凤霞出嫁那天，",
            name_maps=[{"C004": "凤霞"}],
        )
        assert results[0].visual_mode == "literal"
        assert any(item.natural_language == "凤霞" for item in results[0].must_show)

    def test_theory_caption_without_referents_is_not_literal(self) -> None:
        results = enrich_one(
            section_nf="theory",
            section_text="一个人只要还叫得出这些名字，那些人就一直没有离开。",
            caption_text="一个人只要还叫得出这些名字，",
        )
        assert results[0].visual_mode == "symbolic_or_abstract"
        assert results[0].must_show == ()

    def test_theory_without_mapping_fails_closed_in_classifier(self) -> None:
        with pytest.raises(PropositionClassifierError):
            classify_proposition(
                shot_id="s1",
                caption_texts=["一个人只要还叫得出这些名字，"],
                description="",
                seed_rationale="",
                source_entities=[],
                narrative_function="theory",
                visual_mode="symbolic_or_abstract",
            )

    def test_theory_with_approved_mapping_becomes_symbolic(self) -> None:
        proposition = classify_proposition(
            shot_id="s1",
            caption_texts=["一个人只要还叫得出这些名字，"],
            description="",
            seed_rationale="",
            source_entities=[],
            narrative_function="theory",
            visual_mode="symbolic_or_abstract",
            symbolic_mapping={
                "mapping_id": "SYM_MEMORY_NAMES_V1",
                "status": "approved",
                "source_concept": "叫得出这些名字",
                "surrogate_object": "旧屋里老照片与空椅",
            },
        )
        assert proposition.mode == "Symbolic"
        validated = validate_visual_proposition(
            proposition,
            shot_id="s1",
            caption_texts=["一个人只要还叫得出这些名字，"],
            source_entities=[],
            known_symbol_registry=("旧屋里老照片与空椅",),
        )
        assert validated.mode == "Symbolic"


# ---------------------------------------------------------------------------
# FIX 3 — Semantic-incomplete Caption Groups + author-creation metadata
# ---------------------------------------------------------------------------


class TestIncompleteCaptionGroups:
    def test_incomplete_clause_detector(self) -> None:
        assert is_incomplete_clause("写《活着》之前，")
        assert is_incomplete_clause("余华说他听了一首美国民歌，《")
        assert not is_incomplete_clause("煮了半锅豆子放在床边，")
        assert not is_incomplete_clause("老黑奴》。")

    def test_incomplete_clause_merges_until_complete(self) -> None:
        captions = [
            {"caption_id": "c1", "text": "写《活着》之前，", "start": 0.0, "end": 2.0},
            {"caption_id": "c2", "text": "余华说他听了一首美国民歌，《", "start": 2.0, "end": 4.0},
            {"caption_id": "c3", "text": "老黑奴》。", "start": 4.0, "end": 6.0},
        ]
        contracts = {
            cid: contract(cid, captions[i]["text"], narrative_function="author_background", visual_mode="symbolic_or_abstract")
            for i, cid in enumerate(("c1", "c2", "c3"))
        }
        groups = derive_caption_image_groups(captions, contracts)
        assert len(groups) == 1
        assert groups[0].caption_ids == ("c1", "c2", "c3")

    def test_unresolvable_incomplete_group_fails_closed(self) -> None:
        captions = [
            {"caption_id": "c1", "text": "写《活着》之前，", "start": 0.0, "end": 2.0},
            {"caption_id": "c2", "text": "福贵进城了。", "start": 2.0, "end": 4.0},
        ]
        contracts = {
            "c1": contract("c1", "写《活着》之前，", narrative_function="author_background", visual_mode="symbolic_or_abstract"),
            "c2": contract("c2", "福贵进城了。", narrative_function="plot", visual_mode="literal"),
        }
        with pytest.raises(CaptionGroupingError):
            derive_caption_image_groups(captions, contracts)


class TestAuthorCreationMetadata:
    def test_plot_register_with_author_creation_metadata_is_a_contract_error(self) -> None:
        with pytest.raises(CaptionContractError) as excinfo:
            enrich_one(
                section_nf="modern_mirror",
                section_text="写《活着》之前，余华说他听了一首美国民歌，《老黑奴》。",
                caption_text="余华说他听了一首美国民歌，《",
            )
        assert "S1" in str(excinfo.value)
        assert "author" in str(excinfo.value).lower()

    def test_author_background_demotes_the_finished_book(self) -> None:
        results = enrich_one(
            section_nf="author_background",
            section_text="写《活着》之前，余华说他听了一首美国民歌，《老黑奴》。",
            caption_text="写《活着》之前，",
            name_maps=[{"OBJ_BOOK": "《活着》书与旧照片"}],
        )
        result = results[0]
        assert result.visual_mode == "symbolic_or_abstract"
        must_ids = {item.entity_id for item in result.must_show}
        assert "OBJ_BOOK" not in must_ids
        may_ids = {item.entity_id for item in result.may_show}
        assert "OBJ_BOOK" in may_ids


# ---------------------------------------------------------------------------
# FIX 4 — Caption Contract authoritative over legacy Beat anchorRefs
# ---------------------------------------------------------------------------


class TestContractAuthoritativeAnchors:
    def test_contract_character_wins_over_stale_beat_anchorrefs(self) -> None:
        scene = {"id": "shot-1", "anchorRefs": ["C002", "C010"]}
        contracts = [
            contract(
                "c1",
                "凤霞出嫁那天，",
                narrative_function="theory",
                must_show=(
                    evidence("C004", "凤霞"),
                    evidence("OBJ_BRIDAL", "水红嫁衣与花轿"),
                    evidence("SCENE_WEDDING", "婚礼"),
                ),
                location_id="SCENE_WEDDING",
            )
        ]
        continuity = {"C004": "凤霞；清秀安静", "C002": "福贵", "C010": "老牛"}
        front_tasks = {
            "C004": "ANCHOR_C004_FRONT",
            "C002": "ANCHOR_C002_FRONT",
            "C010": "ANCHOR_C010_FRONT",
        }
        refs = _contract_anchor_refs(scene, contracts, continuity, front_tasks)
        assert refs == ["C004"]

    def test_missing_approved_asset_fails_closed(self) -> None:
        scene = {"id": "shot-1", "anchorRefs": ["C010"]}
        contracts = [
            contract(
                "c1",
                "到最后，只剩一个老头，和一头老牛。",
                narrative_function="opening",
                must_show=(evidence("C010", "老牛（福贵）"), evidence("OBJ_OX", "耕牛")),
            )
        ]
        with pytest.raises(DirectorStageError):
            _contract_anchor_refs(scene, contracts, {}, {})

    def test_local_roles_never_require_a_persistent_identity_asset(self) -> None:
        local = contract(
            "c1",
            "他进城去请郎中，",
            must_show=(evidence("ROLE_DOCTOR", "郎中"), evidence("C002", "福贵（中年/老年）")),
        )
        persistent = {
            item.entity_id
            for item in local.must_show
            if item.entity_id.startswith("C")
        }
        assert persistent == {"C002"}


# ---------------------------------------------------------------------------
# FIX A (R2.1) — exact narrative participant cardinality
# ---------------------------------------------------------------------------


class TestExactParticipantCardinality:
    def test_literal_family_scene_derives_exactly_four_contract_characters(self) -> None:
        text = "但他回来了，家珍在，凤霞在，有庆也在。"
        prior_text = "福贵回来了。"
        section_text = prior_text + text
        results = enrich_captions_to_contracts(
            script_sections=[{"section_id": "S1", "narrative_function": "plot", "text": section_text}],
            beats=[
                {
                    "beatId": "B0",
                    "cue": prior_text,
                    "sectionId": "S1",
                    "requiredEntities": ["C002"],
                    "forbiddenEntities": [],
                    "riskFlags": [],
                    "highRisk": False,
                    "generationMode": "single",
                },
                {
                    "beatId": "B1",
                    "cue": text,
                    "sectionId": "S1",
                    "requiredEntities": ["C002", "C003", "C004", "C005"],
                    "forbiddenEntities": [],
                    "riskFlags": [],
                    "highRisk": False,
                    "generationMode": "single",
                },
            ],
            captions=[
                {"caption_id": "c0", "text": prior_text, "shot_ids": ["shot-0"]},
                {"caption_id": "c1", "text": text, "shot_ids": ["shot-1"]},
            ],
            entity_name_maps=[
                {"C002": "福贵（中年/老年）", "C003": "家珍", "C004": "凤霞", "C005": "有庆"},
            ],
        )
        contract = results[1]
        assert set(contract.expected_visible_character_ids) == {"C002", "C003", "C004", "C005"}
        assert contract.expected_narrative_character_count == 4
        assert contract.allow_unlisted_narrative_characters is False

    def test_prompt_states_exact_count_and_forbids_extra_people(self) -> None:
        proposition = VisualProposition(
            mode="Literal",
            subject="福贵",
            action="他回来了，家珍在，凤霞在，有庆也在。",
            environment="茅屋村口",
            mood="归家",
            lighting="DUSK_SOFT",
            palette="EARTH_DUSK",
            rationale_text="旁白点名四位家人，画面必须四人同框并表达归家关系",
            entity_visibility=(
                EntityVisibility("C002", True, "福贵（中年/老年）"),
                EntityVisibility("C003", True, "家珍"),
                EntityVisibility("C004", True, "凤霞"),
                EntityVisibility("C005", True, "有庆"),
            ),
        )
        blocks = build_aligned_prompt_blocks(
            caption_text="但他回来了，家珍在，凤霞在，有庆也在。",
            proposition=proposition,
            narrative_function="plot",
            camera={"shot_size": "wide", "lens": "35mm", "camera_angle": "eye", "composition": "c", "depth": "d"},
            style={"visual_world": "w", "palette": "p", "texture": "t"},
            must_show=["福贵（中年/老年）", "家珍", "凤霞", "有庆"],
            expected_visible_character_ids=["C002", "C003", "C004", "C005"],
            expected_narrative_character_count=4,
            allow_unlisted_narrative_characters=False,
        )
        prompt = "\n".join(blocks)
        assert "Exactly 4 narrative characters are present in this scene" in prompt
        assert "Do not add another woman, man, child" in prompt
        assert "extra family member in the foreground or midground" in prompt
