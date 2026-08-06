from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase2_fixture_factory import write_json
from phase3_fixture_factory import build_phase3_project
from book_video_factory.reference_visuals.catalog import load_reference_catalog
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    register_visual_asset,
    verify_visual_asset_manifest,
)
from book_video_factory.visual_stage.compiler import compile_visual_stage


def make_png(path: Path, color=(70, 90, 110), size=(1920, 1080)) -> Path:
    with Image.new("RGB", size, color) as image:
        image.save(path)
    return path


def tasks(project: Path) -> list[dict]:
    result=[]
    for name in ("ANCHOR_TASKS.jsonl", "LOOKDEV_TASKS.jsonl"):
        for line in (project / "03_images_生成图片" / name).read_text(encoding="utf-8").splitlines():
            if line.strip(): result.append(json.loads(line))
    return result


def register_task(project: Path, task: dict, source: Path, call_id: str = "imagegen_call_123456"):
    return register_visual_asset(
        project,
        task_id=task["task_id"],
        source=source,
        prompt_sha256=task["prompt_sha256"],
        tool_call_id=call_id,
        style_reference_ids=task["style_reference_ids"],
        identity_reference_task_ids=task["identity_dependency_task_ids"],
    )


class Phase3AssetRegistryTests(unittest.TestCase):
    def prepare(self, base: Path):
        project, payload, _, _, _ = build_phase3_project(base)
        input_path = base / "visual-input.json"; write_json(input_path, payload)
        compile_visual_stage(project, input_path)
        return project, tasks(project)

    def test_registers_real_image_with_hash_diagnostics_and_pending_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base)
            task=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_FRONT")
            source=make_png(base/"front.png")
            result=register_task(project, task, source)
            self.assertEqual(result.status,"created")
            asset=result.asset
            self.assertEqual((asset["width"],asset["height"]),(1920,1080))
            self.assertEqual(asset["provider"],"host-imagegen")
            self.assertEqual(asset["semantic_review_status"],"pending")
            self.assertEqual(asset["reality_review_status"],"pending")
            self.assertEqual(asset["human_review_status"],"pending")
            self.assertEqual(
                [item["reference_id"] for item in asset["style_reference_evidence"]],
                task["style_reference_ids"],
            )
            self.assertEqual(asset["identity_reference_evidence"], [])
            self.assertTrue((project/asset["path"]).is_file())
            manifest=json.loads((project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["assets"]),1)

    def test_rejects_unknown_task_prompt_mismatch_and_placeholder_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base); source=make_png(base/"a.png")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"unknown task"):
                register_visual_asset(project,task_id="UNKNOWN",source=source,prompt_sha256="0"*64,tool_call_id="imagegen_call_123",style_reference_ids=[],identity_reference_task_ids=[])
            task=task_list[0]
            with self.assertRaisesRegex(VisualAssetRegistrationError,"prompt_sha256"):
                register_visual_asset(project,task_id=task["task_id"],source=source,prompt_sha256="0"*64,tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])
            with self.assertRaisesRegex(VisualAssetRegistrationError,"tool_call_id"):
                register_visual_asset(project,task_id=task["task_id"],source=source,prompt_sha256=task["prompt_sha256"],tool_call_id="fake",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])

    def test_rejects_wrong_dimensions_corrupt_image_and_unsupported_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base); task=task_list[0]
            with self.assertRaisesRegex(VisualAssetRegistrationError,"1920x1080"):
                register_visual_asset(project,task_id=task["task_id"],source=make_png(base/"small.png",size=(100,100)),prompt_sha256=task["prompt_sha256"],tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])
            corrupt=base/"corrupt.png"; corrupt.write_bytes(b"not an image")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"decodable"):
                register_visual_asset(project,task_id=task["task_id"],source=corrupt,prompt_sha256=task["prompt_sha256"],tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])
            wrong=base/"image.gif"; Image.new("RGB",(1920,1080)).save(wrong)
            with self.assertRaisesRegex(VisualAssetRegistrationError,"PNG"):
                register_visual_asset(project,task_id=task["task_id"],source=wrong,prompt_sha256=task["prompt_sha256"],tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])

    def test_dependencies_must_be_registered_before_dependent_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base)
            task=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_THREE_QUARTER")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"dependency"):
                register_visual_asset(project,task_id=task["task_id"],source=make_png(base/"q.png"),prompt_sha256=task["prompt_sha256"],tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])

    def test_duplicate_task_or_duplicate_image_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base)
            front=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_FRONT")
            scene=next(item for item in task_list if item["task_kind"] == "scene_anchor")
            source=make_png(base/"same.png")
            register_task(project, front, source, "imagegen_call_123")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"already registered"):
                register_task(project, front, source, "imagegen_call_456")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"duplicate image"):
                register_task(project, scene, source, "imagegen_call_789")

    def test_gold_reference_contact_sheet_cannot_be_registered_as_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base); task=task_list[0]
            reference=load_reference_catalog().entries[0].absolute_path
            with self.assertRaisesRegex(VisualAssetRegistrationError,"gold reference"):
                register_visual_asset(project,task_id=task["task_id"],source=reference,prompt_sha256=task["prompt_sha256"],tool_call_id="imagegen_call_123",style_reference_ids=task["style_reference_ids"],identity_reference_task_ids=task["identity_dependency_task_ids"])


    def test_reference_declarations_must_match_task_and_are_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base)
            front=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_FRONT")
            source=make_png(base/"front.png")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"style reference"):
                register_visual_asset(
                    project,task_id=front["task_id"],source=source,
                    prompt_sha256=front["prompt_sha256"],tool_call_id="imagegen_call_123",
                    style_reference_ids=front["style_reference_ids"][:-1],identity_reference_task_ids=[],
                )
            register_task(project,front,source,"imagegen_call_124")
            quarter=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_THREE_QUARTER")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"identity reference"):
                register_visual_asset(
                    project,task_id=quarter["task_id"],source=make_png(base/"quarter.png",color=(80,100,120)),
                    prompt_sha256=quarter["prompt_sha256"],tool_call_id="imagegen_call_125",
                    style_reference_ids=quarter["style_reference_ids"],identity_reference_task_ids=[],
                )
            result=register_task(project,quarter,base/"quarter.png","imagegen_call_126")
            evidence=result.asset["identity_reference_evidence"]
            self.assertEqual([item["task_id"] for item in evidence],[front["task_id"]])
            self.assertEqual(evidence[0]["sha256"],result.asset["identity_reference_evidence"][0]["sha256"])
            self.assertTrue((project/evidence[0]["path"]).is_file())


    def test_manifest_reference_evidence_and_unknown_fields_cannot_self_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base)
            front=next(item for item in task_list if item["task_id"] == "ANCHOR_C001_FRONT")
            register_task(project,front,make_png(base/"front.png"),"imagegen_call_201")
            manifest_path=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
            original=json.loads(manifest_path.read_text(encoding="utf-8"))

            payload=json.loads(json.dumps(original))
            payload["assets"][0]["style_reference_evidence"][0]["sha256"]="0"*64
            manifest_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"style reference evidence"):
                verify_visual_asset_manifest(project)

            payload=json.loads(json.dumps(original))
            payload["assets"][0]["human_approved"]=True
            manifest_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"fields"):
                verify_visual_asset_manifest(project)

            payload=json.loads(json.dumps(original))
            payload["untrusted_override"]={"approved":True}
            manifest_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(VisualAssetRegistrationError,"fields"):
                verify_visual_asset_manifest(project)

    def test_manifest_write_failure_leaves_no_asset_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project, task_list=self.prepare(base); task=task_list[0]
            source=make_png(base/"front.png")
            with mock.patch("book_video_factory.visual_stage.asset_registry.os.replace",side_effect=OSError("injected registry failure")):
                with self.assertRaisesRegex(VisualAssetRegistrationError,"injected"):
                    register_task(project, task, source, "imagegen_call_123")
            self.assertFalse((project/task["output_target"]).exists())
            self.assertFalse((project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json").exists())


if __name__=="__main__": unittest.main()
