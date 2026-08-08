"""Part 6: Render Preflight vision-evidence gate.

``_scene_review_vision_blockers`` is the final checkpoint before pixels are
spent. Every shot must carry authoritative vision evidence (a trusted
multimodal provider that read the pixels, with a passing verdict) bound to its
frame. The decision artifact MUST exist and be a regular file: a missing or
symlinked decision fails closed and blocks the render ("no verified review
means no render"). A mismatch verdict also blocks.
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

    def test_legacy_pass_without_evidence_is_blocked(self) -> None:
        # legacy_pass no longer substitutes for vision evidence.
        path = self._write(_decision_document(_base_decision("B01", legacy_pass=True)))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_valid_vision_evidence_accepted(self) -> None:
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), [])

    def test_mismatch_verdict_is_a_preflight_blocker(self) -> None:
        # With require_pass=True the preflight demands a passing verdict, not just
        # the presence of evidence: a mismatch blocks the render.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01", parity_verdict="mismatch")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_partial_block_reports_only_unbound_shots(self) -> None:
        # Shots carrying authoritative vision evidence are accepted; only the
        # shot with no binding is reported. legacy_pass is no longer a binding.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01")),
            _base_decision("B02"),
            _base_decision("B03", vision_evidence=_evidence(shot_id="B03")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B02"])

    def test_missing_decision_artifact_blocks(self) -> None:
        # Fail closed: a missing decision artifact is not silently skipped.
        missing = self.root / "06_visual_production" / "SCENE_REVIEW_DECISION.json"
        with self.assertRaises(RenderPreflightError):
            _scene_review_vision_blockers(missing)

    def test_symlinked_decision_blocks(self) -> None:
        # Fail closed: a symlinked decision artifact is rejected (tamper surface).
        target = self._write(_decision_document(_base_decision("B01")))
        link = self.root / "link.json"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation requires privilege on this platform")
        with self.assertRaises(RenderPreflightError):
            _scene_review_vision_blockers(link)

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

    def test_stale_image_after_review_is_blocked(self) -> None:
        # A-stale (BLOCKER-2 change-invalidation): a shot whose reviewed frame is
        # swapped after the review must be blocked at the render gate, even when
        # the decision still carries a previously-valid vision evidence record.
        # This drives the public gate with a real on-disk artifact.
        import hashlib

        image_path = self.root / "06_visual_production" / "SCENE_ASSETS" / "B01.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"original-frame-bytes-v1")
        image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()

        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_evidence(shot_id="B01", image_sha256=image_hash)),
        ))
        assets_by_task = {"B01": image_path}

        # Current frame matches the stored evidence -> accepted.
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), [])

        # Frame swapped after review -> stale evidence must block the render.
        image_path.write_bytes(b"tampered-frame-bytes-v2-different")
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), ["B01"])


if __name__ == "__main__":
    unittest.main()
