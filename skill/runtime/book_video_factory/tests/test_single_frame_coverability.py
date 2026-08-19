"""Single-frame coverability regression tests (Semantic Shot Group review round).

The v3 manual review (REJECT) demanded: a group is valid only if ONE normal
single-time/single-space/single-camera frame covers every caption without
contradiction or spoiler. These tests lock down the fixes:
1. death templates are cause-specific and reference mentions are not corpses;
2. pronouns never resolve to scene entities;
3. 老头+老牛 are both primary participants;
4. mutually incompatible visual states split (wedding vs pregnant, alive vs
   dead, pre-donation vs collapse, medical vs conscription vs battlefield,
   theory memory vignettes);
5. Group participant constraints are recomputed from the final Group focus.
"""

from __future__ import annotations

import hashlib

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionEntityEvidence,
    CaptionVisualContract,
    VisualEventState,
    _build_project_entity_metadata,
    _pronoun_candidates,
    enrich_captions_to_contracts,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    derive_caption_image_groups,
)
from book_video_factory.semantic_alignment.group_contract import (
    aggregate_group_visual_contract,
)


def _caption(caption_id: str, start: float, end: float, text: str = "句。") -> dict[str, object]:
    return {"id": caption_id, "start": start, "end": end, "text": text}


def _entity(entity_id: str, natural_language: str) -> CaptionEntityEvidence:
    return CaptionEntityEvidence(
        entity_id=entity_id,
        natural_language=natural_language,
        reason="caption_text",
        evidence={"caption": entity_id},
    )


def _contract(
    caption_id: str,
    text: str,
    *,
    narrative_function: str = "plot",
    visual_mode: str = "literal",
    visual_state: str = "generic_scene",
    section_id: str = "S001",
    location_id: str = "SCENE_HOME",
    hard_split_event: str = "none",
    event_instance_id: str = "sequence:B1",
    must_show: tuple[CaptionEntityEvidence, ...] = (),
) -> CaptionVisualContract:
    return CaptionVisualContract(
        caption_id=caption_id,
        caption_text=text,
        caption_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        section_id=section_id,
        source_beat_ids=("B1",),
        narrative_function=narrative_function,
        visual_mode=visual_mode,
        visual_state=visual_state,
        scene_state={
            "visible_character_ids": [
                item.entity_id for item in must_show if item.entity_id.startswith("C")
            ],
            "location_id": location_id,
            "time_context": "same day",
            "action_state": text,
            "continuity_state": {"pronoun_resolutions": []},
            "action_semantics": {
                "action_key": "same_scene_sequence",
                "incompatible_action_keys": [],
                "hard_split_event": hard_split_event,
                "event_instance_id": event_instance_id,
                "source_evidence": {"beat": {"beat_id": "B1"}, "caption": {"caption_id": caption_id}},
            },
        },
        must_show=must_show,
        may_show=(),
        must_not_show_as_primary=(),
        visual_event_state=VisualEventState(
            action_predicate="caption_scene_state",
            required_observable_evidence=(),
        ),
    )


def _enrich(text: str, *, section_nf: str = "plot", name_maps: list[dict[str, str]] | None = None):
    return enrich_captions_to_contracts(
        script_sections=[{"section_id": "S1", "narrative_function": section_nf, "text": text}],
        beats=[{
            "beatId": "B1",
            "cue": text,
            "sectionId": "S1",
            "requiredEntities": [],
            "forbiddenEntities": [],
            "riskFlags": [],
            "highRisk": False,
            "generationMode": "single",
        }],
        captions=[{"caption_id": "c1", "text": text, "shot_ids": ["shot-1"]}],
        entity_name_maps=name_maps or [],
    )[0]


