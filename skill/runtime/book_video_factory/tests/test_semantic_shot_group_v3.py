"""Semantic Shot Group v3: merge until a material visual boundary, then split.

The final image unit is the Semantic Shot Group. This file locks down the
recalibrated grouping policy, the Group Visual Contract aggregation and the
deterministic Group Visual Focus summariser.
"""

from __future__ import annotations

import hashlib

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionEntityEvidence,
    CaptionVisualContract,
    VisualEventState,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    MAX_IMAGE_GROUP_DURATION,
    derive_caption_image_groups,
)
from book_video_factory.semantic_alignment.group_contract import (
    aggregate_group_visual_contract,
    build_group_visual_contract_document,
    summarize_group_visual_focus,
)


def _caption(caption_id: str, start: float, end: float) -> dict[str, object]:
    return {"id": caption_id, "start": start, "end": end}


def _entity(entity_id: str) -> CaptionEntityEvidence:
    return CaptionEntityEvidence(
        entity_id=entity_id,
        natural_language={"C003": "家珍", "C004": "凤霞", "C005": "有庆", "C010": "老牛（福贵）"}.get(
            entity_id, entity_id
        ),
        reason="caption_text",
        evidence={"caption": entity_id},
    )


def _contract(
    caption_id: str,
    text: str,
    *,
    narrative_function: str = "plot",
    visual_mode: str = "literal",
    location_id: str = "SCENE_WEDDING",
    time_context: str = "1950s wedding day",
    must_show: tuple[CaptionEntityEvidence, ...] = (),
    may_show: tuple[CaptionEntityEvidence, ...] = (),
    hard_split_event: str = "high_risk_action",
    event_instance_id: str = "beat:B001",
    location: str = "婚礼",
) -> CaptionVisualContract:
    return CaptionVisualContract(
        caption_id=caption_id,
        caption_text=text,
        caption_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        section_id="S001",
        source_beat_ids=("B001",),
        narrative_function=narrative_function,
        location=location,
        scene_state={
            "visible_character_ids": [
                item.entity_id for item in must_show if item.entity_id.startswith("C")
            ],
            "location_id": location_id,
            "time_context": time_context,
            "action_state": text,
            "continuity_state": {"pronoun_resolutions": []},
            "action_semantics": {
                "action_key": f"event:{hard_split_event}:{caption_id}",
                "incompatible_action_keys": [],
                "hard_split_event": hard_split_event,
                "event_instance_id": event_instance_id,
                "source_evidence": {"beat": {"beat_id": "B001"}, "caption": {"caption_id": caption_id}},
            },
        },
        must_show=must_show,
        may_show=may_show,
        must_not_show_as_primary=(),
        visual_mode=visual_mode,
        visual_event_state=VisualEventState(
            action_predicate="wedding",
            actors=tuple(item.natural_language for item in must_show),
            objects=(),
            subject_state="出嫁/婚礼",
            required_observable_evidence=("新娘与嫁衣/花轿清晰可见", "婚礼队列或村口喜事气氛"),
            forbidden_contradictory_state=(),
        ),
    )


class TestSemanticShotGroupPolicy:
    def test_same_wedding_event_merges_across_captions(self) -> None:
        """One wedding event phase -> one image, even with location-name drift."""
        first = _contract(
            "caption-0001",
            "锣鼓敲得震天响，",
            location_id="SCENE_VILLAGE",
            location="茅屋村口",
            must_show=(_entity("C004"),),
        )
        second = _contract(
            "caption-0002",
            "村里人后来说，凤霞出嫁，",
            location_id="SCENE_WEDDING",
            location="婚礼",
            must_show=(_entity("C004"), _entity("OBJ_BRIDAL")),
        )

        groups = derive_caption_image_groups(
            [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
            {first.caption_id: first, second.caption_id: second},
        )

        assert len(groups) == 1
        assert groups[0].caption_ids == ("caption-0001", "caption-0002")

    def test_group_duration_cap_is_thirty_seconds(self) -> None:
        assert MAX_IMAGE_GROUP_DURATION == 30.0
        first = _contract("caption-0001", "第一句。", hard_split_event="none", event_instance_id="sequence:B1")
        second = _contract("caption-0002", "第二句。", hard_split_event="none", event_instance_id="sequence:B2")
        groups = derive_caption_image_groups(
            [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 29.0, 33.0)],
            {first.caption_id: first, second.caption_id: second},
        )
        assert len(groups) == 2
        assert groups[1].split_from_previous["reasons"] == ["duration_limit"]


class TestGroupVisualContract:
    def test_aggregation_builds_the_group_record(self) -> None:
        first = _contract(
            "caption-0001",
            "家珍在门口等着他。",
            hard_split_event="none",
            event_instance_id="sequence:B1",
            location_id="SCENE_VILLAGE",
            location="茅屋村口",
            must_show=(_entity("C003"),),
            may_show=(_entity("C010"),),
        )
        group = {
            "group_id": "G001",
            "caption_ids": ["caption-0001"],
            "start": 0.0,
            "end": 3.0,
            "narrative_function": "plot",
            "contract_bindings": [{"caption_id": "caption-0001", "content_sha256": first.content_sha256()}],
            "caption_group_sha256": "a" * 64,
        }
        captions = {"caption-0001": {"text": "家珍在门口等着他。"}}
        record = aggregate_group_visual_contract(
            group=group,
            contracts={"caption-0001": first},
            captions=captions,
        )
        assert record["group_caption_text"] == "家珍在门口等着他。"
        assert record["primary_subjects"] == ["家珍"]
        assert record["expected_character_count"] == 1
        assert record["allow_unlisted_narrative_characters"] is False
        assert "C010" in record["supporting_subject_ids"]
        assert record["event_phase"]["hard_split_event"] == "none"

    def test_group_visual_focus_bean_example(self) -> None:
        focus = summarize_group_visual_focus(
            event_state={
                "action_predicate": "departure_absence",
                "subject_state": "人物已离开（缺席状态）",
                "required_observable_evidence": ["人物离开后的空间关系（背影远去，或门开向田野）"],
            },
            primary_subjects=[],
            location="床边",
            objects=["半锅豆子"],
        )
        assert "半锅豆子" in focus
        assert "人物已离开" in focus
        assert "走向田地" in focus

    def test_document_schema_version(self) -> None:
        first = _contract("caption-0001", "一句。", hard_split_event="none", event_instance_id="sequence:B1")
        document = build_group_visual_contract_document(
            release_id="r1",
            groups=[{
                "group_id": "G001",
                "caption_ids": ["caption-0001"],
                "start": 0.0,
                "end": 3.0,
                "narrative_function": "plot",
                "contract_bindings": [{"caption_id": "caption-0001", "content_sha256": first.content_sha256()}],
                "caption_group_sha256": "b" * 64,
            }],
            contracts={"caption-0001": first},
            captions={"caption-0001": {"text": "一句。"}},
        )
        assert document["schema_version"] == "group-visual-contract.v1"
        assert document["group_count"] == 1
