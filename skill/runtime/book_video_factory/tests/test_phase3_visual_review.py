from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase2_fixture_factory import write_json
from phase3_fixture_factory import build_phase3_project
from book_video_factory.manifests import sha256_file
from book_video_factory.visual_stage.asset_registry import register_visual_asset
from book_video_factory.visual_stage.compiler import compile_visual_stage
from book_video_factory.visual_stage.review import (
    VisualReviewError,
    _path_for_bash,
    build_visual_review,
)


def load_tasks(project: Path) -> list[dict]:
    items=[]
    for name in ("ANCHOR_TASKS.jsonl", "LOOKDEV_TASKS.jsonl"):
        for line in (project/"03_images_生成图片"/name).read_text(encoding="utf-8").splitlines():
            if line.strip(): items.append(json.loads(line))
    return items


def prepare(base: Path) -> tuple[Path,list[dict]]:
    project,payload,_,_,_=build_phase3_project(base)
    source=base/"visual-input.json"; write_json(source,payload)
    compile_visual_stage(project,source)
    return project,load_tasks(project)


def register_all(base: Path,project: Path,tasks: list[dict], *, omit: str|None=None) -> None:
    pending={item["task_id"]:item for item in tasks if item["task_id"] != omit}
    index=1
    while pending:
        progressed=False
        registered=set()
        manifest=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
        if manifest.is_file():
            registered={item["task_id"] for item in json.loads(manifest.read_text(encoding="utf-8"))["assets"]}
        for task_id,task in list(pending.items()):
            if not set(task["depends_on_task_ids"]).issubset(registered): continue
            color=((index*31)%251,(index*67)%251,(index*109)%251)
            image=base/f"{task_id}.png"
            with Image.new("RGB",(1920,1080),color) as generated:
                generated.save(image)
            register_visual_asset(
                project,task_id=task_id,source=image,
                prompt_sha256=task["prompt_sha256"],tool_call_id=f"imagegen_call_{index:06d}",
                style_reference_ids=task["style_reference_ids"],
                identity_reference_task_ids=task["identity_dependency_task_ids"],
            )
            del pending[task_id]; index+=1; progressed=True
        if not progressed: raise AssertionError("task dependencies cannot be resolved")


def fake_contact_sheet(
    _script: Path,
    output: Path,
    _images: list[Path],
    _root: Path,
    *,
    columns: int = 4,
) -> None:
    if columns not in {4, 5}:
        raise AssertionError("Phase 3 review supports four-column LookDev and five-column anchor sheets")
    with Image.new("RGB", (1948, 834), (40, 50, 60)) as image:
        image.save(output, format="JPEG")


