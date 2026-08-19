from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO = _repository_root()

from phase2_fixture_factory import write_json
from phase3_fixture_factory import build_phase3_input, build_phase3_project
from test_phase3_visual_approval import decision_for
from test_phase3_visual_review import fake_contact_sheet, load_tasks, register_all
from book_video_factory.manifests import sha256_file
from book_video_factory.reference_visuals.catalog import (
    ReferenceCatalogError,
    default_catalog_path,
    load_reference_catalog,
)
from book_video_factory.visual_stage.approval import (
    VisualApprovalError,
    approve_visual_stage,
    verify_visual_approval,
    visual_stage_next_status,
)
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    register_visual_asset,
    verify_visual_asset_manifest,
)
from book_video_factory.visual_stage.compiler import (
    VisualStageCompileError,
    VisualStageConflict,
    _required_recorded_at,
    compile_visual_stage,
)
from book_video_factory.visual_stage.contracts import (
    VisualStageContractError,
    validate_visual_stage_input,
)
from book_video_factory.visual_stage.review import VisualReviewError, build_visual_review


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PHASE0_SCANNER = load_script(REPO / "scripts/verify_phase0_architecture.py", "phase0_mutation_scanner")
PHASE3_SCANNER = load_script(REPO / "scripts/verify_phase3_visual_stage.py", "phase3_mutation_scanner")


