from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.contracts import ContractError, ReleaseProfile  # noqa: E402
from book_video_factory.gates import approval_is_current, evaluate_workflow_state  # noqa: E402
from book_video_factory.manifests import record_approval, write_stage_manifest  # noqa: E402
from book_video_factory.project import initialize_project  # noqa: E402
from book_video_factory.style_profiles import (  # noqa: E402
    StyleProfileError,
    load_style_profile,
    project_workflow,
)

PROFILE_PATH = ROOT / "config/release_profiles/book-classic-narrator-hbg-16x9-v1.json"
PROFILE_ID = "book-classic-narrator-hbg-16x9-v1"
STYLE_ID = "classic-narrator-hbg-v1"


class ReleaseProfileTests(unittest.TestCase):
    def test_contract_schemas_are_valid_json(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.schema.json")):
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )

    def test_active_release_and_style_profiles_are_valid(self) -> None:
        profile = ReleaseProfile.load(PROFILE_PATH)
        style = load_style_profile(STYLE_ID)
        self.assertEqual(profile.profile_id, PROFILE_ID)
        self.assertEqual(profile.renderer, "static_streaming_ffmpeg")
        self.assertEqual(style.release_profile_id, PROFILE_ID)
        self.assertEqual(style.resolve_generation_lane(None), "host-imagegen")

    def test_invalid_title_safe_box_fails_closed(self) -> None:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        payload["typography"]["title_max_width_px"] = 1900
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ContractError):
                ReleaseProfile.load(path)