class Phase3VisualReviewTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Git Bash path mapping is Windows-specific")
    def test_windows_paths_are_mapped_for_the_git_bash_adapter(self) -> None:
        self.assertEqual(_path_for_bash(Path("I:/repo/image.jpg")), "I:/repo/image.jpg")

    @classmethod
    def setUpClass(cls) -> None:
        cls._fixture_temp = tempfile.TemporaryDirectory()
        base = Path(cls._fixture_temp.name)
        project, tasks = prepare(base)
        register_all(base, project, tasks)
        cls._fixture_project = project
        cls._fixture_tasks = tasks

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fixture_temp.cleanup()

    def clone_project(self, base: Path) -> tuple[Path, list[dict]]:
        project = base / "warehouse" / "projects" / self._fixture_project.name
        project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self._fixture_project, project)
        return project, list(self._fixture_tasks)

    def test_builds_hbg_contact_sheet_and_pending_review_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,tasks=self.clone_project(base)
            result=build_visual_review(project)
            self.assertEqual(result.status,"created")
            self.assertTrue(result.contact_sheet_path.is_file())
            self.assertTrue(result.report_path.is_file())
            report=json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["generator"]["script"],"vendor/hbg-life-simulation/scripts/make_contact_sheet.sh")
            self.assertEqual(len(report["lookdev_assets"]),12)
            self.assertEqual([item["task_id"] for item in report["lookdev_assets"]],
                [item["task_id"] for item in tasks if item["task_kind"]=="lookdev"])
            self.assertEqual(report["human_review_status"],"pending")
            self.assertEqual(report["semantic_review"]["status"],"pending")
            self.assertEqual(report["reality_review"]["status"],"pending")
            self.assertEqual(report["contact_sheet"]["sha256"],sha256_file(result.contact_sheet_path))
            self.assertTrue(result.stage_manifest_path.is_file())

    def test_requires_every_anchor_and_all_twelve_lookdev_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            manifest_path=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            removed=next(item for item in manifest["assets"] if item["task_id"]=="LOOKDEV_LD12")
            manifest["assets"]=[item for item in manifest["assets"] if item["task_id"]!="LOOKDEV_LD12"]
            manifest["registered_asset_count"]=len(manifest["assets"])
            manifest["last_registered_at"]=manifest["assets"][-1]["registered_at"]
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            (project/removed["path"]).unlink()
            with self.assertRaisesRegex(VisualReviewError,"missing registered assets|LOOKDEV_LD12"):
                build_visual_review(project)
            self.assertFalse((project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg").exists())

    def test_asset_or_prompt_tamper_blocks_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            manifest_path=project/"03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["prompt_sha256"]="0"*64
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(VisualReviewError,"prompt|manifest"):
                build_visual_review(project)

    def test_review_is_idempotent_and_output_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                first=build_visual_review(project); second=build_visual_review(project)
                self.assertEqual(second.status,"unchanged")
                self.assertEqual(first.review_digest,second.review_digest)
                first.contact_sheet_path.write_bytes(b"tampered")
                with self.assertRaisesRegex(VisualReviewError,"contact sheet|hash"):
                    build_visual_review(project)

    def test_hbg_failure_or_stage_manifest_failure_leaves_no_partial_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            with mock.patch("book_video_factory.visual_stage.review.subprocess.run",side_effect=OSError("injected hbg failure")):
                with self.assertRaisesRegex(VisualReviewError,"injected"):
                    build_visual_review(project)
            self.assertFalse((project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg").exists())
            self.assertFalse((project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json").exists())

        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet), \
                 mock.patch("book_video_factory.visual_stage.review.write_stage_manifest",side_effect=RuntimeError("injected stage failure")):
                with self.assertRaisesRegex(RuntimeError,"injected stage"):
                    build_visual_review(project)
            self.assertFalse((project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg").exists())
            self.assertFalse((project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json").exists())

    def test_coordinated_contact_report_and_stage_tamper_is_rejected_when_render_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                result=build_visual_review(project)
            with Image.new("RGB", (1948, 834), (190, 20, 30)) as image:
                image.save(result.contact_sheet_path, format="JPEG")
            report=json.loads(result.report_path.read_text(encoding="utf-8"))
            report["contact_sheet"]["sha256"]=sha256_file(result.contact_sheet_path)
            report["contact_sheet"]["bytes"]=result.contact_sheet_path.stat().st_size
            result.report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            stage=json.loads(result.stage_manifest_path.read_text(encoding="utf-8"))
            for item in stage["outputs"]:
                target=project/item["path"]
                item["sha256"]=sha256_file(target); item["bytes"]=target.stat().st_size
            result.stage_manifest_path.write_text(json.dumps(stage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                with self.assertRaisesRegex(VisualReviewError,"render|contact|deterministic"):
                    build_visual_review(project,verify_contact_render=True)

    def test_report_checklist_tamper_is_rejected_even_when_stage_hash_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project,_=self.clone_project(base)
            with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                result=build_visual_review(project)
            report=json.loads(result.report_path.read_text(encoding="utf-8"))
            report["semantic_review"]["required_checks"][0]="tampered semantic claim"
            result.report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            stage=json.loads(result.stage_manifest_path.read_text(encoding="utf-8"))
            for item in stage["outputs"]:
                if item["path"]==result.report_path.relative_to(project).as_posix():
                    item["sha256"]=sha256_file(result.report_path); item["bytes"]=result.report_path.stat().st_size
            result.stage_manifest_path.write_text(json.dumps(stage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(VisualReviewError,"report|deterministic|checklist"):
                build_visual_review(project)

    def test_stage_manifest_semantic_tamper_is_rejected(self) -> None:
        for mutate in ("producer","checks"):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temp:
                base=Path(temp); project,_=self.clone_project(base)
                with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
                    result=build_visual_review(project)
                stage=json.loads(result.stage_manifest_path.read_text(encoding="utf-8"))
                if mutate=="producer": stage["producer"]={"tool":"attacker"}
                else: stage["checks"][0]["id"]="forged_check"
                result.stage_manifest_path.write_text(json.dumps(stage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
                with self.assertRaisesRegex(VisualReviewError,"stage manifest"):
                    build_visual_review(project)


if __name__=="__main__": unittest.main()
