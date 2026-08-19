"""Task 1: Caption Visual Contract v2 evidence-grounding tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError,
    CaptionVisualContract,
    _build_project_entity_metadata,
    build_caption_visual_contract_document,
    enrich_captions_to_contracts,
)


def _section(*, narrative_function: str, text: str) -> dict[str, str]:
    return {"section_id": "S1", "narrative_function": narrative_function, "text": text}


def _beat(
    *,
    cue: str,
    required_entities: list[str],
    narrative_function: str | None = None,
    risk_flags: list[str] | None = None,
    high_risk: bool = False,
    generation_mode: str = "2x2",
) -> dict:
    beat = {
        "beatId": "B1",
        "sectionId": "S1",
        "cue": cue,
        "description": cue,
        "requiredEntities": required_entities,
        "forbiddenEntities": [],
        "riskFlags": risk_flags or [],
        "highRisk": high_risk,
        "generationMode": generation_mode,
    }
    if narrative_function is not None:
        beat["narrative_function"] = narrative_function
    return beat


def test_contract_derives_structured_action_semantics_from_locked_beat_evidence() -> None:
    """A locked death/climax risk becomes a controlled event, never a text guess."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="A neutral caption.")],
        beats=[
            _beat(
                cue="A neutral caption.",
                required_entities=[],
                risk_flags=["death_climax"],
                high_risk=True,
                generation_mode="single",
            )
        ],
        captions=[{"caption_id": "c1", "text": "A neutral caption.", "shot_ids": ["shot-b1"]}],
    )

    semantics = contracts[0].scene_state["action_semantics"]
    assert semantics["action_key"] == "event:climax:B1"
    assert semantics["incompatible_action_keys"] == []
    assert semantics["hard_split_event"] == "climax"
    assert semantics["event_instance_id"] == "beat:B1"
    assert semantics["source_evidence"] == {
        "beat": {"beat_id": "B1", "risk_flags": ["death_climax"], "high_risk": True, "generation_mode": "single"},
        "caption": {"caption_id": "c1", "shot_ids": ["shot-b1"], "semantic_rationale": ""},
    }


def test_unclassified_caption_text_does_not_create_a_hard_event_without_locked_evidence() -> None:
    """Words alone cannot invent a death, birth, hero, or high-risk event."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="gunshot murdered drowning suicide birth hero")],
        beats=[_beat(cue="gunshot murdered drowning suicide birth hero", required_entities=[])],
        captions=[{"caption_id": "c1", "text": "gunshot murdered drowning suicide birth hero"}],
    )

    semantics = contracts[0].scene_state["action_semantics"]
    assert semantics["action_key"] == "same_scene_sequence"
    assert semantics["hard_split_event"] == "none"
    assert semantics["event_instance_id"] == "sequence:B1"


def test_v2_contract_with_missing_structured_action_semantics_is_rejected() -> None:
    """Persisted v2 cannot silently recover a missing compatibility declaration."""
    contract = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="A locked caption.")],
        beats=[_beat(cue="A locked caption.", required_entities=[])],
        captions=[{"caption_id": "c1", "text": "A locked caption."}],
    )[0]
    raw = contract.to_dict()
    raw["scene_state"].pop("action_semantics")

    with pytest.raises(CaptionContractError, match="action_semantics"):
        CaptionVisualContract.from_mapping(raw)


def test_new_v2_document_cannot_serialize_a_contract_without_action_semantics() -> None:
    """The document writer must not create a nonconforming v2 artifact."""
    contract = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="A locked caption.")],
        beats=[_beat(cue="A locked caption.", required_entities=[])],
        captions=[{"caption_id": "c1", "text": "A locked caption."}],
    )[0]
    invalid = replace(contract, scene_state={key: value for key, value in contract.scene_state.items() if key != "action_semantics"})

    with pytest.raises(CaptionContractError, match="action_semantics"):
        build_caption_visual_contract_document(release_id="r1", contracts=[invalid])


def test_beat_character_not_named_by_caption_stays_out_of_must_show() -> None:
    """A beat character is context only when the caption names no character."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="theory", text="命运从不向谁解释。")],
        beats=[_beat(cue="命运从不向谁解释。", required_entities=["C002"])],
        captions=[{"caption_id": "c1", "text": "命运从不向谁解释。"}],
        entity_name_maps=[{"C002": "福贵"}],
    )

    contract = contracts[0]
    assert contract.section_id == "S1"
    assert contract.must_show == ()
    assert contract.to_dict()["may_show"] == [
        {
            "entity_id": "C002",
            "natural_language": "福贵",
            "reason": "beat_required_entity_context",
            "evidence": {"source_beat_id": "B1", "required_entity": "C002"},
        }
    ]


