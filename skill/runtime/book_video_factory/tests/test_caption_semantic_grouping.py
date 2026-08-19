"""Task 2: derive image groups from the authoritative caption contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionEntityEvidence,
    CaptionVisualContract,
    build_caption_visual_contract_document,
    build_caption_visual_contract_from_project,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    build_caption_grouping_audit_document,
    build_caption_grouping_from_project,
    derive_caption_image_groups,
)


def _caption(caption_id: str, start: float, end: float) -> dict[str, object]:
    return {"id": caption_id, "start": start, "end": end}


def _contract(
    caption_id: str,
    text: str,
    *,
    section_id: str = "S001",
    action_state: str = "福贵低头坐在田埂上",
    visible_character_ids: tuple[str, ...] = ("C001",),
    location_id: str = "SCENE_FIELD",
    time_context: str = "1950s spring afternoon",
    narrative_function: str = "plot",
    visual_mode: str = "literal",
    must_show: tuple[CaptionEntityEvidence, ...] = (),
    must_not_show_as_primary: tuple[CaptionEntityEvidence, ...] = (),
    continuity_state: dict[str, object] | None = None,
    source_beat_ids: tuple[str, ...] = ("B001",),
    action_key: str = "same_scene_sequence",
    incompatible_action_keys: tuple[str, ...] = (),
    hard_split_event: str = "none",
    event_instance_id: str = "sequence:B001",
) -> CaptionVisualContract:
    return CaptionVisualContract(
        caption_id=caption_id,
        caption_text=text,
        caption_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        section_id=section_id,
        source_beat_ids=source_beat_ids,
        narrative_function=narrative_function,
        scene_state={
            "visible_character_ids": list(visible_character_ids),
            "location_id": location_id,
            "time_context": time_context,
            "action_state": action_state,
            "continuity_state": continuity_state or {"wardrobe_state": "patched-blue-jacket"},
            "action_semantics": {
                "action_key": action_key,
                "incompatible_action_keys": list(incompatible_action_keys),
                "hard_split_event": hard_split_event,
                "event_instance_id": event_instance_id,
                "source_evidence": {
                    "beat": {"beat_id": "B001", "risk_flags": [], "high_risk": False, "generation_mode": "2x2"},
                    "caption": {"caption_id": caption_id, "shot_ids": [], "semantic_rationale": "test evidence"},
                },
            },
        },
        must_show=must_show,
        must_not_show_as_primary=must_not_show_as_primary,
        visual_mode=visual_mode,
    )


def _entity(entity_id: str) -> CaptionEntityEvidence:
    return CaptionEntityEvidence(
        entity_id=entity_id,
        natural_language=entity_id,
        reason="caption_text",
        evidence={"caption": entity_id},
    )


def test_same_scene_captions_with_different_durations_share_one_image() -> None:
    first = _contract("caption-0001", "福贵坐在田埂上。")
    second = _contract("caption-0002", "他把草帽压得更低。")

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 7.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.caption_ids == ("caption-0001", "caption-0002")
    assert group.start == 0.0
    assert group.end == 7.0
    assert group.duration == 7.0
    assert group.scene_state_signature
    assert [binding["caption_id"] for binding in group.contract_bindings] == [
        "caption-0001",
        "caption-0002",
    ]
    assert [binding["content_sha256"] for binding in group.contract_bindings] == [
        first.content_sha256(),
        second.content_sha256(),
    ]
    assert group.split_from_previous == {"required": False, "reasons": []}


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"location_id": "SCENE_HOME"}, "location_change"),
        ({"time_context": "1960s winter night"}, "time_change"),
        ({"narrative_function": "theory"}, "narrative_function_change"),
        ({"visual_mode": "symbolic_or_abstract"}, "visual_mode_change"),
        (
            {"hard_split_event": "death", "event_instance_id": "beat:B002"},
            "hard_split_event",
        ),
    ],
)
def test_scene_semantic_change_requires_a_new_image(
    changed: dict[str, object], reason: str
) -> None:
    first = _contract("caption-0001", "福贵坐在田埂上。")
    second = _contract("caption-0002", "下一句字幕。", **changed)

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert [group.caption_ids for group in groups] == [("caption-0001",), ("caption-0002",)]
    assert groups[1].split_from_previous == {"required": True, "reasons": [reason]}


def test_same_place_event_cast_growth_is_compatible() -> None:
    """Semantic Shot Group: family members arriving one by one share one image."""
    first = _contract("caption-0001", "家珍在门口等着他。", must_show=(_entity("C003"),))
    second = _contract("caption-0002", "凤霞也跑了出来。", must_show=(_entity("C004"),))
    third = _contract("caption-0003", "有庆站在娘身边。", must_show=(_entity("C005"),))

    groups = derive_caption_image_groups(
        [
            _caption("caption-0001", 0.0, 3.0),
            _caption("caption-0002", 3.0, 6.0),
            _caption("caption-0003", 6.0, 9.0),
        ],
        {first.caption_id: first, second.caption_id: second, third.caption_id: third},
    )

    assert len(groups) == 1
    assert groups[0].caption_ids == ("caption-0001", "caption-0002", "caption-0003")


def test_disjoint_cast_across_place_and_time_is_a_material_subject_change() -> None:
    first = _contract("caption-0001", "福贵在田埂上。", must_show=(_entity("C001"),))
    second = _contract(
        "caption-0002",
        "龙二在赌场里。",
        location_id="SCENE_GAMBLING",
        time_context="1950s winter night",
        must_show=(_entity("C006"),),
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert [group.caption_ids for group in groups] == [("caption-0001",), ("caption-0002",)]
    assert "primary_subject_change" in groups[1].split_from_previous["reasons"]


def test_must_show_and_primary_prohibition_conflict_requires_a_new_image() -> None:
    first = _contract("caption-0001", "福贵坐在田埂上。", must_show=(_entity("C001"),))
    second = _contract(
        "caption-0002",
        "下一句字幕。",
        must_not_show_as_primary=(_entity("C001"),),
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert [group.caption_ids for group in groups] == [("caption-0001",), ("caption-0002",)]
    assert groups[1].split_from_previous == {
        "required": True,
        "reasons": ["must_show_prohibition_conflict"],
    }


def test_same_wording_with_a_new_death_event_requires_a_split() -> None:
    first = _contract("caption-0001", "The wording is deliberately identical.")
    second = _contract(
        "caption-0002",
        "The wording is deliberately identical.",
        hard_split_event="death",
        event_instance_id="death:father",
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert groups[1].split_from_previous == {
        "required": True,
        "reasons": ["hard_split_event"],
    }


def test_identity_variant_or_wardrobe_change_requires_a_new_image() -> None:
    first = _contract("caption-0001", "福贵坐在田埂上。")
    second = _contract(
        "caption-0002",
        "下一句字幕。",
        continuity_state={"wardrobe_state": "wedding-red-jacket"},
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 3.0), _caption("caption-0002", 3.0, 6.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert groups[1].split_from_previous == {"required": True, "reasons": ["continuity_change"]}


def test_same_beat_different_action_texts_share_one_scene_image() -> None:
    first = _contract("caption-0001", "福贵低头坐在田埂上。", action_state="福贵低头。")
    second = _contract("caption-0002", "他把草帽压得更低。", action_state="他压低草帽。")

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 7.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert [group.caption_ids for group in groups] == [("caption-0001", "caption-0002")]


def test_missing_structured_action_semantics_fails_closed_for_arbitrary_english() -> None:
    first = _contract("caption-0001", "Arbitrary English wording.")
    second = replace(
        _contract("caption-0002", "Completely unrelated English wording."),
        scene_state={
            "visible_character_ids": ["C001"],
            "location_id": "SCENE_FIELD",
            "time_context": "1950s spring afternoon",
            "action_state": "anything at all",
            "continuity_state": {"wardrobe_state": "patched-blue-jacket"},
        },
    )

    with pytest.raises(CaptionGroupingError, match="action_semantics"):
        derive_caption_image_groups(
            [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 5.0)],
            {first.caption_id: first, second.caption_id: second},
        )


def test_explicit_incompatible_action_keys_split_without_text_inference() -> None:
    first = _contract("caption-0001", "No action words here.", action_key="standing")
    second = _contract(
        "caption-0002",
        "No action words here either.",
        action_key="seated",
        incompatible_action_keys=("standing",),
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 5.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert groups[1].split_from_previous == {
        "required": True,
        "reasons": ["declared_incompatible_action_key"],
    }


def test_multiple_captions_for_the_same_hard_event_instance_may_merge() -> None:
    first = _contract(
        "caption-0001", "First framing.", action_key="death_event", hard_split_event="death", event_instance_id="death:father"
    )
    second = _contract(
        "caption-0002", "Second framing.", action_key="death_event", hard_split_event="death", event_instance_id="death:father"
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 5.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert [group.caption_ids for group in groups] == [("caption-0001", "caption-0002")]


def test_different_event_phases_require_a_split() -> None:
    first = _contract(
        "caption-0001", "Same event class.", action_key="death_event", hard_split_event="death", event_instance_id="death:father"
    )
    second = _contract(
        "caption-0002", "Same event class.", section_id="S002",
        action_key="death_event", hard_split_event="death", event_instance_id="death:mother"
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 5.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert groups[1].split_from_previous == {"required": True, "reasons": ["event_instance_change"]}


@pytest.mark.parametrize(
    ("caption_text", "hard_split_event"),
    [
        ("gunshot/murdered", "death"),
        ("drowning", "high_risk_action"),
        ("suicide", "death"),
        ("birth", "birth"),
        ("hero/high-risk", "hero"),
    ],
)
def test_controlled_hard_events_split_through_structured_fields_not_caption_text(
    caption_text: str, hard_split_event: str
) -> None:
    first = _contract("caption-0001", caption_text)
    second = _contract(
        "caption-0002", caption_text,
        hard_split_event=hard_split_event,
        event_instance_id=f"{hard_split_event}:instance-1",
    )

    groups = derive_caption_image_groups(
        [_caption("caption-0001", 0.0, 2.5), _caption("caption-0002", 2.5, 5.0)],
        {first.caption_id: first, second.caption_id: second},
    )

    assert groups[1].split_from_previous == {"required": True, "reasons": ["hard_split_event"]}


def test_duration_limit_splits_same_scene_without_honoring_a_merge_hint() -> None:
    contracts = {
        "caption-0001": _contract("caption-0001", "第一句。"),
        "caption-0002": _contract("caption-0002", "第二句。"),
        "caption-0003": _contract("caption-0003", "第三句。"),
    }

    groups = derive_caption_image_groups(
        [
            _caption("caption-0001", 0.0, 11.0),
            _caption("caption-0002", 11.0, 22.0),
            {**_caption("caption-0003", 22.0, 33.0), "grouping_hint": "merge"},
        ],
        contracts,
    )

    assert [group.caption_ids for group in groups] == [
        ("caption-0001", "caption-0002"),
        ("caption-0003",),
    ]
    assert groups[0].duration == 22.0
    assert groups[1].duration == 11.0
    assert groups[1].scene_state_signature == groups[0].scene_state_signature
    assert groups[1].split_from_previous == {"required": True, "reasons": ["duration_limit"]}
    assert [caption_id for group in groups for caption_id in group.caption_ids] == [
        "caption-0001",
        "caption-0002",
        "caption-0003",
    ]


def test_boundary_audit_records_every_merge_or_required_split_from_contracts() -> None:
    first = _contract("caption-0001", "第一句。")
    second = _contract("caption-0002", "第二句。")
    third = _contract("caption-0003", "第三句。", location_id="SCENE_HOME")

    document = build_caption_grouping_audit_document(
        release_id="r1",
        captions=[
            {**_caption("caption-0001", 0.0, 2.0), "grouping_hint": "split"},
            {**_caption("caption-0002", 2.0, 5.0), "grouping_hint": "retain"},
            {**_caption("caption-0003", 5.0, 7.0), "grouping_hint": "merge"},
        ],
        contracts={first.caption_id: first, second.caption_id: second, third.caption_id: third},
    )

    assert document["schema_version"] == "caption-grouping-audit.v3"
    assert document["caption_count"] == 3
    assert document["group_count"] == 2
    assert document["boundary_count"] == 2
    assert document["merge_count"] == 1
    assert document["split_count"] == 1
    assert document["required_split_count"] == 1
    assert document["reason_distribution"] == {"location_change": 1}
    assert document["stats"]["average_captions_per_group"] == 1.5
    assert document["boundaries"] == [
        {
            "previous_caption_id": "caption-0001",
            "following_caption_id": "caption-0002",
            "decision": "merge",
            "required": False,
            "reasons": [],
        },
        {
            "previous_caption_id": "caption-0002",
            "following_caption_id": "caption-0003",
            "decision": "split",
            "required": True,
            "reasons": ["location_change"],
        },
    ]
    assert [group["caption_ids"] for group in document["groups"]] == [
        ["caption-0001", "caption-0002"],
        ["caption-0003"],
    ]


def test_project_grouping_can_validate_without_writing_then_emits_the_audit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    audio_dir = project / "04_audio"
    audio_dir.mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({"performance_version": {"sections": [{
            "section_id": "S1", "narrative_function": "plot", "text": "福贵走过田埂。"
        }]}} , ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([{
            "beatId": "B1", "sectionId": "S1", "cue": "福贵走过田埂。",
            "description": "福贵走过田埂。", "requiredEntities": ["C002"], "forbiddenEntities": [],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (audio_dir / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {
            "c1": {"caption_id": "c1", "text": "福贵走过田埂。", "start": 0.0, "end": 3.0}
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    build_caption_visual_contract_from_project(project, release_id="r1")

    audit_path = audio_dir / "CAPTION_GROUPING_AUDIT.json"
    preview = build_caption_grouping_from_project(project, validate_only=True)

    assert preview["caption_count"] == 1
    assert preview["boundary_count"] == 0
    assert not audit_path.exists()
    assert build_caption_grouping_from_project(project) == audit_path
    assert json.loads(audit_path.read_text(encoding="utf-8"))["groups"][0]["caption_ids"] == [
        "c1",
    ]


def test_grouping_cli_validate_only_reports_audit_counts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    audio_dir = project / "04_audio"
    audio_dir.mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({"performance_version": {"sections": [{
            "section_id": "S1", "narrative_function": "plot", "text": "福贵走过田埂。"
        }]}} , ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([{
            "beatId": "B1", "sectionId": "S1", "cue": "福贵走过田埂。",
            "description": "福贵走过田埂。", "requiredEntities": ["C002"], "forbiddenEntities": [],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (audio_dir / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {
            "c1": {"caption_id": "c1", "text": "福贵走过田埂。", "start": 0.0, "end": 3.0}
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    build_caption_visual_contract_from_project(project, release_id="r1")

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "book_video_factory" / "scripts" / "build_caption_grouping.py"),
            "--project",
            str(project),
            "--validate-only",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "valid",
        "release_id": "r1",
        "caption_count": 1,
        "boundary_count": 0,
        "required_split_count": 0,
    }
    assert not (audio_dir / "CAPTION_GROUPING_AUDIT.json").exists()


def test_validate_only_derives_v2_contract_in_memory_when_project_has_v1(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    audio_dir = project / "04_audio"
    audio_dir.mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps(
            {"performance_version": {"sections": [{
                "section_id": "S1", "narrative_function": "plot", "text": "福贵走过田埂。"
            }]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([{
            "beatId": "B1", "sectionId": "S1", "cue": "福贵走过田埂。",
            "description": "福贵走过田埂。", "requiredEntities": ["C002"], "forbiddenEntities": [],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (audio_dir / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {
            "c1": {"caption_id": "c1", "text": "福贵走过田埂。", "start": 0.0, "end": 3.0}
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    legacy = audio_dir / "CAPTION_VISUAL_CONTRACT.json"
    legacy.write_text('{"schema_version":"caption-visual-contract.v1"}\n', encoding="utf-8")
    legacy_bytes = legacy.read_bytes()

    document = build_caption_grouping_from_project(project, validate_only=True)

    assert document["caption_count"] == 1
    assert document["boundary_count"] == 0
    assert legacy.read_bytes() == legacy_bytes
    assert not (audio_dir / "CAPTION_GROUPING_AUDIT.json").exists()
    with pytest.raises(CaptionGroupingError, match="caption visual contract is unreadable"):
        build_caption_grouping_from_project(project)


def test_stale_persisted_v2_fails_closed_but_validate_only_uses_current_memory_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    audio_dir = project / "04_audio"
    audio_dir.mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps(
            {"performance_version": {"sections": [{
                "section_id": "S1", "narrative_function": "plot", "text": "福贵走过田埂。"
            }]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([{
            "beatId": "B1", "sectionId": "S1", "cue": "福贵走过田埂。",
            "description": "福贵走过田埂。", "requiredEntities": ["C002"], "forbiddenEntities": [],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (audio_dir / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {
            "c1": {"caption_id": "c1", "text": "福贵走过田埂。", "start": 0.0, "end": 3.0}
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    persisted = build_caption_visual_contract_from_project(project, release_id="r1")
    assert isinstance(persisted, Path)
    stale = json.loads(persisted.read_text(encoding="utf-8"))
    stale["contracts"]["c1"]["scene_state"]["action_state"] = "stale action state"
    persisted.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    stale_sha = CaptionVisualContract.from_mapping(stale["contracts"]["c1"]).content_sha256()
    stale_bytes = persisted.read_bytes()

    with pytest.raises(CaptionGroupingError, match="stale"):
        build_caption_grouping_from_project(project)
    preview = build_caption_grouping_from_project(project, validate_only=True)

    assert preview["groups"][0]["contract_bindings"][0]["content_sha256"] != stale_sha
    assert persisted.read_bytes() == stale_bytes
    assert not (audio_dir / "CAPTION_GROUPING_AUDIT.json").exists()


def test_write_grouping_fails_closed_when_persisted_v2_contract_is_absent(tmp_path: Path) -> None:
    audio_dir = tmp_path / "04_audio"
    audio_dir.mkdir()
    (audio_dir / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {
            "c1": {"caption_id": "c1", "text": "字幕。", "start": 0.0, "end": 3.0}
        }}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(CaptionGroupingError, match="caption visual contract is unreadable"):
        build_caption_grouping_from_project(tmp_path)
