from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from test_sample_visual_pilot import SampleVisualPilotTests, build_multi_span_project
from book_video_factory.sample_visual_approval import (
    SampleVisualApprovalError,
    approve_sample_visual_pilot,
    verify_sample_visual_approval,
)


class SampleVisualApprovalTests(unittest.TestCase):
    def _approved_decision(self, project: Path) -> Path:
        pilot_path = project / "03_images_生成图片" / "SAMPLE_VISUAL_PILOT_MANIFEST.json"
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        decision = {
            "schema_version": "sample-visual-review-decision.v2",
            "release_id": "r1",
            "sample_id": "pilot-60s",
            "sample_visual_pilot_sha256": hashlib.sha256(pilot_path.read_bytes()).hexdigest(),
            "decision": "approved",
            "semantic_checks": [
                {"check": "scene_signature_matches_excerpt", "result": "pass"},
                {"check": "required_entities_present", "result": "pass"},
                {"check": "forbidden_entities_absent", "result": "pass"},
            ],
            "reality_checks": [
                {"check": "anatomy_tools_and_ground_contact_plausible", "result": "pass"},
                {"check": "no_black_border_or_embedded_text", "result": "pass"},
            ],
            "aesthetic_checks": [
                {"check": "identity_and_literary_style_consistent", "result": "pass"},
                {"check": "subtitle_safe_composition", "result": "pass"},
            ],
        }
        path = project / "sample-visual-decision.json"
        path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
        return path

    def test_approval_binds_current_sample_manifest_and_invalidates_on_image_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            factory = SampleVisualPilotTests()
            project, input_path = factory._project(base)
            from book_video_factory.sample_visual_pilot import build_sample_visual_pilot
            build_sample_visual_pilot(project, input_path)
            result = approve_sample_visual_pilot(
                project,
                release_id="r1",
                reviewer="human-reviewer",
                decision_path=self._approved_decision(project),
                note="sample review",
            )
            self.assertEqual(result.next_stage_status, "ready_for_sample_audio")
            self.assertTrue(result.event_path.is_file())
            self.assertTrue(verify_sample_visual_approval(project, "r1").approved)
            pilot = json.loads((project / "03_images_生成图片" / "SAMPLE_VISUAL_PILOT_MANIFEST.json").read_text(encoding="utf-8"))
            target = project / pilot["scene_spans"][0]["representative_image"]["path"]
            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(SampleVisualApprovalError, "stale|hash"):
                verify_sample_visual_approval(project, "r1")

    def test_approval_binds_multi_span_sample_pilot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project, input_path = build_multi_span_project(base)
            from book_video_factory.sample_visual_pilot import build_sample_visual_pilot
            build_sample_visual_pilot(project, input_path)
            result = approve_sample_visual_pilot(
                project,
                release_id="r1",
                reviewer="human-reviewer",
                decision_path=self._approved_decision(project),
                note="multi span review",
            )
            self.assertEqual(result.next_stage_status, "ready_for_sample_audio")
            self.assertTrue(verify_sample_visual_approval(project, "r1").approved)


if __name__ == "__main__":
    unittest.main()
