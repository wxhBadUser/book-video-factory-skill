"""Visual Foundation hard-gate tests (T1-T12), driving production entrypoints:
Director -> Generation Plan -> Reference Resolver -> generation runner boundary -> Registry."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from book_video_factory.director_stage.compiler import DirectorStageError, _expected
from book_video_factory.production_visuals.registry import SceneAssetError, register_scene_asset
from book_video_factory.production_visuals.scheduler import GenerationScheduleError, plan_generation_run
from book_video_factory.visual_foundation import (
    VisualFoundationError,
    approve_visual_foundation,
    build_visual_foundation,
    verify_visual_foundation,
)
from book_video_factory.visual_foundation.resolver import resolve_reference_pack


def _png(path: Path, size=(1920, 1080), color=(120, 90, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _profile(release_id: str = "r1") -> dict:
    return {
        "schema_version": "book-visual-profile.v1",
        "release_id": release_id,
        "bound_hbg_bridge_digest": "a" * 64,
        "bound_release_text_sha256": "b" * 64,
        "global_kernel_id": "literary-cinematic-realism-v1",
        "style_reference_ids": ["REF_JANE_EYRE"],
        "book_look": {
            "period": "20th century",
            "geography": "southern China",
            "visual_world": "restrained literary cinematic realism",
            "render_balance": "realistic figures, restrained painterly surface",
            "emotional_temperature": "restrained",
            "composition_language": ["wide rural negative space", "subtitle-safe lower frame"],
            "portrait_language": ["weathered faces", "restrained expression"],
            "environment_language": ["earth tones", "field and courtyard"],
        },
        "palette_profiles": [{
            "palette_id": "EARTH_DAY",
            "name": "earth day",
            "colors": ["ochre", "old blue", "straw"],
            "use_cases": ["rural day"],
            "diagnostic_envelope": {"median_luma": [40, 120], "dark_pixel_share_percent": [5, 70], "mean_saturation": [30, 160], "warm_pixel_share_percent": [20, 95]},
        }],
        "lighting_profiles": [{
            "lighting_id": "SUN_WORN",
            "name": "sun worn",
            "key": "natural strong daylight",
            "fill": "low ground bounce",
            "shadow": "deep but readable",
            "allowed_times": ["day"],
        }],
        "material_profiles": [{"material_id": "EARTH_MAT", "name": "earth materials", "materials": ["cloth", "mud", "wood"]}],
        "composition_rules": ["keep subtitle-safe lower frame quiet"],
        "repeated_motifs": ["old ox", "field road"],
        "forbidden_traits": ["modern architecture", "anime", "generated text"],
        "character_anchors": [{
            "anchor_id": "CHAR_C001",
            "anchor_type": "character_identity",
            "character_id": "C001",
            "name": "Fugui",
            "prompt_subject": "old Fugui, thin weathered farmer",
            "required_views": ["front", "three_quarter", "full_body", "expression_range", "wardrobe"],
            "invariants": ["thin face", "deep wrinkles"],
            "allowed_changes": ["expression", "pose"],
            "forbidden_changes": ["age change", "face change"],
            "wardrobe": ["faded shirt", "old trousers"],
            "wardrobe_state": "faded shirt and old trousers",
            "hat_state": "none",
        }],
        "scene_anchors": [],
        "object_anchors": [],
    }


def _foundation_input(profile_sha: str, *, master_approved: bool = True, missing_identity_master: bool = False, derived_views: list | None = None) -> dict:
    masters = [{
        "style_master_id": "STYLE_MASTER_RURAL_DAY",
        "book_id": "huozhe",
        "role": "rural_day",
        "image_path": "assets/generated/lookdev/LOOKDEV_LD01.png",
        "image_sha256": "0" * 64,
        "source_lookdev_task_id": "LOOKDEV_LD01",
        "visual_profile_sha256": profile_sha,
        "approved": master_approved,
        "approval_event_sha256": "",
        "style_guidance": "earth tones, restrained literary cinematic realism, weathered materials",
        "match": {"palette_ids": ["EARTH_DAY"], "lighting_ids": ["SUN_WORN"], "event_states": [], "time_contexts": []},
    }]
    identity_master = "ANCHOR_C001_FRONT" if not missing_identity_master else "ANCHOR_MISSING"
    return {
        "release_id": "r1",
        "book_id": "huozhe",
        "style_masters": masters,
        "character_identities": [{
            "character_id": "C001",
            "name": "Fugui",
            "same_person_lineage": True,
            "life_stages": [{
                "life_stage": "old",
                "identity_master_task_id": identity_master,
                "supporting_reference_task_ids": ["ANCHOR_C001_FULL_BODY"],
                "identity_invariants": ["thin face", "deep wrinkles"],
                "allowed_changes": ["expression", "pose"],
                "forbidden_changes": ["age change", "face change"],
                "wardrobe_states": ["faded shirt and old trousers"],
                "scope": {"chapters": ["3"], "event_states": ["alive_active", "death_aftermath"]},
                "approved": True,
                "apparent_age_range": "68-72",
                "derived_views": derived_views or [],
            }],
        }],
        "location_anchors": [],
    }


def build_foundation_project(base: Path, *, master_approved: bool = True, missing_identity_master: bool = False, skip_approve: bool = False, derived_views: list | None = None) -> Path:
    project = base / "warehouse/projects/pilot"
    _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "required"}})
    proj3 = project / "03_images_生成图片"
    proj3.mkdir(parents=True)
    _write_json(proj3 / "BOOK_VISUAL_PROFILE.json", _profile())
    profile_sha = _sha(proj3 / "BOOK_VISUAL_PROFILE.json")
    assets = []
    for task_id in ("ANCHOR_C001_FRONT", "ANCHOR_C001_FULL_BODY", "ANCHOR_C002_FRONT"):
        p = _png(project / "assets/generated/anchors" / f"{task_id}.png")
        assets.append({"task_id": task_id, "task_kind": "character_anchor", "path": f"assets/generated/anchors/{task_id}.png", "sha256": _sha(p)})
    for i in range(1, 3):
        task_id = f"LOOKDEV_LD0{i}"
        p = _png(project / "assets/generated/lookdev" / f"{task_id}.png")
        assets.append({"task_id": task_id, "task_kind": "lookdev", "path": f"assets/generated/lookdev/{task_id}.png", "sha256": _sha(p)})
    _write_json(proj3 / "VISUAL_ASSET_MANIFEST.json", {
        "schema_version": "visual-asset-manifest.v1",
        "release_id": "r1",
        "provider": "host-imagegen",
        "assets": assets,
        "registered_asset_count": len(assets),
        "last_registered_at": "2026-08-12T00:00:00+00:00",
    })
    fixture = _foundation_input(profile_sha, master_approved=master_approved, missing_identity_master=missing_identity_master, derived_views=derived_views)
    for master in fixture["style_masters"]:
        master["image_sha256"] = _sha(project / master["image_path"])
    build_visual_foundation(project, fixture)
    if master_approved and not skip_approve:
        approve_visual_foundation(project, release_id="r1", reviewer="tester", note="fixture")
    return project


def _task(task_id: str, *, contract: dict | None = None, event: str = "alive_active", chapters=("3",)) -> dict:
    prompt = f"production prompt {task_id}"
    return {
        "schema_version": "production-image-task.v1",
        "task_id": task_id,
        "scene_id": task_id,
        "release_id": "r1",
        "generation_lane": "host-imagegen",
        "generation_mode": "single",
        "identity_reference_task_ids": [],
        "style_reference_ids": ["REF_JANE_EYRE"],
        "palette_id": "EARTH_DAY",
        "lighting_id": "SUN_WORN",
        "visual_event_state": {"action_predicate": event, "cause_type": "", "is_reference_death": False, "actors": [], "participant_roles": [], "objects": [], "subject_state": "", "required_observable_evidence": [], "forbidden_contradictory_state": []},
        "source_beat_ids": ["B001"],
        "chapter_ids": list(chapters),
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "output_target": f"assets/generated/scenes/{task_id}.png",
        "reference_contract": contract,
    }


def _character_contract(character_id: str = "C001", life_stage: str = "", continuity: dict | None = None) -> dict:
    return {
        "schema_version": "visual-reference-contract.v1",
        "style": {"required": True},
        "identity": {"required_when_characters_present": True, "characters": [{"character_id": character_id, "life_stage": life_stage}]},
        "location": {},
        "continuity": continuity or {"use_previous_scene": False, "required": False, "reason": ""},
    }


def _symbolic_contract() -> dict:
    return {
        "schema_version": "visual-reference-contract.v1",
        "style": {"required": True},
        "identity": {"required_when_characters_present": True, "characters": []},
        "location": {},
        "continuity": {"use_previous_scene": False, "required": False, "reason": ""},
    }


def _write_director_inputs(project: Path, tasks: list[dict]) -> None:
    director = project / "05_director"
    director.mkdir(parents=True, exist_ok=True)
    (director / "IMAGE_TASKS.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in tasks),
        encoding="utf-8",
    )
    _write_json(director / "SHEET_MAP.json", {
        "schema_version": "sheet-map.v1",
        "sheet_groups": [],
        "single_tasks": [{"task_id": item["task_id"], "reason": "single"} for item in tasks],
        "sheet_task_count": 0,
        "single_task_count": len(tasks),
    })
    _write_json(director / "DIRECTOR_STAGE_MANIFEST.json", {"schema_version": "director-stage-manifest.v1", "release_id": "r1"})


def _load_jobs(project: Path) -> list[dict]:
    return [json.loads(line) for line in (project / "06_visual_production/GENERATION_JOBS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def _pack_for(jobs: list[dict], task_id: str) -> dict:
    for job in jobs:
        packs = job.get("reference_packs", {})
        if task_id in packs:
            return packs[task_id]
    raise AssertionError(f"no reference pack for {task_id}")


class VisualFoundationTests(unittest.TestCase):
    def test_t1_character_task_without_identity_image_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp), missing_identity_master=True, skip_approve=True)
            with self.assertRaises(VisualFoundationError):
                approve_visual_foundation(project, release_id="r1", reviewer="tester", note="fixture")

    def test_t2_stale_anchor_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            # corrupt the identity anchor after the foundation was approved
            _png(project / "assets/generated/anchors/ANCHOR_C001_FRONT.png", color=(1, 2, 3))
            with self.assertRaises((GenerationScheduleError, VisualFoundationError)):
                plan_generation_run(project)

    def test_t3_wrong_character_anchor_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract(character_id="C999"))])
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project)

    def test_t4_wrong_life_stage_anchor_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract(life_stage="middle_age"))])
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project)

    def test_t5_unapproved_style_master_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp), master_approved=False)
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            with self.assertRaises((GenerationScheduleError, VisualFoundationError)):
                plan_generation_run(project)

    def test_t6_stale_style_master_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            _png(project / "assets/generated/lookdev/LOOKDEV_LD01.png", color=(9, 9, 9))
            with self.assertRaises((GenerationScheduleError, VisualFoundationError)):
                plan_generation_run(project)

    def test_t7_provider_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_foundation_project(base)
            tasks = [_task("SCENE_T1", contract=_character_contract())]
            _write_director_inputs(project, tasks)
            plan_generation_run(project)
            source = base / "scene.png"
            _png(source, color=(41, 92, 133))
            director_manifest = project / "05_director/DIRECTOR_STAGE_MANIFEST.json"
            with mock.patch(
                "book_video_factory.production_visuals.registry.compile_director_stage",
                return_value=SimpleNamespace(manifest_path=director_manifest),
            ), mock.patch(
                "book_video_factory.production_visuals.registry._tasks",
                return_value={"SCENE_T1": tasks[0]},
            ):
                with self.assertRaisesRegex(SceneAssetError, "generation_lane"):
                    register_scene_asset(
                        project, task_id="SCENE_T1", source=source,
                        tool_call_id="imagegen_call_scene_000001", provider="gemini-web",
                        style_reference_ids=["REF_JANE_EYRE"], identity_reference_task_ids=[],
                    )

    def test_t8_runner_did_not_consume_required_reference_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_foundation_project(base)
            tasks = [_task("SCENE_T1", contract=_character_contract())]
            _write_director_inputs(project, tasks)
            plan_generation_run(project)
            jobs = _load_jobs(project)
            pack = _pack_for(jobs, "SCENE_T1")
            style_path = next(item["image_path"] for item in pack["references"] if item["role"] == "style_master")
            # runner consumed ONLY the style master, omitted the required identity master
            source = base / "scene.png"
            _png(source, color=(41, 92, 133))
            director_manifest = project / "05_director/DIRECTOR_STAGE_MANIFEST.json"
            with mock.patch(
                "book_video_factory.production_visuals.registry.compile_director_stage",
                return_value=SimpleNamespace(manifest_path=director_manifest),
            ), mock.patch(
                "book_video_factory.production_visuals.registry._tasks",
                return_value={"SCENE_T1": tasks[0]},
            ):
                with self.assertRaisesRegex(SceneAssetError, "do not match"):
                    register_scene_asset(
                        project, task_id="SCENE_T1", source=source,
                        tool_call_id="imagegen_call_scene_000001",
                        style_reference_ids=["REF_JANE_EYRE"], identity_reference_task_ids=[],
                        reference_inputs=[f"style_master:{style_path}"],
                        generation_attempt_id="attempt_scene_t1_000001",
                        generation_mode="reference_conditioned",
                        provider_receipt="host_call_scene_t1_ref_000001",
                    )

    def test_t9_unrelated_previous_frame_inheritance_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = build_foundation_project(base)
            prev = _task("SCENE_T0", contract=_character_contract(), event="death_aftermath")
            cur = _task(
                "SCENE_T1",
                contract={
                    "schema_version": "visual-reference-contract.v1",
                    "style": {"required": True},
                    "identity": {"required_when_characters_present": True, "characters": [{"character_id": "C001", "life_stage": ""}]},
                    "location": {},
                    "continuity": {"use_previous_scene": True, "required": True, "reason": "forced"},
                },
                event="alive_active",
            )
            # register the previous scene so the resolver attempts inheritance
            prev_png = _png(project / "assets/generated/scenes/SCENE_T0.png", color=(7, 7, 7))
            _write_json(project / "06_visual_production/SCENE_ASSET_MANIFEST.json", {
                "schema_version": "scene-asset-manifest.v1",
                "release_id": "r1",
                "assets": [{"task_id": "SCENE_T0", "path": "assets/generated/scenes/SCENE_T0.png", "sha256": _sha(prev_png)}],
                "registered_asset_count": 1,
                "task_count": 2,
            })
            _write_director_inputs(project, [prev, cur])
            with self.assertRaisesRegex(GenerationScheduleError, "unrelated previous-frame"):
                plan_generation_run(project)

    def test_t10_valid_minimum_reference_pack_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            result = plan_generation_run(project)
            self.assertEqual(result.status, "created")
            jobs = _load_jobs(project)
            pack = _pack_for(jobs, "SCENE_T1")
            roles = {item["role"] for item in pack["references"]}
            self.assertIn("style_master", roles)
            self.assertIn("identity_master", roles)
            for item in pack["references"]:
                self.assertTrue((project / item["image_path"]).is_file())
                self.assertEqual(_sha(project / item["image_path"]), item["image_sha256"])

    def test_t11_same_identity_across_two_scenes_resolves_same_anchor_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [
                _task("SCENE_T1", contract=_character_contract()),
                _task("SCENE_T2", contract=_character_contract()),
            ])
            plan_generation_run(project)
            jobs = _load_jobs(project)
            refs1 = _pack_for(jobs, "SCENE_T1")
            refs2 = _pack_for(jobs, "SCENE_T2")
            id1 = [item for item in refs1["references"] if item["role"] == "identity_master"][0]
            id2 = [item for item in refs2["references"] if item["role"] == "identity_master"][0]
            self.assertEqual(id1["image_path"], id2["image_path"])
            self.assertEqual(id1["image_sha256"], id2["image_sha256"])

    def test_t12_symbolic_image_without_story_character_has_no_identity_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_symbolic_contract())])
            plan_generation_run(project)
            jobs = _load_jobs(project)
            pack = _pack_for(jobs, "SCENE_T1")
            roles = {item["role"] for item in pack["references"]}
            self.assertNotIn("identity_master", roles)
            self.assertIn("style_master", roles)



    def test_f1_required_policy_without_foundation_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "warehouse/projects/pilot"
            _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "required"}})
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            with self.assertRaises(GenerationScheduleError):
                plan_generation_run(project)

    def test_f2_foundation_exists_without_approval_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp), skip_approve=True)
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            with self.assertRaises((GenerationScheduleError, VisualFoundationError)):
                plan_generation_run(project)

    def test_f3_stale_approval_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            # corrupt a foundation-controlled asset after approval
            _png(project / "assets/generated/anchors/ANCHOR_C001_FRONT.png", color=(3, 4, 5))
            with self.assertRaises((GenerationScheduleError, VisualFoundationError)):
                plan_generation_run(project)

    def test_f4_valid_approved_foundation_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            _write_director_inputs(project, [_task("SCENE_T1", contract=_character_contract())])
            result = plan_generation_run(project)
            self.assertEqual(result.status, "created")

    def test_f5_legacy_mode_preserves_previous_behavior_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "warehouse/projects/pilot"
            _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "legacy"}})
            # no foundation at all: legacy mode must keep the previous scheduler behavior
            _write_director_inputs(project, [_task("SCENE_T1", contract=None)])
            result = plan_generation_run(project)
            self.assertEqual(result.status, "created")
            jobs = _load_jobs(project)
            self.assertNotIn("reference_packs", jobs[0])



    def test_f0_required_policy_blocks_director_without_foundation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "required"}})
            audio_dir = project / "04_audio"
            audio_dir.mkdir()
            _write_json(audio_dir / "AUDIO_STAGE_MANIFEST.json", {"release_id": "r1"})
            with mock.patch(
                "book_video_factory.director_stage.compiler.audio_stage_status",
                return_value="ready_for_image_task_planning",
            ):
                with self.assertRaisesRegex(DirectorStageError, "visual_foundation_policy=required"):
                    _expected(project)


    def _load_manifest(self, project, name):
        import book_video_factory.visual_foundation.manifests as m
        rel = {"style": m.STYLE_MASTER_REL, "identity": m.CHARACTER_IDENTITY_REL, "location": m.LOCATION_ANCHOR_REL}[name]
        import json as _j
        return project / rel, _j.loads((project / rel).read_text(encoding="utf-8"))

    def _write_manifest(self, path, payload):
        import json as _j
        path.write_text(_j.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_h1_style_master_sourced_from_character_anchor_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            path, payload = self._load_manifest(project, "style")
            payload["items"][0]["source_lookdev_task_id"] = "ANCHOR_C001_FRONT"
            self._write_manifest(path, payload)
            with self.assertRaisesRegex(VisualFoundationError, "role hygiene"):
                verify_visual_foundation(project, "r1")

    def test_h2_style_master_with_recurring_character_reference_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            import json as _j
            lookdev_path = project / "03_images_生成图片/LOOKDEV_TASKS.jsonl"
            lookdev_path.write_text(_j.dumps({"task_id": "LOOKDEV_LD01", "anchor_refs": ["CHAR_C001"]}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(VisualFoundationError, "character-neutral"):
                verify_visual_foundation(project, "r1")

    def test_h3_identity_master_of_wrong_character_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            path, payload = self._load_manifest(project, "identity")
            payload["items"][0]["life_stages"][0]["identity_master_task_id"] = "ANCHOR_C002_FRONT"
            self._write_manifest(path, payload)
            with self.assertRaisesRegex(VisualFoundationError, "not 'C001'"):
                verify_visual_foundation(project, "r1")

    def test_h4_identity_root_not_front_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp))
            path, payload = self._load_manifest(project, "identity")
            payload["items"][0]["life_stages"][0]["identity_master_task_id"] = "ANCHOR_C001_FULL_BODY"
            self._write_manifest(path, payload)
            with self.assertRaisesRegex(VisualFoundationError, "must be a FRONT view"):
                verify_visual_foundation(project, "r1")

    def test_h5_derived_view_with_wrong_root_sha_blocks_and_matching_passes(self) -> None:
        derived = lambda root_sha: [{
            "view": "full_body", "task_id": "ANCHOR_C001_FULL_BODY",
            "source_root_sha256": root_sha, "generation_attempt_id": "attempt_h5",
            "output_sha256": "1" * 64,
        }]
        with tempfile.TemporaryDirectory() as temp:
            project = build_foundation_project(Path(temp), derived_views=derived("0" * 64), skip_approve=True)
            with self.assertRaisesRegex(VisualFoundationError, "source root SHA"):
                verify_visual_foundation(project, "r1")
        with tempfile.TemporaryDirectory() as temp:
            # empty source root SHA is accepted as a valid derived-view record
            project = build_foundation_project(Path(temp), derived_views=derived(""))
            verify_visual_foundation(project, "r1")

if __name__ == "__main__":
    unittest.main()
