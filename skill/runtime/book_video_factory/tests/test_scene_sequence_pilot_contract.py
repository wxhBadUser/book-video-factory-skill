"""S1-S10 production-contract hardening tests (life-stage lock / role binding / participant cardinality / location type)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from book_video_factory.production_visuals.registry import register_scene_asset
from book_video_factory.production_visuals.scheduler import GenerationScheduleError, plan_generation_run
from book_video_factory.visual_foundation import approve_visual_foundation, build_final_generation_prompt, build_visual_foundation
from book_video_factory.visual_foundation.evidence import load_scene_reference_evidence
from book_video_factory.visual_foundation.runner import reference_inputs_from_pack


def _png(path: Path, size=(1920, 1080), color=(120, 90, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _profile() -> dict:
    return {
        "schema_version": "book-visual-profile.v1", "release_id": "r1",
        "bound_hbg_bridge_digest": "a" * 64, "bound_release_text_sha256": "b" * 64,
        "global_kernel_id": "literary-cinematic-realism-v1", "style_reference_ids": ["REF_JANE_EYRE"],
        "book_look": {"period": "20th century", "geography": "southern China", "visual_world": "restrained literary cinematic realism",
                       "render_balance": "realistic figures, restrained painterly surface", "emotional_temperature": "restrained",
                       "composition_language": ["wide rural negative space", "subtitle-safe lower frame"],
                       "portrait_language": ["weathered faces", "restrained expression"],
                       "environment_language": ["earth tones", "field and courtyard"]},
        "palette_profiles": [{"palette_id": "EARTH_DAY", "name": "earth day", "colors": ["ochre", "old blue", "straw"],
                               "use_cases": ["rural day"],
                               "diagnostic_envelope": {"median_luma": [40, 120], "dark_pixel_share_percent": [5, 70], "mean_saturation": [30, 160], "warm_pixel_share_percent": [20, 95]}}],
        "lighting_profiles": [{"lighting_id": "SUN_WORN", "name": "sun worn", "key": "natural strong daylight", "fill": "low ground bounce", "shadow": "deep but readable", "allowed_times": ["day"]}],
        "material_profiles": [{"material_id": "EARTH_MAT", "name": "earth materials", "materials": ["cloth", "mud", "wood"]}],
        "composition_rules": ["keep subtitle-safe lower frame quiet"], "repeated_motifs": ["old ox", "field road"],
        "forbidden_traits": ["modern architecture", "anime", "generated text"],
        "character_anchors": [
            {"anchor_id": "CHAR_C001", "anchor_type": "character_identity", "character_id": "C001", "name": "Fugui",
             "prompt_subject": "Fugui, rural man", "required_views": ["front", "three_quarter", "full_body", "expression_range", "wardrobe"],
             "invariants": ["thin face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"],
             "wardrobe": ["faded shirt"], "wardrobe_state": "faded shirt", "hat_state": "none"},
            {"anchor_id": "CHAR_C002", "anchor_type": "character_identity", "character_id": "C002", "name": "Jiazhen",
             "prompt_subject": "Jiazhen, rural woman", "required_views": ["front", "three_quarter", "full_body", "expression_range", "wardrobe"],
             "invariants": ["calm face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"],
             "wardrobe": ["faded blouse"], "wardrobe_state": "faded blouse", "hat_state": "none"}],
        "scene_anchors": [{"anchor_id": "SCENE_VILLAGE", "name": "village road", "prompt_subject": "village road", "invariants": ["road", "houses"]}],
        "object_anchors": [],
    }


def build_lineage_project(base: Path) -> Path:
    project = base / "warehouse/projects/pilot"
    _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "required"}})
    proj3 = project / "03_images_生成图片"
    _write_json(proj3 / "BOOK_VISUAL_PROFILE.json", _profile())
    profile_sha = _sha(proj3 / "BOOK_VISUAL_PROFILE.json")
    assets = []
    for task_id in ("ANCHOR_C001_FRONT", "ANCHOR_C001_MID_FRONT", "ANCHOR_C001_OLD_FRONT", "ANCHOR_C002_FRONT"):
        p = _png(project / "assets/generated/anchors" / f"{task_id}.png")
        assets.append({"task_id": task_id, "task_kind": "character_anchor", "path": f"assets/generated/anchors/{task_id}.png", "sha256": _sha(p)})
    p = _png(project / "assets/generated/lookdev/LOOKDEV_LD01.png")
    assets.append({"task_id": "LOOKDEV_LD01", "task_kind": "lookdev", "path": "assets/generated/lookdev/LOOKDEV_LD01.png", "sha256": _sha(p)})
    p = _png(project / "assets/generated/anchors/ANCHOR_LOC_VILLAGE.png")
    assets.append({"task_id": "ANCHOR_LOC_VILLAGE", "task_kind": "scene_anchor", "path": "assets/generated/anchors/ANCHOR_LOC_VILLAGE.png", "sha256": _sha(p)})
    _write_json(proj3 / "VISUAL_ASSET_MANIFEST.json", {
        "schema_version": "visual-asset-manifest.v1", "release_id": "r1", "provider": "host-imagegen",
        "assets": assets, "registered_asset_count": len(assets), "last_registered_at": "2026-08-12T00:00:00+00:00",
    })
    def _a(path: Path) -> str:
        return _sha(project / path)
    fixture = {
        "release_id": "r1", "book_id": "huozhe",
        "style_masters": [{
            "style_master_id": "STYLE_MASTER_RURAL_DAY", "book_id": "huozhe", "role": "rural_day",
            "image_path": "assets/generated/lookdev/LOOKDEV_LD01.png", "image_sha256": _a("assets/generated/lookdev/LOOKDEV_LD01.png"),
            "source_lookdev_task_id": "LOOKDEV_LD01", "visual_profile_sha256": profile_sha, "approved": True,
            "approval_event_sha256": "", "style_guidance": "earth tones, restrained realism",
            "match": {"palette_ids": ["EARTH_DAY"], "lighting_ids": ["SUN_WORN"], "event_states": [], "time_contexts": []},
        }],
        "character_identities": [
            {
                "character_id": "C001", "name": "Fugui", "same_person_lineage": True,
                "narrative_role": "protagonist", "gender_presentation": "male",
                "life_stages": [
                    {"life_stage": "young", "identity_master_task_id": "ANCHOR_C001_FRONT", "supporting_reference_task_ids": [], "identity_invariants": ["thin face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"], "wardrobe_states": ["faded shirt"], "scope": {"chapters": ["1"], "event_states": []}, "approved": True, "apparent_age_range": "20-25"},
                    {"life_stage": "middle_age", "identity_master_task_id": "ANCHOR_C001_MID_FRONT", "supporting_reference_task_ids": [], "identity_invariants": ["thin face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"], "wardrobe_states": ["faded shirt"], "scope": {"chapters": ["2"], "event_states": []}, "approved": True, "apparent_age_range": "45-50"},
                    {"life_stage": "old", "identity_master_task_id": "ANCHOR_C001_OLD_FRONT", "supporting_reference_task_ids": [], "identity_invariants": ["thin face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"], "wardrobe_states": ["faded shirt"], "scope": {"chapters": ["3"], "event_states": []}, "approved": True, "apparent_age_range": "68-72"},
                ],
            },
            {
                "character_id": "C002", "name": "Jiazhen", "same_person_lineage": False,
                "narrative_role": "wife", "gender_presentation": "female",
                "life_stages": [{"life_stage": "middle_age", "identity_master_task_id": "ANCHOR_C002_FRONT", "supporting_reference_task_ids": [], "identity_invariants": ["calm face"], "allowed_changes": ["expression"], "forbidden_changes": ["age change"], "wardrobe_states": ["faded blouse"], "scope": {"chapters": ["2"], "event_states": []}, "approved": True, "apparent_age_range": "40-50"}],
            },
        ],
        "location_anchors": [{"location_id": "LOC_VILLAGE", "name": "village road", "anchor_task_id": "ANCHOR_LOC_VILLAGE", "image_path": "assets/generated/anchors/ANCHOR_LOC_VILLAGE.png", "image_sha256": _a("assets/generated/anchors/ANCHOR_LOC_VILLAGE.png"), "approved": True, "aliases": []}],
    }
    build_visual_foundation(project, fixture)
    approve_visual_foundation(project, release_id="r1", reviewer="tester", note="fixture")
    return project


def _task(task_id: str, *, contract: dict, participant: dict, chapters=("2",)) -> dict:
    prompt = f"production prompt {task_id}"
    return {
        "schema_version": "production-image-task.v1", "task_id": task_id, "scene_id": task_id, "release_id": "r1",
        "generation_lane": "host-imagegen", "generation_mode": "single", "identity_reference_task_ids": [],
        "style_reference_ids": ["REF_JANE_EYRE"], "palette_id": "EARTH_DAY", "lighting_id": "SUN_WORN",
        "visual_event_state": {"action_predicate": "alive_active", "cause_type": "", "is_reference_death": False, "actors": [], "participant_roles": [], "objects": [], "subject_state": "", "required_observable_evidence": [], "forbidden_contradictory_state": []},
        "source_beat_ids": ["B001"], "chapter_ids": list(chapters),
        "participant_constraint": participant,
        "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "output_target": f"assets/generated/scenes/{task_id}.png", "reference_contract": contract,
    }


def _identity(characters):
    return {"required_when_characters_present": True, "characters": characters}


def _loc(location_id, loc_type, required=False):
    return {"location_id": location_id, "location_reference_type": loc_type, "required_persistent": required}


def _char(cid, life_stage=""):
    return {"character_id": cid, "life_stage": life_stage, "apparent_age_range": "", "wardrobe_state": "", "narrative_role": "", "gender_presentation": ""}


def _write_director_inputs(project: Path, tasks: list[dict]) -> None:
    director = project / "05_director"
    director.mkdir(parents=True, exist_ok=True)
    (director / "IMAGE_TASKS.jsonl").write_text("".join(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n" for t in tasks), encoding="utf-8")
    _write_json(director / "SHEET_MAP.json", {"schema_version": "sheet-map.v1", "sheet_groups": [], "single_tasks": [{"task_id": t["task_id"], "reason": "single"} for t in tasks], "sheet_task_count": 0, "single_task_count": len(tasks)})
    _write_json(director / "DIRECTOR_STAGE_MANIFEST.json", {"schema_version": "director-stage-manifest.v1", "release_id": "r1"})


def _load_jobs(project: Path) -> list[dict]:
    return [json.loads(line) for line in (project / "06_visual_production/GENERATION_JOBS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def _pack_for(jobs: list[dict], task_id: str) -> dict:
    for job in jobs:
        packs = job.get("reference_packs", {})
        if task_id in packs:
            return packs[task_id]
    raise AssertionError(f"no pack for {task_id}")


def _contract(identity, location):
    return {"schema_version": "visual-reference-contract.v1", "style": {"required": True}, "identity": identity,
            "location": location, "continuity": {"use_previous_scene": False, "required": False, "reason": ""}}



class SceneSequencePilotContractTests(unittest.TestCase):
    def test_s1_middle_stage_fugui_resolves_middle_root_and_age_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S1", contract=_contract(_identity([_char("C001", "middle_age")]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001"], "expected_narrative_character_count": 1, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S1")
            idref = next(i for i in pack["references"] if i["role"] == "identity_master")
            self.assertEqual(idref["reference_id"], "C001:middle_age")
            self.assertIn("ANCHOR_C001_MID_FRONT", idref["image_path"])
            self.assertEqual(idref["meta"]["apparent_age_range"], "45-50")
            final, _ = build_final_generation_prompt(task, pack)
            self.assertIn("C001 apparent age: 45-50", final)

    def test_s2_old_stage_cannot_satisfy_middle_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            mid = _task("SCENE_MID", contract=_contract(_identity([_char("C001", "middle_age")]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001"], "expected_narrative_character_count": 1, "allow_unlisted_narrative_characters": False}, chapters=("2",))
            old = _task("SCENE_OLD", contract=_contract(_identity([_char("C001", "old")]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001"], "expected_narrative_character_count": 1, "allow_unlisted_narrative_characters": False}, chapters=("3",))
            _write_director_inputs(project, [mid, old])
            plan_generation_run(project)
            jobs = _load_jobs(project)
            mid_ref = next(i for i in _pack_for(jobs, "SCENE_MID")["references"] if i["role"] == "identity_master")
            old_ref = next(i for i in _pack_for(jobs, "SCENE_OLD")["references"] if i["role"] == "identity_master")
            self.assertIn("ANCHOR_C001_MID_FRONT", mid_ref["image_path"])
            self.assertIn("ANCHOR_C001_OLD_FRONT", old_ref["image_path"])
            self.assertEqual(mid_ref["meta"]["apparent_age_range"], "45-50")
            self.assertEqual(old_ref["meta"]["apparent_age_range"], "68-72")
            mid_final, _ = build_final_generation_prompt(mid, _pack_for(jobs, "SCENE_MID"))
            self.assertIn("45-50", mid_final)
            self.assertNotIn("68-72", mid_final)

    def test_s3_two_character_scene_preserves_role_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            c1 = dict(_char("C001", "middle_age")); c1["narrative_role"] = "protagonist"; c1["gender_presentation"] = "male"
            c2 = dict(_char("C002", "middle_age")); c2["narrative_role"] = "wife"; c2["gender_presentation"] = "female"
            task = _task("SCENE_S3", contract=_contract(_identity([c1, c2]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001", "C002"], "expected_narrative_character_count": 2, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S3")
            refs = [i for i in pack["references"] if i["role"] == "identity_master"]
            self.assertEqual(len(refs), 2)
            roles = {i["meta"]["character_id"]: (i["meta"]["narrative_role"], i["meta"]["gender_presentation"]) for i in refs}
            self.assertEqual(roles["C001"], ("protagonist", "male"))
            self.assertEqual(roles["C002"], ("wife", "female"))

    def test_s4_exact_two_character_scene_rejects_third_character(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            c1 = dict(_char("C001", "middle_age")); c2 = dict(_char("C002", "middle_age")); c3 = dict(_char("C001", "old"))
            task = _task("SCENE_S4", contract=_contract(_identity([c1, c2, c3]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001", "C002"], "expected_narrative_character_count": 2, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            with self.assertRaisesRegex(GenerationScheduleError, "participant cardinality mismatch"):
                plan_generation_run(project)

    def test_s5_zero_character_environment_forbids_visible_humans(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S5", contract=_contract(_identity([]), _loc("LOC_VILLAGE", "persistent_location_anchor")), participant={"expected_visible_character_ids": [], "expected_narrative_character_count": 0, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S5")
            self.assertNotIn("identity_master", [i["role"] for i in pack["references"]])
            self.assertEqual(pack["location"]["location_reference_type"], "persistent_location_anchor")
            final, _ = build_final_generation_prompt(task, pack)
            self.assertIn("No visible human figure", final)


    def test_s6_generic_location_proxy_cannot_satisfy_persistent_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S6", contract=_contract(_identity([]), _loc("LOC_HOME_EXTERIOR", "persistent_location_anchor", required=True)), participant={"expected_visible_character_ids": [], "expected_narrative_character_count": 0, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            with self.assertRaisesRegex(GenerationScheduleError, "no canonical persistent anchor"):
                plan_generation_run(project)

    def test_s7_generic_environment_task_may_use_generic_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S7", contract=_contract(_identity([]), _loc("LOC_HOME_EXTERIOR", "generic_environment_reference")), participant={"expected_visible_character_ids": [], "expected_narrative_character_count": 0, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S7")
            self.assertEqual(pack["location"]["location_reference_type"], "generic_environment_reference")
            self.assertNotIn("location_anchor", [i["role"] for i in pack["references"]])

    def test_s8_valid_single_character_scene_still_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S8", contract=_contract(_identity([_char("C001", "middle_age")]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001"], "expected_narrative_character_count": 1, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            result = plan_generation_run(project)
            self.assertEqual(result.status, "created")

    def test_s9_symbolic_zero_character_scene_carries_no_identity_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_lineage_project(Path(temp))
            task = _task("SCENE_S9", contract=_contract(_identity([]), _loc("", "none")), participant={"expected_visible_character_ids": [], "expected_narrative_character_count": 0, "allow_unlisted_narrative_characters": True})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S9")
            self.assertNotIn("identity_master", [i["role"] for i in pack["references"]])

    def test_s10_reference_evidence_preserves_role_and_life_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_lineage_project(base)
            c1 = dict(_char("C001", "middle_age")); c1["narrative_role"] = "protagonist"; c1["gender_presentation"] = "male"
            task = _task("SCENE_S10", contract=_contract(_identity([c1]), _loc("", "none")), participant={"expected_visible_character_ids": ["C001"], "expected_narrative_character_count": 1, "allow_unlisted_narrative_characters": False})
            _write_director_inputs(project, [task])
            plan_generation_run(project)
            pack = _pack_for(_load_jobs(project), "SCENE_S10")
            ref_inputs = reference_inputs_from_pack(pack)
            ref_input_args = [str(i["role"]) + ":" + str(i["image_path"]) for i in ref_inputs]
            source = base / "scene.png"
            _png(source, color=(41, 92, 133))
            director_manifest = project / "05_director/DIRECTOR_STAGE_MANIFEST.json"
            with mock.patch("book_video_factory.production_visuals.registry.compile_director_stage", return_value=SimpleNamespace(manifest_path=director_manifest)), mock.patch("book_video_factory.production_visuals.registry._tasks", return_value={"SCENE_S10": task}):
                register_scene_asset(
                    project, task_id="SCENE_S10", source=source, tool_call_id="imagegen_call_s10_000001",
                    style_reference_ids=["REF_JANE_EYRE"], identity_reference_task_ids=[],
                    reference_inputs=ref_input_args,
                    generation_attempt_id="attempt_s10_000001", generation_mode="i2i",
                    provider_receipt="host_call_s10_ref_000001",
                )
            ev = load_scene_reference_evidence(project)
            record = ev.get("SCENE_S10")
            self.assertIsNotNone(record)
            idref = next(i for i in record["reference_inputs"] if i["role"] == "identity_master")
            self.assertEqual(idref["meta"]["character_id"], "C001")
            self.assertEqual(idref["meta"]["life_stage"], "middle_age")
            self.assertEqual(idref["meta"]["narrative_role"], "protagonist")
            self.assertEqual(idref["meta"]["apparent_age_range"], "45-50")
            self.assertEqual(record["provider_receipt"], "host_call_s10_ref_000001")


if __name__ == "__main__":
    unittest.main()
