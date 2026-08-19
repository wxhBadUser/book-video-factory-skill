"""Part 3 - one image may not silently serve unrelated captions.

The production defect this file locks down: several consecutive display
captions were grouped onto a single shot even though the narration moved to a
different character, a different place, a different point in time, or a
different narrative register (plot -> theory -> author background -> closing).
The resulting image could only ever match one of them.

Every test here is deterministic: no network, no model, no randomness.
"""

from __future__ import annotations

import pytest

from book_video_factory.semantic_alignment.caption_grouping import (
    NARRATIVE_FUNCTIONS,
    SPLIT_REASONS,
    CaptionGroupingError,
    CaptionUnit,
    audit_shot_caption_groups,
    group_captions,
    required_split_reasons,
    validate_caption_groups,
)


def unit(
    caption_id: str,
    text: str,
    start: float,
    end: float,
    *,
    characters: tuple[str, ...] = (),
    location: str = "",
    time_of_day: str = "",
    narrative_function: str = "plot",
) -> CaptionUnit:
    return CaptionUnit(
        caption_id=caption_id,
        text=text,
        start=start,
        end=end,
        characters=characters,
        location=location,
        time_of_day=time_of_day,
        narrative_function=narrative_function,
    )


class TestSplitTriggers:
    def test_identical_context_needs_no_split(self) -> None:
        a = unit("c1", "福贵蹲在田埂上", 0.0, 3.0, characters=("福贵",), location="田埂", time_of_day="午后")
        b = unit("c2", "他把草帽压得很低", 3.0, 6.0, characters=("福贵",), location="田埂", time_of_day="午后")
        assert required_split_reasons(a, b) == ()

    def test_same_place_cast_growth_does_not_split(self) -> None:
        """Semantic Shot Group: family members arriving one by one stay in one image."""
        a = unit("c1", "家珍在门口等着他", 0.0, 3.0, characters=("家珍",), location="堂屋")
        b = unit("c2", "凤霞也跑了出来", 3.0, 6.0, characters=("凤霞",), location="堂屋")
        assert required_split_reasons(a, b) == ()

    def test_disjoint_cast_across_place_and_time_splits(self) -> None:
        a = unit("c1", "福贵在田埂上", 0.0, 3.0, characters=("福贵",), location="田埂", time_of_day="白天")
        b = unit("c2", "龙二在赌场里", 3.0, 6.0, characters=("龙二",), location="赌场", time_of_day="夜里")
        reasons = required_split_reasons(a, b)
        assert "primary_subject_change" in reasons
        assert "location_change" in reasons

    def test_location_change_forces_a_split(self) -> None:
        a = unit("c1", "福贵在赌场里", 0.0, 3.0, characters=("福贵",), location="赌场")
        b = unit("c2", "福贵回到家门口", 3.0, 6.0, characters=("福贵",), location="家门口")
        assert "location_change" in required_split_reasons(a, b)

    def test_time_change_forces_a_split(self) -> None:
        a = unit("c1", "白天他还在笑", 0.0, 3.0, characters=("福贵",), location="田埂", time_of_day="白天")
        b = unit("c2", "夜里他一个人回来", 3.0, 6.0, characters=("福贵",), location="田埂", time_of_day="夜里")
        assert "time_change" in required_split_reasons(a, b)

    @pytest.mark.parametrize(
        "left,right",
        [
            ("plot", "theory"),
            ("theory", "plot"),
            ("plot", "author_background"),
            ("author_background", "plot"),
            ("theory", "author_background"),
            ("plot", "closing"),
            ("theory", "closing"),
            ("author_background", "closing"),
        ],
    )
    def test_narrative_function_switch_forces_a_split(self, left: str, right: str) -> None:
        a = unit("c1", "左边这句", 0.0, 3.0, narrative_function=left)
        b = unit("c2", "右边这句", 3.0, 6.0, narrative_function=right)
        assert "narrative_function_change" in required_split_reasons(a, b)

    def test_author_background_may_never_share_a_shot_with_plot(self) -> None:
        """Adversarial case: 作者背景 caption illustrated by a 剧情 image."""
        units = [
            unit("c1", "福贵牵着老牛走过田埂", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit(
                "c2",
                "余华写这本书的时候只有三十岁",
                3.0,
                6.5,
                narrative_function="author_background",
            ),
        ]
        groups = group_captions(units)
        assert len(groups) == 2
        assert [g.caption_ids for g in groups] == [("c1",), ("c2",)]

    def test_every_declared_reason_is_a_known_reason(self) -> None:
        a = unit("c1", "甲", 0.0, 3.0, characters=("凤霞",), location="堂屋", time_of_day="白天")
        b = unit(
            "c2",
            "乙",
            3.0,
            6.0,
            characters=("有庆",),
            location="雪地",
            time_of_day="夜里",
            narrative_function="theory",
        )
        reasons = required_split_reasons(a, b)
        assert set(reasons) <= set(SPLIT_REASONS)
        assert set(reasons) >= {
            "location_change",
            "time_change",
            "narrative_function_change",
        }


class TestGrouping:
    def test_consecutive_compatible_captions_are_merged(self) -> None:
        units = [
            unit("c1", "福贵蹲在田埂上", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit("c2", "他把草帽压得很低", 3.0, 5.5, characters=("福贵",), location="田埂"),
        ]
        groups = group_captions(units)
        assert len(groups) == 1
        assert groups[0].caption_ids == ("c1", "c2")
        assert groups[0].start == pytest.approx(0.0)
        assert groups[0].end == pytest.approx(5.5)

    def test_grouping_is_deterministic(self) -> None:
        units = [
            unit("c1", "甲", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit("c2", "乙", 3.0, 6.0, characters=("凤霞",), location="田埂"),
            unit("c3", "丙", 6.0, 9.0, characters=("凤霞",), location="田埂"),
        ]
        first = [g.to_dict() for g in group_captions(units)]
        second = [g.to_dict() for g in group_captions(units)]
        assert first == second

    def test_duration_limit_splits_a_long_run(self) -> None:
        units = [
            unit(f"c{i}", f"第{i}句", float(i) * 4.0, float(i) * 4.0 + 4.0, characters=("福贵",), location="田埂")
            for i in range(6)
        ]
        groups = group_captions(units, max_group_duration=9.0)
        assert len(groups) > 1
        for group in groups:
            assert group.end - group.start <= 9.0 + 1e-6
        assert any("duration_limit" in group.split_reasons for group in groups[1:])

    def test_caption_count_never_forces_a_split(self) -> None:
        units = [
            unit(f"c{i}", f"第{i}句", float(i), float(i) + 1.0, characters=("福贵",), location="田埂")
            for i in range(7)
        ]
        groups = group_captions(units)
        assert [len(g.caption_ids) for g in groups] == [7]

    def test_group_ids_are_stable_and_ordered(self) -> None:
        units = [
            unit("c1", "甲", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit("c2", "乙", 3.0, 6.0, characters=("凤霞",), location="堂屋"),
        ]
        groups = group_captions(units)
        assert [g.group_id for g in groups] == ["G001", "G002"]

    def test_empty_input_is_a_hard_error(self) -> None:
        with pytest.raises(CaptionGroupingError):
            group_captions([])

    def test_unknown_narrative_function_is_a_hard_error(self) -> None:
        with pytest.raises(CaptionGroupingError):
            group_captions([unit("c1", "甲", 0.0, 3.0, narrative_function="vibes")])

    def test_out_of_order_captions_are_a_hard_error(self) -> None:
        units = [
            unit("c1", "甲", 3.0, 6.0),
            unit("c2", "乙", 0.0, 3.0),
        ]
        with pytest.raises(CaptionGroupingError):
            group_captions(units)

    def test_all_narrative_functions_are_accepted(self) -> None:
        for function in NARRATIVE_FUNCTIONS:
            groups = group_captions([unit("c1", "甲", 0.0, 3.0, narrative_function=function)])
            assert groups[0].narrative_function == function


class TestValidation:
    def test_validate_accepts_a_grouping_produced_by_the_grouper(self) -> None:
        units = [
            unit("c1", "甲", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit("c2", "乙", 3.0, 6.0, characters=("凤霞",), location="堂屋"),
        ]
        validate_caption_groups(group_captions(units), units)

    def test_validate_rejects_a_hand_merged_group_that_crosses_a_boundary(self) -> None:
        """The exact production defect: one image, two incompatible captions."""
        units = [
            unit("c1", "凤霞出嫁那天", 0.0, 3.0, characters=("凤霞",), location="堂屋", time_of_day="白天"),
            unit("c2", "雪地里孩子在跑", 3.0, 6.0, characters=("有庆",), location="雪地", time_of_day="冬夜"),
        ]
        merged = group_captions(units[:1])
        forged = (
            merged[0].__class__(
                group_id="G001",
                caption_ids=("c1", "c2"),
                start=0.0,
                end=6.0,
                narrative_function="plot",
                characters=("凤霞", "有庆"),
                location="堂屋",
                time_of_day="白天",
                split_reasons=(),
            ),
        )
        with pytest.raises(CaptionGroupingError) as excinfo:
            validate_caption_groups(forged, units)
        message = str(excinfo.value)
        assert "c2" in message
        assert "location_change" in message

    def test_validate_rejects_missing_captions(self) -> None:
        units = [
            unit("c1", "甲", 0.0, 3.0, characters=("福贵",)),
            unit("c2", "乙", 3.0, 6.0, characters=("福贵",)),
        ]
        groups = group_captions(units[:1])
        with pytest.raises(CaptionGroupingError):
            validate_caption_groups(groups, units)

    def test_validate_rejects_duplicate_captions(self) -> None:
        units = [unit("c1", "甲", 0.0, 3.0, characters=("福贵",))]
        groups = group_captions(units)
        with pytest.raises(CaptionGroupingError):
            validate_caption_groups(tuple(groups) + tuple(groups), units)


class TestAuditExistingStoryboards:
    def test_audit_flags_a_shot_that_spans_a_required_boundary(self) -> None:
        storyboard = [
            {"id": "SHOT-001", "captionIds": ["c1", "c2"]},
        ]
        units = [
            unit("c1", "凤霞出嫁那天", 0.0, 3.0, characters=("凤霞",), location="堂屋"),
            unit("c2", "雪地里孩子在跑", 3.0, 6.0, characters=("有庆",), location="雪地"),
        ]
        findings = audit_shot_caption_groups(storyboard, units)
        assert len(findings) == 1
        assert findings[0]["shot_id"] == "SHOT-001"
        assert "location_change" in findings[0]["reasons"]
        assert findings[0]["boundary_caption_id"] == "c2"

    def test_audit_is_silent_on_a_clean_storyboard(self) -> None:
        storyboard = [
            {"id": "SHOT-001", "captionIds": ["c1"]},
            {"id": "SHOT-002", "captionIds": ["c2"]},
        ]
        units = [
            unit("c1", "凤霞出嫁那天", 0.0, 3.0, characters=("凤霞",), location="堂屋"),
            unit("c2", "雪地里孩子在跑", 3.0, 6.0, characters=("有庆",), location="雪地"),
        ]
        assert audit_shot_caption_groups(storyboard, units) == []

    def test_audit_reports_every_boundary_inside_one_shot(self) -> None:
        storyboard = [{"id": "SHOT-001", "captionIds": ["c1", "c2", "c3"]}]
        units = [
            unit("c1", "剧情", 0.0, 3.0, characters=("福贵",), location="田埂"),
            unit("c2", "理论", 3.0, 6.0, narrative_function="theory"),
            unit("c3", "作者背景", 6.0, 9.0, narrative_function="author_background"),
        ]
        findings = audit_shot_caption_groups(storyboard, units)
        assert [f["boundary_caption_id"] for f in findings] == ["c2", "c3"]

    def test_audit_ignores_captions_absent_from_the_unit_table(self) -> None:
        storyboard = [{"id": "SHOT-001", "captionIds": ["c1", "unknown"]}]
        units = [unit("c1", "剧情", 0.0, 3.0, characters=("福贵",))]
        assert audit_shot_caption_groups(storyboard, units) == []