def test_resolvable_pronoun_records_upstream_caption_evidence() -> None:
    """A pronoun may require a character only with recorded prior-caption evidence."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="福贵抬头。他低头看着那头牛。")],
        beats=[
            _beat(cue="福贵抬头。", required_entities=["C002"]),
            {
                **_beat(cue="他低头看着那头牛。", required_entities=["C002", "C010"]),
                "beatId": "B2",
            },
        ],
        captions=[
            {"caption_id": "c1", "text": "福贵抬头。"},
            {"caption_id": "c2", "text": "他低头看着那头牛。"},
        ],
        entity_name_maps=[{"C002": "福贵", "C010": "老牛"}],
    )

    pronoun_entry = next(item for item in contracts[1].to_dict()["must_show"] if item["entity_id"] == "C002")
    assert pronoun_entry["natural_language"] == "福贵"
    assert pronoun_entry["reason"] == "pronoun_resolution"
    assert pronoun_entry["evidence"] == {
        "resolved_entity_id": "C002",
        "pronoun_resolution": "他",
            "upstream_caption_id": "c1",
            "upstream_caption_text": "福贵抬头。",
            "upstream_caption_span": {"start": 0, "end": 2, "text": "福贵"},
            "antecedent_clause_text": "福贵抬头",
            "antecedent_clause_span": {"start": 0, "end": 4, "text": "福贵抬头"},
        "resolution_basis": {
            "kind": "current_action_required_participant",
            "source_beat_id": "B2",
            "candidate_entity_ids": ["C002", "C010"],
        },
        "text_evidence": "他",
    }


def test_beat_narrative_function_conflict_with_section_fails_closed() -> None:
    """The locked script section is authoritative; beat disagreement is an error."""
    with pytest.raises(CaptionContractError, match="narrative_function conflicts"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="theory", text="命运从不向谁解释。")],
            beats=[
                _beat(
                    cue="命运从不向谁解释。",
                    required_entities=[],
                    narrative_function="plot",
                )
            ],
            captions=[{"caption_id": "c1", "text": "命运从不向谁解释。"}],
        )


def test_section_only_caption_uses_locked_section_without_beat_entities() -> None:
    """An intro caption may bind directly to exactly one locked script section."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="hook", text="名著太难读了。我们一起读完五十二本。")],
        beats=[],
        captions=[{"caption_id": "c1", "text": "我们一起读完五十二本。"}],
        entity_name_maps=[{"C002": "福贵"}],
    )

    contract = contracts[0]
    assert contract.section_id == "S1"
    assert contract.source_beat_ids == ()
    assert contract.narrative_function == "opening"
    assert contract.must_show == ()
    assert contract.to_dict()["may_show"] == []
    assert contract.to_dict()["scene_state"]["visible_character_ids"] == []
    assert CaptionVisualContract.from_mapping(contract.to_dict()).source_beat_ids == ()


def test_section_only_caption_without_locked_section_fails_closed() -> None:
    """Section-only mode is unavailable when the caption text proves no section membership."""
    with pytest.raises(CaptionContractError, match="locked script section"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="hook", text="名著太难读了。")],
            beats=[],
            captions=[{"caption_id": "c1", "text": "这句话不在锁定脚本里。"}],
        )


def test_equally_specific_beats_with_different_authority_fields_fail_closed() -> None:
    """Identical cues cannot select an input-order Beat, section, or register."""
    cue = "命运从不向谁解释。"
    with pytest.raises(CaptionContractError, match="ambiguous beat"):
        enrich_captions_to_contracts(
            script_sections=[
                {"section_id": "S1", "narrative_function": "theory", "text": cue},
                {"section_id": "S2", "narrative_function": "plot", "text": cue},
            ],
            beats=[
                {**_beat(cue=cue, required_entities=[], narrative_function="theory"), "beatId": "B1", "sectionId": "S1"},
                {**_beat(cue=cue, required_entities=[], narrative_function="plot"), "beatId": "B2", "sectionId": "S2"},
            ],
            captions=[{"caption_id": "c1", "text": cue}],
        )