class Phase3AdversarialMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        base = Path(cls._temp.name)
        project, payload, _, _, _ = build_phase3_project(base / "source")
        visual_input = base / "visual-input.json"
        write_json(visual_input, payload)
        compile_visual_stage(project, visual_input)
        cls.payload = payload
        cls.compiled = base / "states/compiled"
        cls.compiled.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project, cls.compiled)

        tasks = load_tasks(project)
        (base / "generated").mkdir(parents=True, exist_ok=True)
        register_all(base / "generated", project, tasks)
        cls.assets = base / "states/assets"
        shutil.copytree(project, cls.assets)

        with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet", side_effect=fake_contact_sheet):
            build_visual_review(project)
        cls.review = base / "states/review"
        shutil.copytree(project, cls.review)

        decision_path = base / "decision.json"
        write_json(decision_path, decision_for(project))
        with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet", side_effect=fake_contact_sheet):
            approve_visual_stage(project, release_id="r1", reviewer="mutation-fixture", decision_path=decision_path)
        cls.approved = base / "states/approved"
        shutil.copytree(project, cls.approved)
        cls.characters = json.loads((project / "02_story_script_故事脚本/HBG_BRIDGE_INPUT.json").read_text(encoding="utf-8"))["characters"]
        cls.catalog = load_reference_catalog()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def clone(self, base: Path, state: str = "compiled") -> Path:
        source = getattr(self, state)
        project_id = json.loads((source / "project.json").read_text(encoding="utf-8"))["project_id"]
        target = base / "warehouse/projects" / project_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        return target

    def validate(self, payload: dict) -> dict:
        return validate_visual_stage_input(payload, phase2_characters=self.characters, catalog=self.catalog)

    def image(self, path: Path, *, size: tuple[int, int] = (1920, 1080), color=(23, 71, 119), fmt="PNG") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, color) as image:
            image.save(path, format=fmt)
        return path

    def register(self, project: Path, task: dict, image: Path, *, call_id: str = "imagegen_mutation_000001"):
        return register_visual_asset(
            project,
            task_id=task["task_id"],
            source=image,
            prompt_sha256=task["prompt_sha256"],
            tool_call_id=call_id,
            style_reference_ids=task["style_reference_ids"],
            identity_reference_task_ids=task["identity_dependency_task_ids"],
        )

    def tasks(self, project: Path) -> list[dict]:
        return load_tasks(project)

    def clone_reference_repo(self, base: Path) -> tuple[Path, Path]:
        root = base / "repo"
        (root / "book_video_factory/config").mkdir(parents=True)
        shutil.copytree(REPO / "book_video_factory/config/visuals", root / "book_video_factory/config/visuals")
        (root / "docs/visual-upgrade-input").mkdir(parents=True)
        shutil.copytree(REPO / "docs/visual-upgrade-input/contact_sheets", root / "docs/visual-upgrade-input/contact_sheets")
        return root, root / "book_video_factory/config/visuals/gold-reference-catalog-v1.json"

    # Reference evidence attacks.
    def test_01_catalog_contact_hash_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root, catalog = self.clone_reference_repo(Path(t)); data=json.loads(catalog.read_text(encoding="utf-8")); data["entries"][0]["sha256"]="0"*64; write_json(catalog,data)
            with self.assertRaises(ReferenceCatalogError): load_reference_catalog(catalog,repository_root=root)

    def test_02_catalog_reference_path_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root,catalog=self.clone_reference_repo(Path(t)); data=json.loads(catalog.read_text(encoding="utf-8")); data["entries"][0]["path"]="../outside.jpg"; write_json(catalog,data)
            with self.assertRaises(ReferenceCatalogError): load_reference_catalog(catalog,repository_root=root)

    def test_03_catalog_profile_hash_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root,catalog=self.clone_reference_repo(Path(t)); data=json.loads(catalog.read_text(encoding="utf-8")); data["entries"][0]["profile_sha256"]="1"*64; write_json(catalog,data)
            with self.assertRaises(ReferenceCatalogError): load_reference_catalog(catalog,repository_root=root)

    def test_04_catalog_kernel_hash_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root,catalog=self.clone_reference_repo(Path(t)); data=json.loads(catalog.read_text(encoding="utf-8")); data["kernel_sha256"]="2"*64; write_json(catalog,data)
            with self.assertRaises(ReferenceCatalogError): load_reference_catalog(catalog,repository_root=root)

    # Visual contract attacks.
    def test_05_style_reference_cannot_be_used_as_identity_anchor(self):
        payload=copy.deepcopy(self.payload); payload["lookdev_tasks"][0]["anchor_refs"]=["REF_JANE_EYRE"]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_06_wrong_release_script_binding_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["release_text_sha256"]="0"*64
        with self.assertRaises(VisualStageCompileError):
            with tempfile.TemporaryDirectory() as t:
                project=self.clone(Path(t)); p=Path(t)/"input.json"; write_json(p,payload); compile_visual_stage(project,p)

    def test_07_missing_character_anchor_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["character_anchors"]=[]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_08_duplicate_anchor_id_is_blocked(self):
        payload=copy.deepcopy(self.payload); duplicate=copy.deepcopy(payload["scene_anchors"][0]); duplicate["anchor_id"]=payload["character_anchors"][0]["anchor_id"]; payload["scene_anchors"].append(duplicate)
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_09_eleven_lookdev_tasks_are_blocked(self):
        payload=copy.deepcopy(self.payload); payload["lookdev_tasks"].pop()
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_10_thirteen_lookdev_tasks_are_blocked(self):
        payload=copy.deepcopy(self.payload); extra=copy.deepcopy(payload["lookdev_tasks"][0]); extra["task_id"]="LD13"; payload["lookdev_tasks"].append(extra)
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_11_missing_required_lookdev_category_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["lookdev_tasks"][0]["category"]="full_body"
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_12_fake_approved_anchor_status_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["character_anchors"][0]["anchor_status"]="approved"
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_13_unknown_visual_input_field_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["hidden_override"]="yes"
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_14_global_only_envelope_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["palette_profiles"][0].pop("diagnostic_envelope")
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_15_absolute_path_prompt_material_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["lookdev_tasks"][0]["subject"]="/Users/person/private/reference.png"
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_16_whitespace_mutated_id_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["character_anchors"][0]["anchor_id"]=" CHAR_C001"
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_17_unknown_risk_flag_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["lookdev_tasks"][0]["risk_flags"]=["almost_safe_hands"]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_18_unknown_style_reference_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["style_reference_ids"]=["REF_UNKNOWN"]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_19_missing_object_anchor_is_blocked(self):
        payload=copy.deepcopy(self.payload); payload["object_anchors"]=[]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    def test_20_incomplete_character_views_are_blocked(self):
        payload=copy.deepcopy(self.payload); payload["character_anchors"][0]["required_views"]=["front"]
        with self.assertRaises(VisualStageContractError): self.validate(payload)

    # Compiler and prompt attacks.
    def test_21_prompt_hash_substitution_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; image=self.image(base/"real.png")
            with self.assertRaises(VisualAssetRegistrationError):
                register_visual_asset(project,task_id=task["task_id"],source=image,prompt_sha256="0"*64,tool_call_id="imagegen_mutation_000021",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])

    def test_22_profile_task_and_self_reported_hash_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); profile=project/"03_images_生成图片/BOOK_VISUAL_PROFILE.json"; data=json.loads(profile.read_text(encoding="utf-8")); data["book_look"]["period"]="伪造时代"; write_json(profile,data)
            manifest_path=project/"03_images_生成图片/VISUAL_STAGE_MANIFEST.json"; manifest=json.loads(manifest_path.read_text(encoding="utf-8")); manifest["output_hashes"]["03_images_生成图片/BOOK_VISUAL_PROFILE.json"]=sha256_file(profile); write_json(manifest_path,manifest)
            with self.assertRaises(VisualStageConflict): compile_visual_stage(project,project/"03_images_生成图片/VISUAL_STAGE_INPUT.json")

    def test_23_json_key_reorder_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); source=project/"03_images_生成图片/VISUAL_STAGE_INPUT.json"; data=json.loads(source.read_text(encoding="utf-8")); reordered={k:data[k] for k in reversed(list(data))}; p=base/"reordered.json"; write_json(p,reordered)
            self.assertEqual(compile_visual_stage(project,p).status,"unchanged")

    def test_24_missing_phase2_recorded_at_is_blocked(self):
        with self.assertRaises(VisualStageCompileError): _required_recorded_at({})

    def test_25_wrong_bridge_digest_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); payload=copy.deepcopy(self.payload); payload["hbg_bridge_digest"]="0"*64; p=base/"input.json"; write_json(p,payload)
            with self.assertRaises(VisualStageCompileError): compile_visual_stage(project,p)

    # Asset registration attacks.
    def test_26_wrong_dimensions_are_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; image=self.image(base/"small.png",size=(1024,1024))
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,image)

    def test_27_duplicate_generated_hash_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); tasks=self.tasks(project); image=self.image(base/"same.png"); self.register(project,tasks[0],image)
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,tasks[1],image,call_id="imagegen_mutation_000027")

    def test_28_reference_catalog_image_cannot_be_registered_as_output(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t)); task=self.tasks(project)[0]
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,self.catalog.entries[0].absolute_path)

    def test_29_missing_registered_asset_blocks_review(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets"); manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"; data=json.loads(manifest.read_text(encoding="utf-8")); removed=data["assets"].pop(); data["registered_asset_count"]=len(data["assets"]); data["last_registered_at"]=data["assets"][-1]["registered_at"]; write_json(manifest,data); (project/removed["path"]).unlink()
            with self.assertRaises(VisualReviewError): build_visual_review(project)

    def test_30_style_reference_evidence_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets"); manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"; data=json.loads(manifest.read_text(encoding="utf-8")); data["assets"][0]["style_reference_evidence"][0]["sha256"]="0"*64; write_json(manifest,data)
            with self.assertRaises(VisualAssetRegistrationError): verify_visual_asset_manifest(project)

    def test_31_identity_reference_evidence_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets"); manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"; data=json.loads(manifest.read_text(encoding="utf-8")); target=next(x for x in data["assets"] if x["identity_reference_evidence"]); target["identity_reference_evidence"][0]["sha256"]="1"*64; write_json(manifest,data)
            with self.assertRaises(VisualAssetRegistrationError): verify_visual_asset_manifest(project)

    def test_32_unknown_asset_manifest_field_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets"); manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"; data=json.loads(manifest.read_text(encoding="utf-8")); data["forged_approval"]=True; write_json(manifest,data)
            with self.assertRaises(VisualAssetRegistrationError): verify_visual_asset_manifest(project)

    def test_33_unknown_provider_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets"); manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"; data=json.loads(manifest.read_text(encoding="utf-8")); data["provider"]="private-api"; write_json(manifest,data)
            with self.assertRaises(VisualAssetRegistrationError): verify_visual_asset_manifest(project)

    def test_34_fabricated_placeholder_call_id_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; image=self.image(base/"real.png")
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,image,call_id="placeholder")

    def test_35_fake_image_extension_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; image=self.image(base/"real.jpg",fmt="JPEG")
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,image)

    def test_36_corrupt_png_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; image=base/"bad.png"; image.write_bytes(b"not png")
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,image)

    def test_37_symlinked_output_path_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base); task=self.tasks(project)[0]; outside=base/"outside"; outside.mkdir(); target_parent=project/Path(task["output_target"]).parent; shutil.rmtree(target_parent); target_parent.parent.mkdir(parents=True,exist_ok=True)
            try:
                target_parent.symlink_to(outside,target_is_directory=True)
            except OSError as error:
                if getattr(error,"winerror",None)==1314: self.skipTest("Windows symlink privilege is unavailable")
                raise
            image=self.image(base/"real.png")
            with self.assertRaises(VisualAssetRegistrationError): self.register(project,task,image)

    # Review and approval attacks.
    def test_38_contact_sheet_substitution_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"review"); (project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg").write_bytes(b"substitute")
            with self.assertRaises(VisualReviewError): build_visual_review(project)

    def test_39_coordinated_contact_report_stage_tamper_is_blocked_before_approval(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base,"review"); contact=project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg"; self.image(contact,size=(1948,834),color=(200,20,30),fmt="JPEG")
            report_path=project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json"; report=json.loads(report_path.read_text(encoding="utf-8")); report["contact_sheet"]["sha256"]=sha256_file(contact); report["contact_sheet"]["bytes"]=contact.stat().st_size; write_json(report_path,report)
            stage_path=project/report["stage_manifest_path"]; stage=json.loads(stage_path.read_text(encoding="utf-8"));
            for item in stage["outputs"]:
                target=project/item["path"]; item["sha256"]=sha256_file(target); item["bytes"]=target.stat().st_size
            write_json(stage_path,stage); decision=base/"decision.json"; write_json(decision,decision_for(project))
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                with self.assertRaises(VisualApprovalError): approve_visual_stage(project,release_id="r1",reviewer="human",decision_path=decision)

    def test_40_report_checklist_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"review"); report=project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json"; data=json.loads(report.read_text(encoding="utf-8")); data["semantic_review"]["required_checks"][0]="forged"; write_json(report,data)
            with self.assertRaises(VisualReviewError): build_visual_review(project)

    def test_41_stage_manifest_producer_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"review"); report=json.loads((project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json").read_text(encoding="utf-8")); stage=project/report["stage_manifest_path"]; data=json.loads(stage.read_text(encoding="utf-8")); data["producer"]={"tool":"attacker"}; write_json(stage,data)
            with self.assertRaises(VisualReviewError): build_visual_review(project)

    def test_42_hbg_contact_script_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base,"assets"); fake_root=base/"repo"; shutil.copytree(REPO/"vendor",fake_root/"vendor"); script=fake_root/"vendor/hbg-life-simulation/scripts/make_contact_sheet.sh"; script.write_text(script.read_text(encoding="utf-8")+"\n# tamper\n",encoding="utf-8")
            with mock.patch("book_video_factory.visual_stage.review.repository_root",return_value=fake_root):
                with self.assertRaises(VisualReviewError): build_visual_review(project)

    def test_43_approval_without_semantic_checks_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base,"review"); decision=decision_for(project); decision["semantic_checks"].pop(); p=base/"decision.json"; write_json(p,decision)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                with self.assertRaises(VisualApprovalError): approve_visual_stage(project,release_id="r1",reviewer="human",decision_path=p)

    def test_44_approval_without_reality_pass_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base,"review"); decision=decision_for(project); decision["reality_checks"][0]["result"]="fail"; p=base/"decision.json"; write_json(p,decision)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                with self.assertRaises(VisualApprovalError): approve_visual_stage(project,release_id="r1",reviewer="human",decision_path=p)

    def test_45_approval_subject_omission_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"approved"); approval=project/"03_images_生成图片/ANCHOR_APPROVAL.json"; data=json.loads(approval.read_text(encoding="utf-8")); data["subjects"].pop(); write_json(approval,data)
            with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r1")

    def test_46_approval_event_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"approved"); approval=json.loads((project/"03_images_生成图片/ANCHOR_APPROVAL.json").read_text(encoding="utf-8")); event=project/approval["approval_event"]["path"]; event.write_text("{}",encoding="utf-8")
            with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r1")

    def test_47_post_approval_asset_tamper_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"approved"); manifest=json.loads((project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json").read_text(encoding="utf-8")); asset=project/manifest["assets"][0]["path"]; asset.write_bytes(asset.read_bytes()+b"tamper")
            with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r1")

    def test_48_stale_release_approval_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"approved")
            with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r2")

    def test_49_rejected_approval_cannot_be_treated_as_approved(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project=self.clone(base,"review"); decision=decision_for(project,"rejected"); p=base/"decision.json"; write_json(p,decision)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet): approve_visual_stage(project,release_id="r1",reviewer="human",decision_path=p)
            self.assertFalse(verify_visual_approval(project,"r1").approved)

    def test_50_phase_ordering_remains_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"review"); self.assertEqual(visual_stage_next_status(project,"r1"),"blocked_by_visual_approval")

    # Transaction, architecture, and scope attacks.
    def test_51_compile_atomic_write_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t); project, payload, _, _, _=build_phase3_project(base); p=base/"input.json"; write_json(p,payload)
            with mock.patch("book_video_factory.visual_stage.compiler.write_stage_manifest",side_effect=RuntimeError("injected")):
                with self.assertRaises(RuntimeError): compile_visual_stage(project,p)
            self.assertFalse((project/"03_images_生成图片/VISUAL_STAGE_MANIFEST.json").exists())

    def test_52_review_stage_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as t:
            project=self.clone(Path(t),"assets")
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet), mock.patch("book_video_factory.visual_stage.review.write_stage_manifest",side_effect=RuntimeError("injected")):
                with self.assertRaises(RuntimeError): build_visual_review(project)
            self.assertFalse((project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json").exists())

    def test_53_runtime_divergence_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); (root/"book_video_factory").mkdir(); (root/"skill/runtime/book_video_factory").mkdir(parents=True); (root/"book_video_factory/a.py").write_text("x=1\n"); (root/"skill/runtime/book_video_factory/a.py").write_text("x=2\n")
            self.assertTrue(any(x["check_id"]=="runtime_divergence" for x in PHASE0_SCANNER.scan_runtime_divergence(root)))

    def test_54_phase3_false_audio_video_completion_claim_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); readme=root/"README.md"; readme.write_text("Phase 3 已完成 Edge TTS、VTT 和最终 MP4。\n",encoding="utf-8")
            self.assertTrue(any(x["check_id"]=="phase3_scope_claim" for x in PHASE3_SCANNER.scan_scope_claims(root,[readme])))

    def test_55_private_image_api_client_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); path=root/"book_video_factory/src/book_video_factory/visual_stage/client.py"; path.parent.mkdir(parents=True); path.write_text("import requests\nrequests.post('https://api.openai.com/v1/images/generations')\n")
            self.assertTrue(any(x["check_id"]=="forbidden_image_api_client" for x in PHASE3_SCANNER.scan_forbidden_image_clients(root)))


if __name__ == "__main__":
    unittest.main()