class TestDeathTemplateContamination:
    def test_reference_death_is_not_bean_aftermath(self) -> None:
        contract = _enrich("这本书把爹娘老婆儿子女儿一个一个写死了。", section_nf="opening")
        state = contract.visual_event_state
        assert state.action_predicate == "death_mention"
        assert state.is_reference_death is True
        assert all("豆子" not in item for item in state.required_observable_evidence)
        assert any("不出现尸体" in item or "不画尸体" in item for item in state.required_observable_evidence)

    def test_blood_loss_death_uses_medical_evidence_not_beans(self) -> None:
        contract = _enrich("有庆是抽血抽死的。")
        state = contract.visual_event_state
        assert state.action_predicate == "death_aftermath"
        assert state.cause_type == "blood_loss"
        assert any("抽血" in item or "针管" in item for item in state.required_observable_evidence)
        assert all("豆子" not in item for item in state.required_observable_evidence)

    def test_overeating_keeps_bean_evidence(self) -> None:
        contract = _enrich("苦根是吃豆子撑死的。")
        assert contract.visual_event_state.cause_type == "overeating_beans"
        assert any("豆子" in item for item in contract.visual_event_state.required_observable_evidence)


class TestOldManAndOx:
    def test_both_old_man_and_ox_are_primary(self) -> None:
        contract = _enrich(
            "到最后，只剩一个老头，和一头老牛。",
            name_maps=[{"C002": "福贵（中年/老年）", "C010": "老牛（福贵）"}],
        )
        ids = {item.entity_id for item in contract.must_show}
        assert "C002" in ids
        assert "C010" in ids


class TestPronounTypeSafety:
    def test_ta_never_resolves_to_scene(self) -> None:
        candidates = _pronoun_candidates("它", [("SCENE_FIELD", "scene", "")], {})
        assert candidates == []
        candidates = _pronoun_candidates(
            "它",
            [("C010", "char", "")],
            {"C010": {"entity_type": "animal"}},
        )
        assert candidates != []

    def test_ox_anchor_is_inferred_as_animal(self) -> None:
        metadata = _build_project_entity_metadata(
            {
                "character_anchors": [{
                    "character_id": "C010",
                    "anchor_id": "CHAR_C010",
                    "prompt_subject": "瘦骨嶙峋的老年水牛",
                    "name": "老牛（福贵）",
                }],
                "object_anchors": [],
                "scene_anchors": [],
            },
            profile_sha256="a" * 64,
        )
        assert metadata["C010"]["entity_type"] == "animal"


class TestStateCompatibilitySplits:
    def test_wedding_splits_from_pregnancy(self) -> None:
        first = _contract("c1", "凤霞出嫁", visual_state="wedding", hard_split_event="high_risk_action", event_instance_id="beat:B1")
        second = _contract("c2", "凤霞怀孕了", visual_state="pregnancy_birth", hard_split_event="high_risk_action", event_instance_id="beat:B2")
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "凤霞出嫁"), _caption("c2", 3.0, 6.0, "凤霞怀孕了")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 2

    def test_alive_speaking_splits_from_death_aftermath(self) -> None:
        first = _contract("c1", "你怎么不吃啊", visual_state="alive_active")
        second = _contract("c2", "傍晚回来，苦根歪在床上", visual_state="collapse")
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "你怎么不吃啊"), _caption("c2", 3.0, 6.0, "傍晚回来，苦根歪在床上")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 2

    def test_return_to_death_aftermath_may_share_one_frame(self) -> None:
        first = _contract("c1", "傍晚回来", visual_state="return_home")
        second = _contract("c2", "苦根歪在床上", visual_state="collapse")
        third = _contract("c3", "撑死的", visual_state="death_aftermath")
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "傍晚回来"), _caption("c2", 3.0, 6.0, "苦根歪在床上"), _caption("c3", 6.0, 9.0, "撑死的")],
            {first.caption_id: first, second.caption_id: second, third.caption_id: third},
        )
        assert len(groups) == 1

    def test_medical_conscription_battlefield_split(self) -> None:
        states = [
            ("c1", "他进城去请郎中", "medical_visit"),
            ("c2", "他被抓了壮丁", "conscription"),
            ("c3", "战场上认识了老全", "battlefield"),
        ]
        contracts = {
            cid: _contract(cid, text, visual_state=state)
            for cid, text, state in states
        }
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "他进城去请郎中"), _caption("c2", 3.0, 6.0, "他被抓了壮丁"), _caption("c3", 6.0, 9.0, "战场上认识了老全")],
            contracts,
        )
        assert len(groups) == 3

    def test_theory_memory_vignettes_split_on_disjoint_cast(self) -> None:
        first = _contract(
            "c1", "有庆光着脚跑在雪地里", visual_state="alive_active",
            narrative_function="theory", must_show=(_entity("C005", "有庆"),),
        )
        second = _contract(
            "c2", "凤霞出嫁那天", visual_state="wedding",
            narrative_function="theory", must_show=(_entity("C004", "凤霞"),),
        )
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "有庆光着脚跑在雪地里"), _caption("c2", 3.0, 6.0, "凤霞出嫁那天")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 2

    def test_blood_loss_splits_before_collapse(self) -> None:
        """G068 review: conscious blood-draw must not share a frame with collapse/death."""
        states = [
            ("c1", "抽着抽着有庆的脸白了", "blood_loss"),
            ("c2", "脑袋一歪摔在地上", "collapse"),
            ("c3", "心跳都没了", "death_aftermath"),
        ]
        contracts = {cid: _contract(cid, text, visual_state=state) for cid, text, state in states}
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "抽着抽着有庆的脸白了"), _caption("c2", 3.0, 6.0, "脑袋一歪摔在地上"), _caption("c3", 6.0, 9.0, "心跳都没了")],
            contracts,
        )
        assert len(groups) == 2
        assert groups[0].caption_ids == ("c1",)
        assert groups[1].caption_ids == ("c2", "c3")


