from __future__ import annotations

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

from phase2_fixture_factory import write_json
from book_video_factory.manifests import sha256_file
from test_phase3_visual_review import prepare, register_all, fake_contact_sheet
from book_video_factory.visual_stage.approval import (
    VisualApprovalError,
    approve_visual_stage,
    verify_visual_approval,
    visual_stage_next_status,
)
from book_video_factory.visual_stage.review import build_visual_review


def decision_for(project: Path, decision: str = "approved") -> dict:
    report=json.loads((project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json").read_text(encoding="utf-8"))
    status="pass" if decision=="approved" else "fail"
    return {
        "schema_version":"visual-review-decision.v1",
        "release_id":report["release_id"],
        "review_digest":report["review_digest"],
        "decision":decision,
        "semantic_checks":[{"check":item,"result":status} for item in report["semantic_review"]["required_checks"]],
        "reality_checks":[{"check":item,"result":status} for item in report["reality_review"]["required_checks"]],
        "aesthetic_checks":[{"check":item,"result":status} for item in report["aesthetic_review"]["required_checks"]],
    }


class Phase3VisualApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._fixture_temp=tempfile.TemporaryDirectory(); base=Path(cls._fixture_temp.name)
        project,tasks=prepare(base); register_all(base,project,tasks)
        with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
            build_visual_review(project)
        cls._fixture_project=project
        approved=base/"approved"/"warehouse"/"projects"/project.name
        approved.parent.mkdir(parents=True,exist_ok=True); shutil.copytree(project,approved)
        decision=base/"approved-decision.json"; write_json(decision,decision_for(approved))
        with mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet):
            approve_visual_stage(approved,release_id="r1",reviewer="fixture-reviewer",decision_path=decision,note="fixture approval")
        cls._approved_fixture_project=approved

    @classmethod
    def tearDownClass(cls) -> None: cls._fixture_temp.cleanup()

    def setUp(self) -> None:
        self._contact_patcher=mock.patch("book_video_factory.visual_stage.review._run_hbg_contact_sheet",side_effect=fake_contact_sheet)
        self._contact_patcher.start()

    def tearDown(self) -> None:
        self._contact_patcher.stop()

    def clone(self, base: Path, *, approved: bool=False) -> Path:
        source=self._approved_fixture_project if approved else self._fixture_project
        target=base/"warehouse"/"projects"/source.name
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copytree(source,target)
        return target

    def test_approved_decision_creates_hash_bound_event_and_unlocks_edge_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); decision=base/"decision.json"; write_json(decision,decision_for(project))
            result=approve_visual_stage(project,release_id="r1",reviewer="human-reviewer",
                decision_path=decision,note="锚点和十二张视觉测试图已逐项审查")
            self.assertEqual(result.status,"created")
            self.assertTrue(result.approval_path.is_file()); self.assertTrue(result.event_path.is_file())
            approval=json.loads(result.approval_path.read_text(encoding="utf-8"))
            self.assertEqual(approval["decision"],"approved")
            self.assertEqual(approval["next_stage_status"],"ready_for_edge_tts")
            verified=verify_visual_approval(project,"r1")
            self.assertTrue(verified.approved)
            self.assertEqual(visual_stage_next_status(project,"r1"),"ready_for_edge_tts")
            event=json.loads(result.event_path.read_text(encoding="utf-8"))
            self.assertEqual(event["gate"],"visual_anchor_lookdev")
            self.assertEqual(len(event["subjects"]),9)

    def test_cannot_approve_without_complete_explicit_semantic_reality_and_aesthetic_checks(self) -> None:
        for field in ("semantic_checks","reality_checks","aesthetic_checks"):
            with tempfile.TemporaryDirectory() as temp:
                base=Path(temp); project=self.clone(base); payload=decision_for(project); payload[field]=payload[field][:-1]
                path=base/"decision.json"; write_json(path,payload)
                with self.assertRaisesRegex(VisualApprovalError,field.replace("_"," ")[:-1] if False else "check|complete"):
                    approve_visual_stage(project,release_id="r1",reviewer="reviewer",decision_path=path)
                self.assertFalse((project/"03_images_生成图片/ANCHOR_APPROVAL.json").exists())
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); payload=decision_for(project); payload["reality_checks"][0]["result"]="fail"
            path=base/"decision.json"; write_json(path,payload)
            with self.assertRaisesRegex(VisualApprovalError,"all review checks must pass"):
                approve_visual_stage(project,release_id="r1",reviewer="reviewer",decision_path=path)

    def test_rejected_decision_is_recorded_but_remains_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); path=base/"decision.json"; write_json(path,decision_for(project,"rejected"))
            result=approve_visual_stage(project,release_id="r1",reviewer="reviewer",decision_path=path,note="人物身份不稳定")
            approval=json.loads(result.approval_path.read_text(encoding="utf-8"))
            self.assertEqual(approval["decision"],"rejected")
            self.assertEqual(approval["next_stage_status"],"blocked_by_visual_rejection")
            self.assertFalse(verify_visual_approval(project,"r1").approved)
            self.assertEqual(visual_stage_next_status(project,"r1"),"blocked_by_visual_rejection")

    def test_subject_tamper_or_approval_event_tamper_invalidates_approval(self) -> None:
        subjects=(
            "BOOK_VISUAL_PROFILE.json","VISUAL_REFERENCE_MANIFEST.json","ANCHOR_TASKS.jsonl",
            "LOOKDEV_TASKS.jsonl","VISUAL_ASSET_MANIFEST.json","VISUAL_REVIEW_REPORT.json",
            "LOOKDEV_CONTACT_SHEET.jpg",
        )
        for name in subjects:
            with tempfile.TemporaryDirectory() as temp:
                base=Path(temp); project=self.clone(base,approved=True)
                target=project/"03_images_生成图片"/name; target.write_bytes(target.read_bytes()+b"tamper")
                with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r1")
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base,approved=True)
            visual_manifest=json.loads((project/"03_images_生成图片/VISUAL_STAGE_MANIFEST.json").read_text(encoding="utf-8"))
            stage_path=project/visual_manifest["stage_manifest_path"]
            stage_path.write_bytes(stage_path.read_bytes()+b"tamper")
            with self.assertRaises(VisualApprovalError): verify_visual_approval(project,"r1")
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base,approved=True)
            approval=json.loads((project/"03_images_生成图片/ANCHOR_APPROVAL.json").read_text(encoding="utf-8"))
            event_path=project/approval["approval_event"]["path"]
            event_path.write_text("{}",encoding="utf-8")
            with self.assertRaisesRegex(VisualApprovalError,"event"):
                verify_visual_approval(project,"r1")

    def test_obvious_subject_tamper_fails_before_expensive_review_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base,approved=True)
            target=project/"03_images_生成图片/BOOK_VISUAL_PROFILE.json"
            target.write_bytes(target.read_bytes()+b"tamper")
            with mock.patch(
                "book_video_factory.visual_stage.approval.build_visual_review",
                side_effect=AssertionError("expensive review must not run for obvious subject tamper"),
            ) as rebuild:
                with self.assertRaisesRegex(VisualApprovalError,"subjects|hash|evidence"):
                    verify_visual_approval(project,"r1")
            rebuild.assert_not_called()

    def test_stale_release_fake_preexisting_approval_and_atomic_failure_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); path=base/"decision.json"; write_json(path,decision_for(project))
            with self.assertRaisesRegex(VisualApprovalError,"release"):
                approve_visual_stage(project,release_id="r2",reviewer="reviewer",decision_path=path)
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); fake=project/"03_images_生成图片/ANCHOR_APPROVAL.json"; fake.write_text("{}",encoding="utf-8")
            path=base/"decision.json"; write_json(path,decision_for(project))
            with self.assertRaisesRegex(VisualApprovalError,"pre-existing|approval"):
                approve_visual_stage(project,release_id="r1",reviewer="reviewer",decision_path=path)
            self.assertEqual(fake.read_text(encoding="utf-8"),"{}")
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base); path=base/"decision.json"; write_json(path,decision_for(project))
            with mock.patch("book_video_factory.visual_stage.approval.os.replace",side_effect=OSError("injected approval failure")):
                with self.assertRaisesRegex(VisualApprovalError,"injected"):
                    approve_visual_stage(project,release_id="r1",reviewer="reviewer",decision_path=path)
            self.assertFalse((project/"03_images_生成图片/ANCHOR_APPROVAL.json").exists())
            self.assertFalse(any((project/"logs/approval_events").glob("*visual-anchor*")))

    def test_coordinated_contact_report_and_stage_tamper_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); project=self.clone(base)
            contact=project/"03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg"
            with Image.new("RGB",(1948,834),(220,15,40)) as image:
                image.save(contact,format="JPEG")
            report_path=project/"03_images_生成图片/VISUAL_REVIEW_REPORT.json"
            report=json.loads(report_path.read_text(encoding="utf-8"))
            report["contact_sheet"]["sha256"]=sha256_file(contact)
            report["contact_sheet"]["bytes"]=contact.stat().st_size
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            stage_path=project/report["stage_manifest_path"]
            stage=json.loads(stage_path.read_text(encoding="utf-8"))
            for item in stage["outputs"]:
                target=project/item["path"]
                item["sha256"]=sha256_file(target); item["bytes"]=target.stat().st_size
            stage_path.write_text(json.dumps(stage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            decision=base/"decision.json"; write_json(decision,decision_for(project))
            with self.assertRaisesRegex(VisualApprovalError,"contact|render|review"):
                approve_visual_stage(project,release_id="r1",reviewer="human-reviewer",decision_path=decision)


if __name__=="__main__": unittest.main()