class ManifestTests(unittest.TestCase):
    def test_stage_manifest_is_immutable_and_hashes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            source = project / "input.txt"
            output = project / "output.txt"
            source.write_text("source", encoding="utf-8")
            output.write_text("output", encoding="utf-8")
            first = write_stage_manifest(
                project,
                stage="render",
                release_id="v1-r1",
                release_profile_id=PROFILE_ID,
                inputs=[("script", source)],
                outputs=[("local_master", output)],
                checks=[{"id": "smoke", "result": "pass", "severity": "error"}],
                manifest_id="fixed-id",
                recorded_at="2026-08-01T00:00:00+00:00",
            )
            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["inputs"][0]["sha256"]), 64)
            with self.assertRaises(FileExistsError):
                write_stage_manifest(
                    project,
                    stage="render",
                    release_id="v1-r1",
                    release_profile_id=PROFILE_ID,
                    inputs=[("script", source)],
                    outputs=[("local_master", output)],
                    checks=[],
                    manifest_id="fixed-id",
                    recorded_at="2026-08-01T00:00:00+00:00",
                )

    def test_approval_becomes_stale_when_subject_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            script = project / "script.json"
            script.write_text('{"version": 1}', encoding="utf-8")
            event_path = record_approval(
                project,
                release_id="v1-r1",
                gate="script",
                decision="approved",
                reviewer="human-reviewer",
                subjects=[script],
                event_id="approval-1",
                reviewed_at="2026-08-01T00:00:00+00:00",
            )
            event = json.loads(event_path.read_text(encoding="utf-8"))
            self.assertTrue(approval_is_current(project, event))
            script.write_text('{"version": 2}', encoding="utf-8")
            self.assertFalse(approval_is_current(project, event))

    def test_stage_manifest_rejects_symlinked_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            source = project / "input.txt"
            output = project / "output.txt"
            source.write_text("source", encoding="utf-8")
            output.write_text("output", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            manifests = project / "manifests"
            try:
                manifests.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            with self.assertRaisesRegex(ValueError, "symlink"):
                write_stage_manifest(
                    project,
                    stage="render",
                    release_id="v1-r1",
                    release_profile_id=PROFILE_ID,
                    inputs=[("script", source)],
                    outputs=[("master", output)],
                    checks=[],
                )
            self.assertEqual(list(outside.iterdir()), [])


class GateTests(unittest.TestCase):
    def test_project_uses_the_single_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            workflow = project_workflow(project)
            self.assertEqual(workflow["style_profile_id"], STYLE_ID)
            self.assertEqual(workflow["release_profile_id"], PROFILE_ID)
            self.assertEqual(workflow["generation_lane"], "host-imagegen")
            self.assertEqual(workflow["narration_provider_policy"], "minimax_required")
            result = evaluate_workflow_state(project, ReleaseProfile.load(PROFILE_PATH))
            self.assertEqual(result["derived_state"], "draft")
            self.assertTrue(result["release_profile_aligned"])

    def test_project_workflow_rejects_recorded_release_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            contract_path = project / "project.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["workflow"]["release_profile_id"] = "removed-profile"
            contract_path.write_text(
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StyleProfileError, "incompatible profile"):
                project_workflow(project)

    def test_same_timestamp_gate_decisions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            topic = project / "00_topic_选题/topic.json"
            topic.write_text('{"approved": true}', encoding="utf-8")
            timestamp = "2026-08-01T00:00:00+00:00"
            for event_id, decision in (("approval-a", "approved"), ("approval-b", "revoked")):
                record_approval(
                    project,
                    release_id="v1-r1",
                    gate="topic",
                    decision=decision,
                    reviewer="human",
                    subjects=[topic],
                    event_id=event_id,
                    reviewed_at=timestamp,
                )
            result = evaluate_workflow_state(
                project, ReleaseProfile.load(PROFILE_PATH), release_id="v1-r1"
            )
            self.assertEqual(result["derived_state"], "draft")
            self.assertNotIn("topic", result["current_approval_gates"])

    def test_release_scope_never_combines_two_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            topic = project / "00_topic_选题/topic.json"
            script = project / "02_story_script_故事脚本/script.narrator-essay.v1.json"
            topic.write_text('{"approved": true}', encoding="utf-8")
            script.write_text('{"schema_version": "script.narrator-essay.v1"}', encoding="utf-8")
            record_approval(
                project,
                release_id="v1-r1",
                gate="topic",
                decision="approved",
                reviewer="human",
                subjects=[topic],
            )
            record_approval(
                project,
                release_id="v2-r1",
                gate="script",
                decision="approved",
                reviewer="human",
                subjects=[script],
            )
            profile = ReleaseProfile.load(PROFILE_PATH)
            ambiguous = evaluate_workflow_state(project, profile)
            self.assertIsNone(ambiguous["release_id"])
            self.assertFalse(ambiguous["release_scope_valid"])
            self.assertEqual(ambiguous["derived_state"], "invalid")
            old_release = evaluate_workflow_state(project, profile, release_id="v1-r1")
            self.assertEqual(old_release["derived_state"], "topic_approved")
            self.assertEqual(old_release["current_approval_gates"], ["topic"])

    def test_qc_report_must_match_active_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            qc_dir = project / "09_qc_质检/v1-r1"
            qc_dir.mkdir(parents=True)
            (qc_dir / "release-gate.json").write_text(
                json.dumps({"release_id": "v1-r1", "local_master_status": "pass"}),
                encoding="utf-8",
            )
            profile = ReleaseProfile.load(PROFILE_PATH)
            self.assertTrue(evaluate_workflow_state(project, profile, release_id="v1-r1")["qc_passed"])
            self.assertFalse(evaluate_workflow_state(project, profile, release_id="v2-r1")["qc_passed"])

    def test_project_status_cannot_bypass_derived_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(Path(temp), "sample", "样书", "作者")
            project_json = project / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["status"] = "ready_to_publish"
            project_json.write_text(json.dumps(payload), encoding="utf-8")
            result = evaluate_workflow_state(project, ReleaseProfile.load(PROFILE_PATH))
            self.assertEqual(result["derived_state"], "draft")
            self.assertFalse(result["ready_to_publish"])


if __name__ == "__main__":
    unittest.main()
