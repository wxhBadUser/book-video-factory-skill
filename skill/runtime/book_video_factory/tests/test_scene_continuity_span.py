from __future__ import annotations

import hashlib

from book_video_factory.semantic_alignment.scene_continuity import (
    build_scene_continuity_document,
)


def contract(
    caption_id: str,
    text: str,
    *,
    people: tuple[str, ...],
    location: str,
    action: str,
    event_instance: str = "",
    time_context: str = "day",
    life_stage: str = "",
    story_objects: tuple[str, ...] = (),
    event_state: dict | None = None,
) -> dict:
    return {
        "caption_id": caption_id,
        "caption_text": text,
        "narrative_function": "plot",
        "subjects": list(people),
        "actions": [action],
        "location": location,
        "time_context": time_context,
        "story_objects": list(story_objects),
        "scene_state": {
            "visible_character_ids": list(people),
            "location_id": location,
            "time_context": time_context,
            "action_state": action,
            "continuity_state": {
                **({"scene_identity": event_instance} if event_instance else {}),
                **({"life_stage": life_stage} if life_stage else {}),
            },
            "action_semantics": {
                "action_key": action,
                "incompatible_action_keys": [],
                "hard_split_event": "none",
                "event_instance_id": event_instance,
                "source_evidence": text,
            },
        },
        "must_show": [],
        "may_show": [],
        "must_not_show_as_primary": [],
        "visual_focus": text,
        "visual_mode": "literal",
        "visual_state": "generic_scene",
        "presence_mode": "current",
        "semantic_signature": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "visual_event_state": event_state,
        "expected_visible_character_ids": list(people),
        "expected_narrative_character_count": len(people),
        "allow_unlisted_narrative_characters": False,
    }


def group(index: int, caption_id: str, start: float, end: float) -> dict:
    return {
        "group_id": f"G{index:03d}",
        "caption_ids": [caption_id],
        "start": start,
        "end": end,
        "duration": end - start,
        "narrative_function": "plot",
        "characters": [],
        "location": "",
        "time_of_day": "",
    }


def build(contracts: list[dict]) -> dict:
    captions = {
        item["caption_id"]: {
            "caption_id": item["caption_id"],
            "text": item["caption_text"],
            "start": float(index * 3),
            "end": float(index * 3 + 3),
        }
        for index, item in enumerate(contracts)
    }
    groups = [
        group(index + 1, item["caption_id"], float(index * 3), float(index * 3 + 3))
        for index, item in enumerate(contracts)
    ]
    return build_scene_continuity_document(
        release_id="r-test",
        groups=groups,
        contracts={item["caption_id"]: item for item in contracts},
        captions=captions,
        source_grouping_sha256="1" * 64,
        source_contract_sha256="2" * 64,
    )


def test_micro_actions_at_same_table_are_one_span() -> None:
    document = build([
        contract("c1", "福贵坐在家里，桌上放着面。", people=("C_FUGUI",), location="home_table", action="sit"),
        contract("c2", "他低头吃面。", people=("C_FUGUI",), location="home_table", action="eat"),
        contract("c3", "他摸了摸自己的脸。", people=("C_FUGUI",), location="home_table", action="touch_face"),
    ])

    assert document["span_count"] == 1
    assert document["spans"][0]["group_ids"] == ["G001", "G002", "G003"]
    assert document["spans"][0]["narration_only_actions"] == ["sit", "eat", "touch_face"]


def test_relationship_participant_is_aggregated_from_later_caption() -> None:
    document = build([
        contract("c1", "龙二被押往刑场。", people=("C_LONGER",), location="execution_road", action="escorted", event_instance="execution_arrival"),
        contract("c2", "半路遇到了福贵。", people=("C_LONGER", "C_FUGUI"), location="execution_road", action="encounter", event_instance="execution_arrival"),
    ])

    assert document["span_count"] == 1
    assert document["spans"][0]["core_participants"] == ["C_LONGER", "C_FUGUI"]


