# -*- coding: utf-8 -*-
"""M3.1 Visual Hold Planner regression tests (R1..R10).

Every core test is deterministic (no network / no model).  The real-pilot
tests load the actual on-disk pilot data (SEGMENT_TASKS + Caption Visual
Contract) and skip when those gitignored files are absent, so the suite stays
green in a fresh checkout while still exercising the real production path
locally.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

from book_video_factory.semantic_alignment.visual_hold_planner import (
    NAME_TO_ID,
    ReferentResolution,
    build_coverage_plan,
    build_hold_reference_pack,
    classify_merge,
    continuity_reference_eligible,
    gate_scene_image_cleanliness,
    normalize_frame_hygiene,
    normalize_group,
    plan_visual_holds,
    propose_holds,
    resolve_discourse_referents,
    resolve_hold_participant_policy,
    resolve_hold_participants,
    select_preferred_variant,
    validate_hold_boundaries,
    VisualHold,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT_TASKS = REPO_ROOT / "qa" / "sequence-pilot-v1" / "SEGMENT_TASKS.json"
HUOZHE_CONTRACT = (
    REPO_ROOT
    / "workspace" / "book_video_warehouse" / "projects" / "huozhe"
    / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
)
R2_PLAN = REPO_ROOT / "qa" / "sequence-pilot-v1-r2" / "VISUAL_HOLD_PLAN.json"

REGISTER = {
    "C002": "middle_age 45-50",
    "C003": "middle_age 40-50",
    "C004": "adult 20-30",
    "C005": "child 8-12",
    "C006": "adult 35-45",
    "C007": "young_adult 18-25",
}

GOLDEN_HOLDS = [
    ["G070", "G071"], ["G072"], ["G073"], ["G074", "G075"], ["G076"], ["G077"], ["G078"],
    ["G079", "G080"], ["G081", "G082"], ["G083", "G084"], ["G085", "G086", "G087", "G088"],
    ["G089"], ["G090", "G091", "G092"], ["G093", "G094"],
]


def mini_contract(caption_id: str, text: str, subjects=None, visible=None, pron=None, action=None) -> dict:
    return {
        "caption_id": caption_id,
        "caption_text": text,
        "subjects": subjects or [],
        "scene_state": {
            "visible_character_ids": visible or [],
            "continuity_state": {"pronoun_resolutions": pron or []},
            "action_semantics": action or {},
        },
    }


def mini_group(gid: str, captions, start: float, end: float, *, env: str, chars=None) -> dict:
    return {
        "group_id": gid,
        "caption_ids": captions,
        "start": start,
        "end": end,
        "environment": env,
        "character_ids": list(chars or []),
        "caption_texts": [],
    }


# ---------------------------------------------------------------------------
# Discourse subject carryover (R1/R2/R3)
# ---------------------------------------------------------------------------

class TestDiscourseCarryover:
    def test_r2_fengxia_elliptical_subject(self) -> None:
        caps = [
            mini_contract("c0101", "凤霞一年前发了一场高烧，", subjects=["凤霞"], visible=["C004"]),
            mini_contract("c0102", "再不会说话了，"),
            mini_contract("c0103", "也听不见了。"),
        ]
        refs = resolve_discourse_referents(caps)
        ids = [r.resolved_character_id for r in refs["c0102"]]
        assert "C004" in ids
        assert any(r.type == "elliptical_subject" for r in refs["c0102"])

    def test_r3_fugui_action_subject(self) -> None:
        caps = [
            mini_contract("c0114", "福贵走在回家的路上，", subjects=["福贵"], visible=["C002"]),
            mini_contract("c0115", "他摸摸自己的脸，", pron=[{"resolved_entity_id": "C002", "pronoun_resolution": "他"}]),
            mini_contract("c0116", "摸摸自己的胳膊，"),
            mini_contract("c0117", "都是好好的。"),
        ]
        refs = resolve_discourse_referents(caps)
        for cid in ("c0116", "c0117"):
            assert any(r.resolved_character_id == "C002" for r in refs[cid])

    def test_r1_dialogue_speaker_longer_addressee_fugui(self) -> None:
        caps = [
            mini_contract("c0109", "龙二被五花大绑押过来，", subjects=["龙二"], visible=["C006"]),
            mini_contract("c0110", "走过福贵身边时，", subjects=["福贵"], visible=["C002"]),
            mini_contract("c0111", "他回过头，哭着喊：福贵，"),
            mini_contract("c0112", "我是替你去死啊。"),
        ]
        refs = resolve_discourse_referents(caps)
        # speaker of the quoted continuation is Long Er (C006), addressee Fugui (C002)
        assert any(r.type == "dialogue_speaker" and r.resolved_character_id == "C006" for r in refs["c0112"])
        assert any(r.type == "dialogue_speaker" and r.resolved_character_id == "C002" for r in refs["c0112"])
        # the introducing caption also resolves the speaker to C006 (non-vocative)
        assert any(r.type == "dialogue_speaker" and r.resolved_character_id == "C006" for r in refs["c0111"])

    def test_fail_closed_when_no_antecedent(self) -> None:
        caps = [mini_contract("c1", "再不会说话了，")]
        refs = resolve_discourse_referents(caps)
        assert refs["c1"] == []


# ---------------------------------------------------------------------------
# Merge rules (R7/R9) + life-stage (R4)
# ---------------------------------------------------------------------------

class TestMergeRules:
    def test_r7_one_hold_may_contain_multiple_groups(self) -> None:
        # same execution event (B051) + same location: one frame valid for all
        groups = [
            mini_group("G1", ["c1"], 0.0, 3.0, env="village_execution", chars=["C006"]),
            mini_group("G2", ["c2"], 3.0, 6.0, env="village_execution", chars=["C002"]),
            mini_group("G3", ["c3"], 6.0, 9.0, env="village_execution", chars=["C006", "C002"]),
            mini_group("G4", ["c4"], 9.0, 12.0, env="village_execution"),
        ]
        for g in groups:
            g["event_phase"] = {"hard_split_event": "climax", "event_instance_id": "beat:B051"}
        holds = propose_holds(groups)
        assert holds[0] == ["G1", "G2", "G3", "G4"]

    def test_r9_hard_conflict_forces_separate_holds(self) -> None:
        alive = mini_group("A", ["c1"], 0.0, 3.0, env="home_interior", chars=["C004"])
        death = mini_group("B", ["c2"], 3.0, 6.0, env="home_interior", chars=["C004"])
        death["caption_texts"] = ["等福贵回到家，他娘已经死了。"]
        alive["caption_texts"] = ["凤霞一年前发了一场高烧，"]
        a = normalize_group(alive)
        b = normalize_group(death)
        a["resolved_subjects"] = a["primary_subject_ids"]
        b["resolved_subjects"] = b["primary_subject_ids"]
        allowed, _, reason = classify_merge(a, b)
        assert not allowed
        assert reason == "alive_vs_death"

    def test_r4_no_elderly_fugui_when_merging(self) -> None:
        g081 = normalize_group(mini_group("G081", ["c104"], 253.137, 257.625,
                                          env="home_exterior", chars=["C003", "C004", "C005", "C002"]))
        g082 = normalize_group(mini_group("G082", ["c105"], 257.625, 259.625,
                                          env="home_exterior", chars=["C002"]))
        hold = VisualHold(
            hold_id="H09", start=253.137, end=259.625, duration=6.488,
            group_ids=("G081", "G082"), caption_ids=("c104", "c105"), caption_texts=("但他回来了，家珍在，凤霞在，有庆也在。", "日子还能过。"),
            visual_proposition="", coverage_plan=(), required_character_instances=(),
            participant_constraint="", location="home_exterior", time_context="", event_state="",
            merge_reason="", referent_resolutions=(),
        )
        ids, constraint = resolve_hold_participants(hold, [g081, g082], REGISTER)
        assert "C002" in ids
        assert "middle_age 45-50" in constraint
        assert "elderly" not in constraint
        assert "老年" not in constraint


# ---------------------------------------------------------------------------
# Raw cleanliness gate + variant selection (R5/R6)
# ---------------------------------------------------------------------------

class TestRawCleanliness:
    def test_r5_contaminated_raw_blocked(self) -> None:
        allowed, _ = gate_scene_image_cleanliness("CONTAMINATED")
        assert not allowed
        allowed, _ = gate_scene_image_cleanliness("UNRESOLVED_VERIFY")
        assert not allowed
        allowed, _ = gate_scene_image_cleanliness("CLEAN")
        assert allowed

    def test_r6_clean_alternate_replaces_contaminated_first(self) -> None:
        variants = [
            {"file": "raw_1.jpg", "semantic": "PASS", "identity": "PASS", "participant": "PASS", "cleanliness": "CONTAMINATED", "quality": "HIGH"},
            {"file": "raw_2.jpg", "semantic": "PASS", "identity": "PASS", "participant": "PASS", "cleanliness": "CLEAN", "quality": "MED"},
        ]
        chosen, why = select_preferred_variant(
            variants,
            semantic=["PASS", "PASS"], identity=["PASS", "PASS"], participant=["PASS", "PASS"],
            cleanliness=["CONTAMINATED", "CLEAN"], quality=["HIGH", "MED"],
        )
        assert chosen["file"] == "raw_2.jpg"

    def test_r6_semantic_priority_outranks_cleanliness_tie(self) -> None:
        variants = [
            {"file": "semantic_fail.jpg", "semantic": "FAIL", "cleanliness": "CLEAN"},
            {"file": "semantic_ok_contam.jpg", "semantic": "PASS", "cleanliness": "CONTAMINATED"},
            {"file": "semantic_ok_clean.jpg", "semantic": "PASS", "cleanliness": "CLEAN"},
        ]
        chosen, _ = select_preferred_variant(
            variants,
            semantic=["FAIL", "PASS", "PASS"], identity=["", "", ""], participant=["", "", ""],
            cleanliness=["CLEAN", "CONTAMINATED", "CLEAN"], quality=["", "", ""],
        )
        assert chosen["file"] == "semantic_ok_clean.jpg"


# ---------------------------------------------------------------------------
# Coverage per caption (R8) + plan validation (R10)
# ---------------------------------------------------------------------------

class TestPlanStructure:
    def _hold(self, caption_ids, caption_texts) -> VisualHold:
        return VisualHold(
            hold_id="H11", start=0.0, end=8.207, duration=8.207,
            group_ids=("G085", "G086", "G087", "G088"),
            caption_ids=tuple(caption_ids), caption_texts=tuple(caption_texts),
            visual_proposition="", coverage_plan=(), required_character_instances=(),
            participant_constraint="", location="village_execution", time_context="", event_state="",
            merge_reason="", referent_resolutions=(),
        )

    def test_r8_coverage_stays_per_caption_after_merge(self) -> None:
        captions = ["c109", "c110", "c111", "c112"]
        texts = ["龙二被五花大绑押过来，", "走过福贵身边时，", "他回过头，哭着喊：福贵，", "我是替你去死啊。"]
        hold = self._hold(captions, texts)
        plan = build_coverage_plan(hold, {}, {})
        assert len(plan) == 4
        assert plan[0].intended_coverage == "DIRECT"
        assert all(item.intended_coverage in ("DIRECT", "SUPPORTED", "SYMBOLIC") for item in plan)

    def test_r10_plan_does_not_alter_vtt_timestamps(self) -> None:
        groups = [
            mini_group("A", ["c1"], 0.0, 3.0, env="road"),
            mini_group("B", ["c2"], 3.5, 6.0, env="road"),  # real 0.5s gap in VTT
        ]
        plan_holds = [{"hold_id": "H1", "group_ids": ["A"], "start": 0.0, "end": 3.0},
                      {"hold_id": "H2", "group_ids": ["B"], "start": 3.5, "end": 6.0}]
        findings = validate_hold_boundaries(groups, plan_holds)
        assert findings == []  # gaps preserved, timestamps untouched


# ---------------------------------------------------------------------------
# Real production path (pilot data) - skipped when files absent
# ---------------------------------------------------------------------------

def _load_pilot():
    tasks = json.loads(PILOT_TASKS.read_text(encoding="utf-8"))["tasks"]
    tasks = sorted(tasks, key=lambda t: t["start"])
    cc = json.loads(HUOZHE_CONTRACT.read_text(encoding="utf-8"))["contracts"]
    for t in tasks:
        first = t["caption_ids"][0]
        asem = cc[first].get("scene_state", {}).get("action_semantics", {})
        t["event_phase"] = {
            "hard_split_event": asem.get("hard_split_event", "none"),
            "event_instance_id": asem.get("event_instance_id", ""),
        }
        t["caption_texts"] = [c.strip() for c in t["caption_text"].split(" / ")]
    return tasks, cc


@pytest.mark.skipif(not (PILOT_TASKS.exists() and HUOZHE_CONTRACT.exists()),
                    reason="pilot data not present in this checkout")
class TestRealPilotProductionPath:
    def test_auto_proposal_reproduces_human_golden_plan(self) -> None:
        tasks, cc = _load_pilot()
        ordered = [dict(cc[cid]) for t in tasks for cid in t["caption_ids"] if cid in cc]
        refs = resolve_discourse_referents(ordered)
        proposed = propose_holds(tasks, refs)
        assert proposed == GOLDEN_HOLDS

    def test_r1_r2_r3_referents_on_real_captions(self) -> None:
        tasks, cc = _load_pilot()
        ordered = [dict(cc[cid]) for t in tasks for cid in t["caption_ids"] if cid in cc]
        refs = resolve_discourse_referents(ordered)
        # R2: 0102/0103 resolve Fengxia
        assert any(r.resolved_character_id == "C004" for r in refs["caption-0102"])
        assert any(r.resolved_character_id == "C004" for r in refs["caption-0103"])
        # R3: 0116/0117 resolve Fugui
        assert any(r.resolved_character_id == "C002" for r in refs["caption-0116"])
        # R1: 0112 dialogue speaker = Long Er, addressee = Fugui
        assert any(r.type == "dialogue_speaker" and r.resolved_character_id == "C006" for r in refs["caption-0112"])
        assert any(r.type == "dialogue_speaker" and r.resolved_character_id == "C002" for r in refs["caption-0112"])

    def test_r8_plan_coverage_is_per_caption(self) -> None:
        if not R2_PLAN.exists():
            pytest.skip("R2 plan not built")
        plan = json.loads(R2_PLAN.read_text(encoding="utf-8"))
        total = sum(len(h["coverage_plan"]) for h in plan["holds"])
        assert total == 30  # 30 captions, each with one coverage item
        assert plan["hold_count"] == 14

    def test_r10_plan_preserves_group_boundaries(self) -> None:
        tasks, cc = _load_pilot()
        plan_doc, findings = plan_visual_holds(
            groups=tasks, captions=cc,
            approved_holds=GOLDEN_HOLDS, character_register=REGISTER,
        )
        assert findings == []
        for hold in plan_doc["holds"]:
            groups_in_hold = [g for g in tasks if g["group_id"] in hold["group_ids"]]
            assert abs(hold["start"] - min(g["start"] for g in groups_in_hold)) < 1e-6
            assert abs(hold["end"] - max(g["end"] for g in groups_in_hold)) < 1e-6

    def test_r4_no_elderly_fugui_in_h09(self) -> None:
        tasks, cc = _load_pilot()
        plan_doc, _ = plan_visual_holds(
            groups=tasks, captions=cc,
            approved_holds=GOLDEN_HOLDS, character_register=REGISTER,
        )
        h09 = next(h for h in plan_doc["holds"] if h["hold_id"] == "H09")
        assert "C002" in h09["required_character_instances"]
        assert "middle_age 45-50" in h09["participant_constraint"]
        assert "elderly" not in h09["participant_constraint"]


# ---------------------------------------------------------------------------
# M3.2 final targeted repair (F1..F10)
# ---------------------------------------------------------------------------

class TestM32TargetedRepair:
    def test_f1_current_time_fugui_resolves_only_c002(self) -> None:
        # reproduce the real v2-contract ambiguity: the caption lists BOTH
        # young-Fugui and middle-Fugui; current-time resolution must collapse
        # to the single active life-stage instance C002.
        caps = [
            mini_contract("c0110", "走过福贵身边时，", subjects=["福贵"], visible=["C002"]),
            mini_contract("c0111", "他回过头，哭着喊：福贵，",
                          subjects=["福贵（青年阔少）", "福贵（中年/老年）"],
                          visible=["C001", "C002"]),
            mini_contract("c0112", "我是替你去死啊。"),
        ]
        refs = resolve_discourse_referents(caps)
        explicit_ids = [r.resolved_character_id for r in refs["c0111"] if r.type == "explicit"]
        assert "C001" not in explicit_ids
        assert "C002" in explicit_ids
        assert "C001" not in explicit_ids

    def test_f2_gunshot_does_not_inherit_actor(self) -> None:
        caps = [
            mini_contract("c0112", "我是替你去死啊。"),
            mini_contract("c0113", "枪响了五声。"),
        ]
        refs = resolve_discourse_referents(caps)
        assert refs["c0113"] == []  # impersonal event: no subject inheritance

    def test_f3_collective_army_action_no_c002_actor(self) -> None:
        caps = [
            mini_contract("c0094", "战场上他认识了老兵老全，还有娃娃兵春生。", subjects=["福贵"], visible=["C002"]),
            mini_contract("c0096", "没吃的，抢空投，拆房子，掘坟烧棺材板。"),
        ]
        refs = resolve_discourse_referents(caps)
        assert refs["c0096"] == []

    def test_f4_family_scene_forbids_extra_characters(self) -> None:
        g = normalize_group(mini_group("G081", ["c104"], 253.137, 257.625,
                                       env="home_exterior", chars=["C003", "C004", "C005", "C002"]))
        hold = VisualHold(
            hold_id="H09", start=253.137, end=259.625, duration=6.488,
            group_ids=("G081",), caption_ids=("c104",), caption_texts=("但他回来了，家珍在，凤霞在，有庆也在。",),
            visual_proposition="", coverage_plan=(), required_character_instances=(),
            participant_constraint="", location="home_exterior", time_context="", event_state="",
            merge_reason="", referent_resolutions=(),
        )
        policy = resolve_hold_participant_policy(hold, [g])
        assert policy["background_extras_policy"] == "forbidden"
        assert set(policy["persistent_story_characters"]) == {"C003", "C004", "C005", "C002"}
        assert policy["anonymous_required_roles"] == ()

    def test_f5_execution_scene_allows_anonymous_roles(self) -> None:
        g = normalize_group(mini_group("G085", ["c109"], 267.698, 270.185,
                                       env="village_execution", chars=["C006", "C002"]))
        g["extra_allowed"] = True
        g["extra_kind"] = ["crowd"]
        hold = VisualHold(
            hold_id="H11", start=267.698, end=275.905, duration=8.207,
            group_ids=("G085",), caption_ids=("c109",), caption_texts=("龙二被五花大绑押过来，",),
            visual_proposition="", coverage_plan=(), required_character_instances=(),
            participant_constraint="", location="village_execution", time_context="", event_state="",
            merge_reason="", referent_resolutions=(),
        )
        policy = resolve_hold_participant_policy(hold, [g])
        assert policy["background_extras_policy"] == "crowd_allowed"
        assert "C006" in policy["persistent_story_characters"]
        assert "C002" in policy["persistent_story_characters"]
        assert "crowd" in policy["anonymous_required_roles"]

    def test_f6_continuity_reference_off_normally(self) -> None:
        def mk(hid, gid, start, end, chars):
            g = normalize_group(mini_group(gid, ["c1"], start, end, env="village_execution", chars=chars))
            g["event_phase"] = {"hard_split_event": "climax", "event_instance_id": "beat:B051"}
            return g, VisualHold(
                hold_id=hid, start=start, end=end, duration=end - start,
                group_ids=(gid,), caption_ids=("c1",), caption_texts=("字幕",),
                visual_proposition="", coverage_plan=(), required_character_instances=tuple(chars),
                participant_constraint="", location="village_execution", time_context="", event_state="",
                merge_reason="", referent_resolutions=(),
            )
        g_a, h_a = mk("H10", "G084", 259.78, 267.698, ["C002"])
        g_b, h_b = mk("H11", "G085", 267.698, 275.905, ["C006", "C002"])
        # different event instance -> not eligible
        g_a["event_phase"] = {"hard_split_event": "climax", "event_instance_id": "beat:B050"}
        assert not continuity_reference_eligible(h_a, h_b, [g_a, g_b])

    def test_f7_continuity_eligible_same_char_event_adjacent(self) -> None:
        def mk(hid, gid, start, end, chars, ev):
            g = normalize_group(mini_group(gid, ["c1"], start, end, env="village_execution", chars=chars))
            g["event_phase"] = {"hard_split_event": "climax", "event_instance_id": ev}
            return g, VisualHold(
                hold_id=hid, start=start, end=end, duration=end - start,
                group_ids=(gid,), caption_ids=("c1",), caption_texts=("字幕",),
                visual_proposition="", coverage_plan=(), required_character_instances=tuple(chars),
                participant_constraint="", location="village_execution", time_context="", event_state="",
                merge_reason="", referent_resolutions=(),
            )
        g_a, h_a = mk("H10", "G084", 259.78, 267.698, ["C002"], "beat:B051")
        g_b, h_b = mk("H11", "G085", 267.698, 275.905, ["C006", "C002"], "beat:B051")
        assert continuity_reference_eligible(h_a, h_b, [g_a, g_b])

    @pytest.mark.skipif(not (PILOT_TASKS.exists() and HUOZHE_CONTRACT.exists()),
                        reason="pilot data not present in this checkout")
    def test_f8_h11_reference_pack_has_roots_and_continuity(self) -> None:
        tasks, cc = _load_pilot()
        plan_doc, _ = plan_visual_holds(
            groups=tasks, captions=cc,
            approved_holds=GOLDEN_HOLDS, character_register=REGISTER,
        )
        holds = [VisualHold(**{**h, "caption_texts": tuple(h["caption_texts"]),
                               "group_ids": tuple(h["group_ids"]), "caption_ids": tuple(h["caption_ids"]),
                               "coverage_plan": (), "referent_resolutions": (),
                               "required_character_instances": tuple(h["required_character_instances"]),
                               "persistent_story_characters": tuple(h["persistent_story_characters"]),
                               "anonymous_required_roles": tuple(h["anonymous_required_roles"])}) for h in plan_doc["holds"]]
        selected_frames = {h.hold_id: "frame_%s.png" % h.hold_id for h in holds}
        identity_roots = {"C002": "FUGUI_MIDDLE_IDENTITY_ROOT.jpg",
                          "C006": "LONGER_IDENTITY_ROOT.jpg"}
        h11 = next(h for h in holds if h.hold_id == "H11")
        h10 = next(h for h in holds if h.hold_id == "H10")
        pack = build_hold_reference_pack(h11, holds, tasks, identity_roots, "STYLE_MASTER_RURAL_DAY.jpg", selected_frames)
        kinds = [r["kind"] for r in pack["reference_pack"]]
        assert "identity_root" in kinds
        assert "continuity_reference" in kinds
        assert pack["continuity_reference"]["source_hold_id"] == "H10"
        assert any(r["character_id"] == "C006" for r in pack["reference_pack"] if r["kind"] == "identity_root")
        assert any(r["character_id"] == "C002" for r in pack["reference_pack"] if r["kind"] == "identity_root")
        assert pack["continuity_priority"] == "identity_root > continuity_reference"

    def test_f9_small_border_normalized(self) -> None:
        from PIL import Image
        import numpy as np
        import tempfile
        arr = np.zeros((1080, 1920, 3), dtype=np.uint8)
        arr[8:1072, :, :] = 120  # 8px+8px = 16px total black border (1.48% <= 2%)
        with tempfile.TemporaryDirectory() as td:
            src = pathlib.Path(td) / "src.png"
            out = pathlib.Path(td) / "norm.png"
            Image.fromarray(arr).save(src)
            result = normalize_frame_hygiene(src, out)
            assert result["verdict"] == "NORMALIZED"
            with Image.open(out) as im:
                assert im.size == (1920, 1080)

    def test_f10_large_border_blocks_render(self) -> None:
        from PIL import Image
        import numpy as np
        import tempfile
        arr = np.zeros((1080, 1920, 3), dtype=np.uint8)
        arr[80:1000, :, :] = 120  # 160px border (14.8% > 2%)
        with tempfile.TemporaryDirectory() as td:
            src = pathlib.Path(td) / "src.png"
            out = pathlib.Path(td) / "norm.png"
            Image.fromarray(arr).save(src)
            result = normalize_frame_hygiene(src, out)
            assert result["verdict"] == "RAW_CONTAMINATION"
        allowed, _ = gate_scene_image_cleanliness("CONTAMINATED")
        assert not allowed
