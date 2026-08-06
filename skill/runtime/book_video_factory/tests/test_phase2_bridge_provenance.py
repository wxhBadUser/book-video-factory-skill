from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase2_fixture_factory import build_phase2_project
from book_video_factory.hbg_bridge.provenance import (
    HbgBridgeProvenanceError,
    verify_phase1_handoff,
)
from book_video_factory.manifests import record_approval, sha256_file


REQUIRED_SUBJECTS = [
    "02_story_script_故事脚本/SCRIPT_RELEASE.md",
    "02_story_script_故事脚本/SCRIPT_AUDIT.md",
    "02_story_script_故事脚本/SCRIPT_METRICS.json",
    "02_story_script_故事脚本/SCRIPT_LOCK.json",
    "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
]


class Phase2ProvenanceTests(unittest.TestCase):
    def test_content_manifest_outputs_must_match_phase_one_stage_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            manifest_path = project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            relative = "02_story_script_故事脚本/SCRIPT_RELEASE.md"
            target = project / relative
            target.write_text("coordinated but inconsistent release", encoding="utf-8")
            manifest["output_hashes"][relative] = sha256_file(target)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_approval(
                project, release_id="r1", gate="script", decision="approved", reviewer="reviewer",
                subjects=[project / item for item in REQUIRED_SUBJECTS], event_id="coordinated",
                reviewed_at="2026-08-01T14:00:00+00:00",
            )
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "stage manifest.*(?:outputs|byte count)|release artifact"):
                verify_phase1_handoff(project, "r1")

    def test_release_markdown_must_equal_frozen_script_package_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            manifest_path = project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stage_path = project / manifest["stage_manifest_path"]
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            relative = "02_story_script_故事脚本/SCRIPT_RELEASE.md"
            target = project / relative
            target.write_text("different release artifact", encoding="utf-8")
            new_hash = sha256_file(target)
            manifest["output_hashes"][relative] = new_hash
            for item in stage["outputs"]:
                if item["path"] == relative:
                    item["sha256"] = new_hash
                    item["bytes"] = target.stat().st_size
            stage_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest["stage_manifest_sha256"] = sha256_file(stage_path)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_approval(
                project, release_id="r1", gate="script", decision="approved", reviewer="reviewer",
                subjects=[project / item for item in REQUIRED_SUBJECTS], event_id="coordinated-stage",
                reviewed_at="2026-08-01T14:01:00+00:00",
            )
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "release artifact"):
                verify_phase1_handoff(project, "r1")


    def test_phase_one_stage_output_byte_count_must_match_real_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            manifest_path = project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stage_path = project / manifest["stage_manifest_path"]
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            relative = "02_story_script_故事脚本/SCRIPT_RELEASE.md"
            for item in stage["outputs"]:
                if item["path"] == relative:
                    item["bytes"] += 1
            stage_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest["stage_manifest_sha256"] = sha256_file(stage_path)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_approval(
                project, release_id="r1", gate="script", decision="approved", reviewer="reviewer",
                subjects=[project / item for item in REQUIRED_SUBJECTS], event_id="bad-byte-count",
                reviewed_at="2026-08-01T14:02:00+00:00",
            )
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "byte count"):
                verify_phase1_handoff(project, "r1")

    def test_valid_approved_handoff_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, result, _ = build_phase2_project(Path(temp), approve=True)
            handoff = verify_phase1_handoff(project, "r1")
            self.assertEqual(handoff.package_digest, result.package_digest)
            self.assertEqual(handoff.release_id, "r1")
            self.assertEqual(handoff.hbg_commit, "63aa262d88f18c6058b205c2dd582cf909b219a4")
            self.assertTrue(handoff.release_text)

    def test_missing_script_approval_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "script approval"):
                verify_phase1_handoff(project, "r1")

    def test_approval_for_wrong_release_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            record_approval(project, release_id="other", gate="script", decision="approved",
                            reviewer="reviewer", subjects=[project / item for item in REQUIRED_SUBJECTS],
                            event_id="wrong-release", reviewed_at="2026-08-01T12:00:00+00:00")
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "script approval"):
                verify_phase1_handoff(project, "r1")

    def test_approval_missing_required_subject_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=False)
            record_approval(project, release_id="r1", gate="script", decision="approved",
                            reviewer="reviewer", subjects=[project / item for item in REQUIRED_SUBJECTS[:-1]],
                            event_id="partial", reviewed_at="2026-08-01T12:00:00+00:00")
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "does not cover"):
                verify_phase1_handoff(project, "r1")

    def test_stale_approval_subject_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=True)
            path = project / REQUIRED_SUBJECTS[0]
            path.write_text(path.read_text(encoding="utf-8") + "tamper", encoding="utf-8")
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "package output hash"):
                verify_phase1_handoff(project, "r1")

    def test_tampered_stage_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=True)
            manifest = json.loads((project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
            stage = project / manifest["stage_manifest_path"]
            stage.write_text(stage.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "stage manifest"):
                verify_phase1_handoff(project, "r1")

    def test_script_lock_package_digest_disagreement_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=True)
            lock_path = project / "02_story_script_故事脚本/SCRIPT_LOCK.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["package_digest"] = "0" * 64
            lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # Content manifest catches the output tamper first; both are hard failures.
            with self.assertRaises(HbgBridgeProvenanceError):
                verify_phase1_handoff(project, "r1")

    def test_release_text_hash_disagreement_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=True)
            package_path = project / "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
            payload = json.loads(package_path.read_text(encoding="utf-8"))
            payload["script"]["release_version"]["text"] += "被篡改"
            package_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "package output hash"):
                verify_phase1_handoff(project, "r1")

    def test_missing_locked_hbg_file_fails(self) -> None:
        # Use a copied repository root override so the real vendor remains pristine.
        with tempfile.TemporaryDirectory() as temp:
            project, _, _, _ = build_phase2_project(Path(temp), approve=True)
            fake_root = Path(temp) / "fake-repo"
            vendor_source = ROOT.parent / "vendor" / "hbg-life-simulation"
            import shutil
            shutil.copytree(vendor_source, fake_root / "vendor/hbg-life-simulation")
            (fake_root / "vendor/hbg-life-simulation/scripts/build_narration.mjs").unlink()
            with self.assertRaisesRegex(HbgBridgeProvenanceError, "HBG vendor"):
                verify_phase1_handoff(project, "r1", repository_root=fake_root)


if __name__ == "__main__":
    unittest.main()