class TestGroupContractConsistency:
    def test_author_group_participant_constraint_is_recomputed(self) -> None:
        member = _contract(
            "c1", "余华说他听了一首美国民歌",
            narrative_function="author_background",
            visual_mode="symbolic_or_abstract",
            visual_state="author_hold",
            must_show=(_entity("C002", "福贵（中年/老年）"),),
        )
        group = {
            "group_id": "G001",
            "caption_ids": ["c1"],
            "start": 0.0,
            "end": 3.0,
            "narrative_function": "author_background",
            "contract_bindings": [{"caption_id": "c1", "content_sha256": member.content_sha256()}],
            "caption_group_sha256": "a" * 64,
        }
        record = aggregate_group_visual_contract(
            group=group,
            contracts={"c1": member},
            captions={"c1": {"text": "余华说他听了一首美国民歌"}},
        )
        assert record["primary_subjects"] == []
        assert record["expected_character_count"] == 0
        assert record["allow_unlisted_narrative_characters"] is True
        assert all(item["entity_id"] != "C002" for item in record["must_show"])


class TestDiscourseIntegrity:
    def test_unfinished_quote_continuation_never_splits(self) -> None:
        """G179/G180 review: '苦根说，镰刀越快，' + '我力气也就越大啦。' stay together."""
        first = _contract(
            "c1", "苦根说，镰刀越快，", visual_state="alive_active",
            narrative_function="theory", must_show=(_entity("C009", "苦根"),),
        )
        second = _contract(
            "c2", "我力气也就越大啦。", visual_state="theory_hold",
            narrative_function="theory", visual_mode="symbolic_or_abstract",
        )
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "苦根说，镰刀越快，"), _caption("c2", 3.0, 6.0, "我力气也就越大啦。")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 1
        assert groups[0].caption_ids == ("c1", "c2")

    def test_colon_quote_never_splits(self) -> None:
        """G067/G068 review: '跑到门口喊：' + '要抽我的血啦。' stay together."""
        first = _contract("c1", "跑到门口喊：", visual_state="donation_match")
        second = _contract("c2", "要抽我的血啦。", visual_state="donation_match")
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "跑到门口喊："), _caption("c2", 3.0, 6.0, "要抽我的血啦。")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 1

    def test_yue_continuation_never_splits(self) -> None:
        first = _contract("c1", "镰刀越快，", visual_state="generic_scene", narrative_function="theory")
        second = _contract("c2", "我力气也就越大啦。", visual_state="theory_hold",
                           narrative_function="theory", visual_mode="symbolic_or_abstract")
        groups = derive_caption_image_groups(
            [_caption("c1", 0.0, 3.0, "镰刀越快，"), _caption("c2", 3.0, 6.0, "我力气也就越大啦。")],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 1