def test_adjacent_execution_phases_are_one_scene_not_three_action_images() -> None:
    document = build([
        contract("c1", "龙二被绑在柱子上。", people=("C_LONGER",), location="execution_post", action="bound", event_instance="execution_death"),
        contract("c2", "枪响了。", people=("C_LONGER",), location="execution_post", action="gunshot", event_instance="execution_death"),
        contract("c3", "龙二死了。", people=("C_LONGER",), location="execution_post", action="dies", event_instance="execution_death"),
    ])

    assert document["span_count"] == 1
    assert document["stats"]["action_only_split_count"] == 0


def test_location_change_still_forces_a_new_span() -> None:
    document = build([
        contract("c1", "福贵坐在家里。", people=("C_FUGUI",), location="home", action="sit"),
        contract("c2", "福贵走上田埂。", people=("C_FUGUI",), location="field", action="walk"),
    ])

    assert document["span_count"] == 2
    assert document["spans"][1]["cut_in_reason"] == "location_change"


def test_disjoint_people_without_shared_event_start_a_new_scene() -> None:
    document = build([
        contract("c1", "福贵坐在堂屋。", people=("C_FUGUI",), location="home", action="sit"),
        contract("c2", "龙二在堂屋清点地契。", people=("C_LONGER",), location="home", action="count_deeds"),
    ])

    assert document["span_count"] == 2
    assert document["spans"][1]["cut_in_reason"] == "independent_cast_change"


def test_duration_does_not_force_a_cut() -> None:
    items = [
        contract(f"c{i}", f"同一张桌边的第{i}段叙述。", people=("C_FUGUI",), location="home_table", action=f"micro_{i}")
        for i in range(1, 9)
    ]
    document = build(items)

    assert document["spans"][0]["duration"] == 24.0
    assert document["span_count"] == 1


def test_different_default_beat_event_ids_do_not_force_a_cut() -> None:
    first = contract("c1", "福贵在屋里坐着。", people=("C_FUGUI",), location="home", action="sit")
    second = contract("c2", "他接着低头吃面。", people=("C_FUGUI",), location="home", action="eat")
    first["scene_state"]["action_semantics"]["event_instance_id"] = "sequence:beat-B01"
    second["scene_state"]["action_semantics"]["event_instance_id"] = "sequence:beat-B02"

    document = build([first, second])

    assert document["span_count"] == 1


def test_span_exposes_one_representative_core_and_narration_only_actions() -> None:
    document = build([
        contract("c1", "龙二被绑在柱子上。", people=("C_LONGER",), location="execution_post", action="bound", event_instance="execution_death"),
        contract("c2", "枪响了，龙二死了。", people=("C_LONGER",), location="execution_post", action="gunshot_and_death", event_instance="execution_death"),
    ])

    span = document["spans"][0]
    assert "execution_post" in span["representative_visual_core"]
    assert span["narration_only_actions"] == ["bound", "gunshot_and_death"]