def test_pronoun_with_multiple_people_in_current_context_fails_closed() -> None:
    """The review reproduction cannot select either person from one caption."""
    with pytest.raises(CaptionContractError, match="nearest prior caption c1"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵和家珍站在村口。他转身离开。")],
            beats=[
                _beat(cue="福贵和家珍站在村口。", required_entities=["C001", "C003"]),
                {**_beat(cue="他转身离开。", required_entities=["C001", "C003"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "福贵和家珍站在村口。"},
                {"caption_id": "c2", "text": "他转身离开。"},
            ],
            entity_name_maps=[{"C001": "福贵", "C003": "家珍"}],
        )


def test_local_mother_role_does_not_bind_jiazhen_or_ambiguate_him() -> None:
    """娘 is caption-local; the next 他 resolves only to the current 福贵 id."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="福贵的娘病了。他进城去请郎中。")],
        beats=[
            _beat(cue="福贵的娘病了。", required_entities=["C002", "C003"]),
            {**_beat(cue="他进城去请郎中。", required_entities=["C002", "C003", "C004", "C005"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "福贵的娘病了。"},
            {"caption_id": "c2", "text": "他进城去请郎中。"},
        ],
        entity_name_maps=[{"C002": "福贵", "C003": "家珍", "C004": "凤霞", "C005": "有庆"}],
    )

    mother = next(item for item in contracts[0].to_dict()["must_show"] if item["entity_id"] == "ROLE_MOTHER")
    assert mother["natural_language"] == "娘"
    assert mother["evidence"] == {
        "caption_local": True,
        "caption_span": {"start": 3, "end": 4, "text": "娘"},
        "entity_kind": "character",
        "gender": "female",
        "role": "mother",
        "text_evidence": "娘",
    }
    assert "C003" not in {item["entity_id"] for item in contracts[0].to_dict()["must_show"]}

    entry = next(item for item in contracts[1].to_dict()["must_show"] if item["entity_id"] == "C002")
    assert entry["evidence"]["upstream_caption_id"] == "c1"
    assert entry["evidence"]["upstream_caption_span"] == {"start": 0, "end": 2, "text": "福贵"}
    assert entry["evidence"]["resolution_basis"] == {
        "kind": "current_action_required_participant",
        "source_beat_id": "B2",
        "candidate_entity_ids": ["C002", "C003", "C004", "C005"],
    }


def test_lamp_caption_evidence_never_binds_book() -> None:
    """煤油灯 is the lamp object, not a cross-object alias for the book."""
    contract = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="theory", text="煤油灯亮着。")],
        beats=[_beat(cue="煤油灯亮着。", required_entities=["OBJ_BOOK", "OBJ_LAMP"])],
        captions=[{"caption_id": "c1", "text": "煤油灯亮着。"}],
        entity_name_maps=[{"OBJ_BOOK": "《活着》书", "OBJ_LAMP": "煤油灯"}],
    )[0]

    assert [item.entity_id for item in contract.must_show] == ["OBJ_LAMP"]


def test_same_caption_fengxia_resolves_both_prior_pronouns() -> None:
    """凤霞 is the unique current-caption antecedent for both later 她 tokens."""
    contract = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="凤霞又聋又哑，村里人笑话她，说她想男人。")],
        beats=[_beat(cue="凤霞又聋又哑，村里人笑话她，说她想男人。", required_entities=["C002", "C003", "C004", "C008"])],
        captions=[{"caption_id": "c1", "text": "凤霞又聋又哑，村里人笑话她，说她想男人。"}],
        entity_name_maps=[{"C002": "福贵", "C003": "家珍", "C004": "凤霞", "C008": "二喜"}],
    )[0]

    resolutions = contract.to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"]
    assert [item["resolved_entity_id"] for item in resolutions] == ["C004", "C004"]
    assert [item["antecedent_caption_span"] for item in resolutions] == [
        {"start": 0, "end": 2, "text": "凤霞"},
        {"start": 0, "end": 2, "text": "凤霞"},
    ]
    assert [item["pronoun_span"] for item in resolutions] == [
        {"start": 12, "end": 13, "text": "她"},
        {"start": 15, "end": 16, "text": "她"},
    ]


def test_same_caption_two_current_people_before_pronoun_fails_closed() -> None:
    """The reviewer reproduction remains ambiguous before any backward scan."""
    with pytest.raises(CaptionContractError, match="same caption"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵和家珍站在村口，他转身离开。")],
            beats=[_beat(cue="福贵和家珍站在村口，他转身离开。", required_entities=["C001", "C003"])],
            captions=[{"caption_id": "c1", "text": "福贵和家珍站在村口，他转身离开。"}],
            entity_name_maps=[{"C001": "福贵", "C003": "家珍"}],
        )


def test_pronoun_before_same_caption_name_cannot_look_forward() -> None:
    """A later name cannot resolve an earlier pronoun without upstream evidence."""
    with pytest.raises(CaptionContractError, match="unresolved pronoun"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="她走进来，家珍站在门口。")],
            beats=[_beat(cue="她走进来，家珍站在门口。", required_entities=["C003"])],
            captions=[{"caption_id": "c1", "text": "她走进来，家珍站在门口。"}],
            entity_name_maps=[{"C003": "家珍"}],
        )


def test_plural_pronoun_resolves_explicit_local_villagers_group() -> None:
    """他们 binds to one upstream caption-local group, never 凤霞 alone."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="村里人后来说，凤霞出嫁。是他们见过最气派的。")],
        beats=[
            _beat(cue="村里人后来说，凤霞出嫁。", required_entities=["C002", "C003", "C004", "C008"]),
            {**_beat(cue="是他们见过最气派的。", required_entities=["C002", "C003", "C004", "C008"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "村里人后来说，凤霞出嫁。"},
            {"caption_id": "c2", "text": "是他们见过最气派的。"},
        ],
        entity_name_maps=[{"C002": "福贵", "C003": "家珍", "C004": "凤霞", "C008": "二喜"}],
    )

    group = next(item for item in contracts[0].to_dict()["must_show"] if item["entity_id"] == "GROUP_VILLAGERS")
    assert group["evidence"] == {
        "caption_local": True,
        "caption_span": {"start": 0, "end": 3, "text": "村里人"},
        "entity_kind": "character_group",
        "group": "villagers",
        "number": "plural",
        "text_evidence": "村里人",
    }
    resolution = contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"]
    assert resolution == [
        {
            "resolved_entity_id": "GROUP_VILLAGERS",
            "pronoun_resolution": "他们",
            "upstream_caption_id": "c1",
                "upstream_caption_text": "村里人后来说，凤霞出嫁。",
                "upstream_caption_span": {"start": 0, "end": 3, "text": "村里人"},
                "antecedent_clause_text": "村里人后来说",
                "antecedent_clause_span": {"start": 0, "end": 6, "text": "村里人后来说"},
                "resolution_basis": {
                    "kind": "upstream_caption_fallback_due_no_compatible_current_participant",
                    "current_beat_id": "B2",
                    "section_id": "S1",
                    "rejected_current_candidates": [],
                },
            "text_evidence": "他们",
        }
    ]


def test_plural_pronoun_cannot_make_a_group_from_two_named_people() -> None:
    """Two individual current entities are not an explicitly evidenced group."""
    with pytest.raises(CaptionContractError, match="unresolved pronoun"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵和家珍站在村口。他们转身离开。")],
            beats=[
                _beat(cue="福贵和家珍站在村口。", required_entities=["C001", "C003"]),
                {**_beat(cue="他们转身离开。", required_entities=["C001", "C003"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "福贵和家珍站在村口。"},
                {"caption_id": "c2", "text": "他们转身离开。"},
            ],
            entity_name_maps=[{"C001": "福贵", "C003": "家珍"}],
        )


def test_pronoun_ignores_recent_ox_outside_current_book_context() -> None:
    """读完它 resolves the current book participant, never the recently named ox."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="theory", text="这本书写了什么？一头老牛走来。读完它的人很少说它绝望。")],
        beats=[
            _beat(cue="这本书写了什么？", required_entities=["OBJ_BOOK", "OBJ_LAMP"]),
            {**_beat(cue="一头老牛走来。", required_entities=["OBJ_OX"]), "beatId": "B2"},
            {**_beat(cue="读完它的人很少说它绝望。", required_entities=["OBJ_BOOK", "OBJ_LAMP"]), "beatId": "B3"},
        ],
        captions=[
            {"caption_id": "c1", "text": "这本书写了什么？"},
            {"caption_id": "c2", "text": "一头老牛走来。"},
            {"caption_id": "c3", "text": "读完它的人很少说它绝望。"},
        ],
        entity_name_maps=[{"OBJ_BOOK": "书", "OBJ_LAMP": "煤油灯", "OBJ_OX": "老牛"}],
    )

    entries = [item for item in contracts[2].to_dict()["must_show"] if item["reason"] == "pronoun_resolution"]
    assert {item["entity_id"] for item in entries} == {"OBJ_BOOK"}
    assert entries[0]["evidence"]["upstream_caption_id"] == "c1"
    assert entries[0]["evidence"]["upstream_caption_text"] == "这本书写了什么？"
    assert entries[0]["evidence"]["upstream_caption_span"] == {"start": 2, "end": 3, "text": "书"}
    assert entries[0]["evidence"]["resolution_basis"] == {
        "kind": "current_action_required_participant",
        "source_beat_id": "B3",
        "candidate_entity_ids": ["OBJ_BOOK", "OBJ_LAMP"],
    }


def test_pronoun_without_a_prior_candidate_fails_closed() -> None:
    """An unsupported pronoun cannot silently pass as an abstract caption."""
    with pytest.raises(CaptionContractError, match="unresolved pronoun"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="theory", text="一阵风吹过。它停了。")],
            beats=[
                _beat(cue="一阵风吹过。", required_entities=[]),
                {**_beat(cue="它停了。", required_entities=["OBJ_BOOK"]), "beatId": "B2"},
            ],
            entity_name_maps=[{"OBJ_BOOK": "书"}],
            captions=[
                {"caption_id": "c1", "text": "一阵风吹过。"},
                {"caption_id": "c2", "text": "它停了。"},
            ],
        )


def test_pronoun_it_resolves_a_unique_explicit_object() -> None:
    """它 may resolve only to one earlier caption-named object anchor."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="theory", text="余华的《活着》。它写尽了苦难。")],
        beats=[
            _beat(cue="余华的《活着》。", required_entities=[]),
            {**_beat(cue="它写尽了苦难。", required_entities=["OBJ_BOOK"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "余华的《活着》。"},
            {"caption_id": "c2", "text": "它写尽了苦难。"},
        ],
        entity_name_maps=[{"OBJ_BOOK": "《活着》书与旧照片"}],
    )

    entry = contracts[1].to_dict()["must_show"][0]
    assert entry["entity_id"] == "OBJ_BOOK"
    assert entry["reason"] == "pronoun_resolution"
    assert entry["evidence"]["upstream_caption_id"] == "c1"


def test_pronoun_rejects_trusted_entity_type_mismatch() -> None:
    """Trusted metadata may reject 他 when its only antecedent is not a person."""
    with pytest.raises(CaptionContractError, match="no same-section upstream evidence"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵走到门口。他低头不语。")],
            beats=[
                _beat(cue="福贵走到门口。", required_entities=["C002"]),
                {**_beat(cue="他低头不语。", required_entities=["C002"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "福贵走到门口。"},
                {"caption_id": "c2", "text": "他低头不语。"},
            ],
            entity_name_maps=[{"C002": "福贵"}],
            entity_metadata={"C002": {"entity_type": "animal"}},
        )


def test_pronoun_with_only_mismatched_gender_fails_without_same_section_evidence() -> None:
    """A rejected current participant may not be guessed without same-section evidence."""
    with pytest.raises(CaptionContractError, match="no same-section upstream evidence"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵走到门口。他低头不语。")],
            beats=[
                _beat(cue="福贵走到门口。", required_entities=["C002"]),
                {**_beat(cue="他低头不语。", required_entities=["C002"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "福贵走到门口。"},
                {"caption_id": "c2", "text": "他低头不语。"},
            ],
            entity_name_maps=[{"C002": "福贵"}],
            entity_metadata={"C002": {"entity_type": "person", "gender": "female"}},
        )


def test_zero_compatible_current_beat_falls_back_to_unique_same_section_evidence() -> None:
    """A male-only beat may use the nearest same-section female caption evidence."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="家珍来到街上。她跪在福贵面前。")],
        beats=[
            _beat(cue="家珍来到街上。", required_entities=["C003"]),
            {**_beat(cue="她跪在福贵面前。", required_entities=["C001"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "家珍来到街上。"},
            {"caption_id": "c2", "text": "她跪在福贵面前。"},
        ],
        entity_name_maps=[{"C001": "福贵", "C003": "家珍"}],
        entity_metadata={
            "C001": {"entity_type": "person", "gender": "male", "gender_evidence": {"anchor_id": "CHAR_C001"}},
            "C003": {"entity_type": "person", "gender": "female", "gender_evidence": {"anchor_id": "CHAR_C004"}},
        },
    )

    resolution = contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"][0]
    assert resolution["resolved_entity_id"] == "C003"
    assert resolution["upstream_caption_id"] == "c1"
    assert resolution["resolution_basis"] == {
        "kind": "upstream_caption_fallback_due_no_compatible_current_participant",
        "current_beat_id": "B2",
        "section_id": "S1",
        "rejected_current_candidates": [
            {
                "entity_id": "C001",
                "gender": "male",
                "gender_evidence": {"anchor_id": "CHAR_C001"},
            }
        ],
        "upstream_candidate_profile_evidence": [
            {
                "entity_id": "C003",
                "gender": "female",
                "gender_evidence": {"anchor_id": "CHAR_C004"},
            }
        ],
    }


def test_compatible_current_beat_candidates_do_not_fall_back_to_section_evidence() -> None:
    """An ambiguous compatible Beat set cannot be hidden by a unique earlier woman."""
    with pytest.raises(CaptionContractError, match="no current-context upstream evidence"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="凤霞来到街上。她跪在门口。")],
            beats=[
                _beat(cue="凤霞来到街上。", required_entities=["C004"]),
                {**_beat(cue="她跪在门口。", required_entities=["C001", "C003"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "凤霞来到街上。"},
                {"caption_id": "c2", "text": "她跪在门口。"},
            ],
            entity_name_maps=[{"C001": "福贵", "C003": "家珍", "C004": "凤霞"}],
        )


def test_plural_pronoun_resolves_explicit_same_section_doctor_role_group() -> None:
    """Repeated hospital doctor mentions support one non-persistent role group."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(
            narrative_function="plot",
            text="产房里的医生出来问。医生，救救凤霞。医生后来出来说。我要大的，他们给了我小的。",
        )],
        beats=[
            _beat(cue="产房里的医生出来问。", required_entities=["SCENE_HOSPITAL"]),
            {**_beat(cue="医生，救救凤霞。", required_entities=["SCENE_HOSPITAL"]), "beatId": "B2"},
            {**_beat(cue="医生后来出来说。", required_entities=["SCENE_HOSPITAL"]), "beatId": "B3"},
            {**_beat(cue="我要大的，他们给了我小的。", required_entities=["SCENE_HOSPITAL"]), "beatId": "B4"},
        ],
        captions=[
            {"caption_id": "c1", "text": "产房里的医生出来问。"},
            {"caption_id": "c2", "text": "医生，救救凤霞。"},
            {"caption_id": "c3", "text": "医生后来出来说。"},
            {"caption_id": "c4", "text": "我要大的，他们给了我小的。"},
        ],
    )

    resolution = contracts[3].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"][0]
    assert resolution["resolved_entity_id"] == "GROUP_DOCTORS"
    assert resolution["resolution_basis"]["kind"] == "plural_role_group_from_explicit_upstream_roles"
    assert resolution["resolution_basis"]["scene_id"] == "SCENE_HOSPITAL"
    assert [item["caption_id"] for item in resolution["resolution_basis"]["supporting_upstream_captions"]] == [
        "c1", "c2", "c3"
    ]


def test_plural_role_groups_with_two_compatible_categories_fail_closed() -> None:
    """Hospital doctors and nurses cannot be collapsed into one pronoun group."""
    with pytest.raises(CaptionContractError, match="ambiguous plural role groups"):
        enrich_captions_to_contracts(
            script_sections=[_section(
                narrative_function="plot",
                text="医生进来。护士进来。他们都沉默。",
            )],
            beats=[
                _beat(cue="医生进来。", required_entities=["SCENE_HOSPITAL"]),
                {**_beat(cue="护士进来。", required_entities=["SCENE_HOSPITAL"]), "beatId": "B2"},
                {**_beat(cue="他们都沉默。", required_entities=["SCENE_HOSPITAL"]), "beatId": "B3"},
            ],
            captions=[
                {"caption_id": "c1", "text": "医生进来。"},
                {"caption_id": "c2", "text": "护士进来。"},
                {"caption_id": "c3", "text": "他们都沉默。"},
            ],
        )


def test_profile_marker_excludes_explicitly_female_candidate_for_him() -> None:
    """A locked 女子 marker excludes C004 while unknown C008 remains auditable."""
    metadata = _build_project_entity_metadata(
        {
            "character_anchors": [
                {"character_id": "C004", "anchor_id": "CHAR_C005", "prompt_subject": "安静的女子"},
                {"character_id": "C008", "anchor_id": "CHAR_C007", "prompt_subject": "朴素搬运工"},
            ]
        },
        profile_sha256="a" * 64,
    )
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="凤霞和二喜回家。他说话了。")],
        beats=[
            _beat(cue="凤霞和二喜回家。", required_entities=["C004", "C008"]),
            {**_beat(cue="他说话了。", required_entities=["C004", "C008"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "凤霞和二喜回家。"},
            {"caption_id": "c2", "text": "他说话了。"},
        ],
        entity_name_maps=[{"C004": "凤霞", "C008": "二喜"}],
        entity_metadata=metadata,
    )

    resolution = contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"][0]
    assert resolution["resolved_entity_id"] == "C008"
    assert resolution["resolution_basis"]["gender_exclusions"] == [
        {
            "entity_id": "C004",
            "gender": "female",
            "gender_evidence": {
                "anchor_id": "CHAR_C005",
                "field": "prompt_subject",
                "marker": "女子",
                "profile_sha256": "a" * 64,
                "span": {"start": 3, "end": 5, "text": "女子"},
            },
        }
    ]


def test_explicit_gender_match_precedes_unknown_pronoun_candidate() -> None:
    """A profile-backed female match wins over an otherwise eligible unknown."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="家珍和苦根进屋。她说：他坐下了。")],
        beats=[
            _beat(cue="家珍和苦根进屋。", required_entities=["C003", "C009"]),
            {**_beat(cue="她说：他坐下了。", required_entities=["C003", "C009"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "家珍和苦根进屋。"},
            {"caption_id": "c2", "text": "她说：他坐下了。"},
        ],
        entity_name_maps=[{"C003": "家珍", "C009": "苦根"}],
        entity_metadata={
            "C003": {"entity_type": "person", "gender": "female", "gender_evidence": {"anchor_id": "CHAR_C003"}},
        },
    )

    resolutions = contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"]
    assert [entry["resolved_entity_id"] for entry in resolutions] == ["C003", "C009"]
    assert resolutions[0]["resolution_basis"]["unknown_candidates_excluded_by_explicit_match"] == ["C009"]
    assert resolutions[1]["resolution_basis"]["gender_exclusions"][0]["entity_id"] == "C003"


def test_multiple_explicit_gender_matches_remain_ambiguous() -> None:
    """Two profile-backed female candidates may not be broken by input order."""
    with pytest.raises(CaptionContractError, match="ambiguous pronoun"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="家珍和凤霞看着苦根。她转身离开。")],
            beats=[
                _beat(cue="家珍和凤霞看着苦根。", required_entities=["C003", "C004", "C009"]),
                {**_beat(cue="她转身离开。", required_entities=["C003", "C004", "C009"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "家珍和凤霞看着苦根。"},
                {"caption_id": "c2", "text": "她转身离开。"},
            ],
            entity_name_maps=[{"C003": "家珍", "C004": "凤霞", "C009": "苦根"}],
            entity_metadata={
                "C003": {"entity_type": "person", "gender": "female"},
                "C004": {"entity_type": "person", "gender": "female"},
            },
        )


def test_unknown_gender_candidate_uses_existing_unique_evidence_when_no_match_exists() -> None:
    """Unknown gender is not assumed, but one unique candidate remains resolvable."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="苦根进屋。他坐下了。")],
        beats=[
            _beat(cue="苦根进屋。", required_entities=["C009"]),
            {**_beat(cue="他坐下了。", required_entities=["C009"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "苦根进屋。"},
            {"caption_id": "c2", "text": "他坐下了。"},
        ],
        entity_name_maps=[{"C009": "苦根"}],
    )

    assert contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"][0]["resolved_entity_id"] == "C009"


def test_upstream_pronoun_resolution_prefers_the_latest_compatible_clause() -> None:
    """The final clause naming 家珍 outranks an earlier 凤霞 clause in one caption."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="凤霞死后不到三个月，家珍也死了。她跟福贵说。")],
        beats=[
            _beat(cue="凤霞死后不到三个月，家珍也死了。", required_entities=["C003", "C004"]),
            {**_beat(cue="她跟福贵说。", required_entities=["C003", "C004"]), "beatId": "B2"},
        ],
        captions=[
            {"caption_id": "c1", "text": "凤霞死后不到三个月，家珍也死了。"},
            {"caption_id": "c2", "text": "她跟福贵说。"},
        ],
        entity_name_maps=[{"C003": "家珍", "C004": "凤霞"}],
        entity_metadata={
            "C003": {"entity_type": "person", "gender": "female"},
            "C004": {"entity_type": "person", "gender": "female"},
        },
    )

    evidence = contracts[1].to_dict()["scene_state"]["continuity_state"]["pronoun_resolutions"][0]
    assert evidence["resolved_entity_id"] == "C003"
    assert evidence["antecedent_clause_text"] == "家珍也死了"
    assert evidence["antecedent_clause_span"] == {"start": 10, "end": 15, "text": "家珍也死了"}


def test_same_clause_coordinated_candidates_remain_ambiguous() -> None:
    """A coordinated name pair in one selected clause has no order-based winner."""
    with pytest.raises(CaptionContractError, match="ambiguous pronoun"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="plot", text="福贵和家珍站在村口。她转身离开。")],
            beats=[
                _beat(cue="福贵和家珍站在村口。", required_entities=["C001", "C003"]),
                {**_beat(cue="她转身离开。", required_entities=["C001", "C003"]), "beatId": "B2"},
            ],
            captions=[
                {"caption_id": "c1", "text": "福贵和家珍站在村口。"},
                {"caption_id": "c2", "text": "她转身离开。"},
            ],
            entity_name_maps=[{"C001": "福贵", "C003": "家珍"}],
        )


def test_unknown_profile_gender_is_not_assumed() -> None:
    """A marker-free anchor stays unknown rather than being treated as male."""
    metadata = _build_project_entity_metadata(
        {"character_anchors": [{"character_id": "C008", "anchor_id": "CHAR_C007", "prompt_subject": "朴素搬运工"}]},
        profile_sha256="b" * 64,
    )
    assert "gender" not in metadata.get("C008", {})


def test_contradictory_profile_gender_markers_fail_closed() -> None:
    """One anchor cannot carry incompatible lexical gender evidence."""
    with pytest.raises(CaptionContractError, match="contradictory gender markers"):
        _build_project_entity_metadata(
            {"character_anchors": [{"character_id": "C004", "anchor_id": "CHAR_C005", "prompt_subject": "女子与少年"}]},
            profile_sha256="c" * 64,
        )


def test_validate_only_cli_reads_metadata_without_writing_contract(tmp_path: Path) -> None:
    """Validation uses only locked metadata and leaves Phase 4 artifacts unchanged."""
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    (project / "04_audio").mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({"performance_version": {"sections": [_section(narrative_function="plot", text="福贵走过田埂。")]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([_beat(cue="福贵走过田埂。", required_entities=["C002"] )], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "04_audio" / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {"c1": {"caption_id": "c1", "text": "福贵走过田埂。"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "book_video_factory/scripts/build_caption_visual_contract.py",
            "--project",
            str(project),
            "--release-id",
            "r1",
            "--validate-only",
        ],
        cwd=Path(__file__).parents[2],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"caption_count": 1, "release_id": "r1", "status": "valid"}
    assert not (project / "04_audio" / "CAPTION_VISUAL_CONTRACT.json").exists()
