"""Part 6: Render Preflight vision-evidence gate.

``_scene_review_vision_blockers`` is the final checkpoint before pixels are
spent. Once a ``SCENE_REVIEW_DECISION.json`` artifact exists, every shot must
carry authoritative vision evidence (or an explicit ``legacy_pass``) or the
render is blocked. A missing artifact is not the preflight's responsibility
(the scene-approval gate governs that) so it is skipped.
"""

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.render_stage.preflight import (
    RenderPreflightError,
    _scene_review_vision_blockers,
)


def _evidence(**overrides: object) -> dict:
    base = {
        "shot_id": "B01",
        "vision_provider": "claude-sonnet-4.5",
        "call_id": "call-abc123",
        "image_sha256": "a" * 64,
        "caption_sha256": "b" * 64,
        "prompt_sha256": "c" * 64,
        "parity_verdict": "match",
        "parity_reasoning": "The image clearly shows the subject described in the caption.",
        "reviewed_pixels": True,
        "legacy_pass": False,
    }
    base.update(overrides)
    return base


def _decision_document(*decisions: dict) -> dict:
    return {
        "schema_version": "scene-review-decision.v1",
        "release_id": "r1",
        "director_stage_manifest_sha256": "0" * 64,
        "scene_asset_manifest_sha256": "0" * 64,
        "reviewer": "Reviewer",
        "decisions": list(decisions),
    }


def _base_decision(task_id: str = "B01", **extra: object) -> dict:
    decision = {
        "task_id": task_id,
        "semantic_review_status": "pass",
        "reality_review_status": "pass",
        "identity_review_status": "pass",
        "note": "Reviewed.",
    }
    decision.update(extra)
    return decision


class PreflightVisionEvidenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="preflight-gate-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, document: dict) -> Path:
        path = self.root / "06_visual_production" / "SCENE_REVIEW_DECISION.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_unbound_decision_blocks_shot(self) -> None:
        path = self._write(_decision_document(_base_decision("B01")))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_legacy_pass_accepted(self) -> None:
        path = self._write(_decision_document(_base_decision("B01", legacy_pass=True)))
        self.assertEqual(_scene_review_vision_blockers(path), [])

    def test_valid_vision_evidence_accepted(self) -> None:
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), [])

    def test_mismatch_verdict_still_structurally_accepted(self) -> None:
        # The preflight gate only demands *presence* and *structure* of evidence;
        # the verdict severity is judged later by the review stage.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01", parity_verdict="mismatch")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), [])

    def test_partial_block_reports_only_unbound_shots(self) -> None:
        path = self._write(_decision_document(
            _base_decision("B01", legacy_pass=True),
            _base_decision("B02"),
            _base_decision("B03", vision_evidence=_evidence(shot_id="B03")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B02"])

    def test_missing_decision_artifact_skipped(self) -> None:
        missing = self.root / "06_visual_production" / "SCENE_REVIEW_DECISION.json"
        self.assertEqual(_scene_review_vision_blockers(missing), [])

    def test_symlinked_decision_skipped(self) -> None:
        target = self._write(_decision_document(_base_decision("B01")))
        link = self.root / "link.json"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation requires privilege on this platform")
        self.assertEqual(_scene_review_vision_blockers(link), [])

    def test_malformed_schema_raises(self) -> None:
        doc = _decision_document(_base_decision("B01"))
        doc["schema_version"] = "scene-review-decision.v0"
        path = self._write(doc)
        with self.assertRaises(RenderPreflightError):
            _scene_review_vision_blockers(path)

    def test_unknown_decision_field_rejected(self) -> None:
        path = self._write(_decision_document(
            _base_decision("B01", unexpected_field="should not be here"),
        ))
        with self.assertRaises(RenderPreflightError):
            _scene_review_vision_blockers(path)

    def test_thin_reasoning_evidence_blocks_shot(self) -> None:
        # Evidence that did not read pixels or is too thin is not evidence.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01", parity_reasoning="ok")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])


if __name__ == "__main__":
    unittest.main()