def test_old_man_and_ox_must_show_the_ox() -> None:
    """字幕说“老头和老牛”时，画面必须真的把牛放进视觉证据里。"""
    document = build([
        contract(
            "c1", "老头和老牛在田埂上慢慢走。",
            people=("C_OLD",), location="field_ridge", action="walk",
            story_objects=("老牛",),
            event_state={
                "action_predicate": "walk_with_ox",
                "required_observable_evidence": ["老牛", "老人"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
    ])
    span = document["spans"][0]
    assert "老牛" in span["scene_defining_props"]
    assert "老牛" in span["visual_evidence"]
    assert "老人" in span["visual_evidence"]


def test_young_collector_does_not_reuse_old_man_frame() -> None:
    """同一地点、仅人生阶段跳变时必须换图（青年福贵 -> 老年福贵）。"""
    document = build([
        contract(
            "c1", "老人坐在屋檐下回忆往事。",
            people=("C_FUGUI",), location="village", action="remember",
            life_stage="old",
            event_state={
                "action_predicate": "remember",
                "required_observable_evidence": ["老年福贵"],
                "forbidden_contradictory_state": ["青年福贵"],
                "is_reference_death": False,
            },
        ),
        contract(
            "c2", "年轻人下乡收集民谣。",
            people=("C_FUGUI",), location="village", action="collect_ballads",
            life_stage="young",
            event_state={
                "action_predicate": "collect_ballads",
                "required_observable_evidence": ["青年福贵", "村落"],
                "forbidden_contradictory_state": ["老年福贵"],
                "is_reference_death": False,
            },
        ),
    ])
    assert document["span_count"] == 2
    assert document["spans"][1]["cut_in_reason"] == "life_stage_change"
    assert "青年福贵" in document["spans"][1]["visual_evidence"]


def test_execution_death_continuation_keeps_one_container() -> None:
    """绑柱 -> 开枪 -> 倒下 -> 死亡即使 predicate 变成 collapse/death_aftermath，
    仍属于 EXECUTION_AT_POST 同一个 Scene Span。"""
    document = build([
        contract(
            "b1", "龙二绑在柱子上。",
            people=("C_LONGER",), location="execution_post", action="bound",
            event_instance="event:execution_at_post:execution_post",
            event_state={
                "action_predicate": "execution_at_post",
                "required_observable_evidence": ["刑柱", "绳子"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
        contract(
            "b2", "枪响了。",
            people=("C_LONGER",), location="execution_post", action="gunshot",
            event_instance="event:execution_at_post:execution_post",
            event_state={
                "action_predicate": "execution_at_post",
                "required_observable_evidence": ["执行队"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
        contract(
            "b3", "龙二倒下了。",
            people=("C_LONGER",), location="execution_post", action="falls",
            event_instance="event:physical_collapse:execution_post",
            event_state={
                "action_predicate": "collapse",
                "required_observable_evidence": ["倒地"],
                "forbidden_contradictory_state": ["健康站立"],
                "is_reference_death": False,
            },
        ),
        contract(
            "b4", "龙二死了。",
            people=("C_LONGER",), location="execution_post", action="dies",
            event_instance="event:death_aftermath:execution_post",
            event_state={
                "action_predicate": "death_aftermath",
                "required_observable_evidence": ["死亡后果"],
                "forbidden_contradictory_state": ["当事人健康站立或微笑"],
                "is_reference_death": False,
            },
        ),
    ])
    assert document["span_count"] == 1
    span = document["spans"][0]
    assert span["event_container"] == "execution_at_post"
    assert span["group_ids"] == ["G001", "G002", "G003", "G004"]


def test_beans_by_bedside_does_not_reuse_field_frame() -> None:
    """字幕说“煮半锅豆子放床边”，画面不得仍是田里。"""
    document = build([
        contract(
            "c1", "福贵在田里收豆子。",
            people=("C_FUGUI",), location="field", action="harvest",
            story_objects=("豆子",),
            event_state={
                "action_predicate": "harvest_beans",
                "required_observable_evidence": ["田里", "豆子"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
        contract(
            "c2", "煮半锅豆子放床边。",
            people=("C_FUGUI",), location="bedside", action="cook_and_place",
            story_objects=("半锅豆子", "床"),
            event_state={
                "action_predicate": "cook_beans",
                "required_observable_evidence": ["床边", "锅", "豆子"],
                "forbidden_contradictory_state": ["田里"],
                "is_reference_death": False,
            },
        ),
    ])
    assert document["span_count"] == 2
    assert document["spans"][1]["cut_in_reason"] == "location_change"
    assert "床边" in document["spans"][1]["visual_evidence"]
    assert "田里" not in document["spans"][1]["visual_evidence"]


def test_fengxia_wedding_must_be_a_wedding_scene() -> None:
    """凤霞出嫁必须进入婚礼事件容器，而不是沿用家里的画面。"""
    document = build([
        contract(
            "c1", "凤霞在家里整理衣裳。",
            people=("C_FENGXIA",), location="home", action="dress",
            event_state={
                "action_predicate": "dress",
                "required_observable_evidence": ["凤霞", "家里"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
        contract(
            "c2", "凤霞出嫁那天，队伍走过村口。",
            people=("C_FENGXIA",), location="village_gate", action="wedding_procession",
            event_instance="event:wedding",
            event_state={
                "action_predicate": "wedding_procession",
                "required_observable_evidence": ["凤霞", "婚礼队伍", "红盖头"],
                "forbidden_contradictory_state": ["婚礼前家常衣服"],
                "is_reference_death": False,
            },
        ),
    ])
    assert document["span_count"] == 2
    wedding = document["spans"][1]
    assert wedding["event_container"] == "wedding"
    assert "婚礼队伍" in wedding["visual_evidence"]
    assert "红盖头" in wedding["visual_evidence"]


def test_longer_execution_standard_example_two_spans() -> None:
    """龙二范例：押送+相遇=1 span；绑柱→开枪→倒下→死亡=1 span；两段之间必须换。"""
    document = build([
        contract("a1", "龙二被拉到邻村枪毙。", people=("C_LONGER",), location="execution_road", action="escorted", event_instance="event:execution_arrival"),
        contract("a2", "福贵也去看。", people=("C_LONGER", "C_FUGUI"), location="execution_road", action="observe", event_instance="event:execution_arrival"),
        contract("a3", "龙二经过福贵。", people=("C_LONGER", "C_FUGUI"), location="execution_road", action="passes", event_instance="event:execution_arrival"),
        contract("a4", "龙二和福贵说话。", people=("C_LONGER", "C_FUGUI"), location="execution_road", action="talk", event_instance="event:execution_arrival"),
        contract("b1", "龙二绑在柱子上。", people=("C_LONGER",), location="execution_post", action="bound", event_instance="event:execution_at_post"),
        contract("b2", "枪响了。", people=("C_LONGER",), location="execution_post", action="gunshot", event_instance="event:execution_at_post"),
        contract("b3", "龙二倒下了。", people=("C_LONGER",), location="execution_post", action="falls", event_instance="event:execution_at_post"),
        contract("b4", "龙二死了。", people=("C_LONGER",), location="execution_post", action="dies", event_instance="event:execution_at_post"),
    ])
    assert document["span_count"] == 2
    arrival, death = document["spans"]
    assert arrival["group_ids"] == ["G001", "G002", "G003", "G004"]
    assert arrival["core_participants"] == ["C_LONGER", "C_FUGUI"]
    assert death["group_ids"] == ["G005", "G006", "G007", "G008"]
    assert death["event_container"] == "execution_at_post"
    assert death["narration_only_actions"] == ["bound", "gunshot", "falls", "dies"]


def test_micro_gestures_never_split_a_stable_scene() -> None:
    """摸脸/摸胳膊/拿筷子/说话都属于旁白动作，不能各自拆图。"""
    document = build([
        contract("c1", "福贵坐在桌边，桌上放着面。", people=("C_FUGUI",), location="home_table", action="sit"),
        contract("c2", "他拿起筷子吃面。", people=("C_FUGUI",), location="home_table", action="pick_chopsticks"),
        contract("c3", "他摸了摸脸。", people=("C_FUGUI",), location="home_table", action="touch_face"),
        contract("c4", "他摸了摸胳膊。", people=("C_FUGUI",), location="home_table", action="touch_arm"),
        contract("c5", "他放下筷子，低头说话。", people=("C_FUGUI",), location="home_table", action="speak"),
    ])
    assert document["span_count"] == 1
    assert document["spans"][0]["narration_only_actions"] == [
        "sit", "pick_chopsticks", "touch_face", "touch_arm", "speak",
    ]


def test_visual_evidence_fallback_splits_when_frame_would_mislead() -> None:
    """E 兜底：即使位置字段没变，字幕已进入 B 场景、画面仍会误解为 A 场景时必须拆。"""
    document = build([
        contract(
            "c1", "画面停在田里。",
            people=("C_FUGUI",), location="field", action="stay",
            story_objects=("田野", "豆子"),
            event_state={
                "action_predicate": "stay_in_field",
                "required_observable_evidence": ["田野", "豆子"],
                "forbidden_contradictory_state": [],
                "is_reference_death": False,
            },
        ),
        contract(
            "c2", "字幕讲'煮半锅豆子放床边'。",
            people=("C_FUGUI",), location="field", action="narrate_beans",
            story_objects=("床边", "锅", "豆子"),
            event_state={
                "action_predicate": "narrate_beans_by_bed",
                "required_observable_evidence": ["床边", "锅"],
                "forbidden_contradictory_state": ["田野"],
                "is_reference_death": False,
            },
        ),
    ])
    assert document["span_count"] == 2
    assert document["spans"][1]["cut_in_reason"] == "visual_evidence_conflict"
    assert document["spans"][1]["visual_forbidden_states"] == ["田野"]
