"""§10.1 / §10.2 gates: a scene review decision may not assert a semantic pass
without real, signed vision evidence.

These tests exercise ``production_visuals.review._decision`` and
``render_stage.encoded_visual_qa._review_decision`` directly because the full
fixture chain is unrelated to the gate under test. ``_decision`` and
``_review_decision`` are the exact boundaries the design modifies:

    * evidence present (and cryptographically verifiable) -> accepted
    * evidence absent (incl. legacy)                    -> ``MissingVisionEvidenceError``
    * evidence present but hand-forged / broken         -> ``VisionReviewError``

The vision_evidence payload is no longer a hand-authored dict. It is minted by a
real, keyed ``LocalVisionProvider`` through ``review_shot``, so the gate is
exercised against verifiable evidence -- and the old forgery shortcut (a JSON
naming ``claude-sonnet-4.5`` with no signature) is asserted to be rejected.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from book_video_factory.production_visuals.review import SceneReviewError, _decision
from book_video_factory.render_stage.encoded_visual_qa import (
    EncodedVisualQaError,
    _review_decision,
)
from book_video_factory.semantic_alignment.vision_review import (
    LocalVisionProvider,
    MissingVisionEvidenceError,
    ParityResult,
    VisionEvidence,
    VisionReviewError,
    review_shot,
)

RELEASE = "r1"
DIRECTOR_SHA = "a" * 64
ASSET_SHA = "b" * 64
VIDEO_SHA = "f" * 64
PLAN_SHA = "0" * 64

CAPTION = "福贵牵着老牛走过田埂。"
PROMPT = "[1/9 CAPTION]\n福贵牵着老牛走过田埂。\n..."


def _make_image(tmp: Path) -> Path:
    path = tmp / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake-pixels")
    return path


class _KeyedStubProvider(LocalVisionProvider):
    """A local, keyed stub that returns a caller-chosen verdict.

    Inherits the real signing machinery from ``LocalVisionProvider`` (its secret
    is registered at import), so the evidence it mints is genuinely verifiable.
    """

    name = "local-vision-stub"

    def __init__(self, verdict: str = "match") -> None:
        self.verdict = verdict

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        import hashlib
        call_id = hashlib.sha256(image_bytes + str(caption_text).encode("utf-8")).hexdigest()[:24]
        return ParityResult(verdict=self.verdict, reasoning="The scene image clearly illustrates the approved caption.", call_id=call_id)


def _signed_evidence(verdict: str = "match", **overrides: Any) -> dict[str, Any]:
    """Mint a real, signed vision-evidence dict via the keyed local provider."""

    with tempfile.TemporaryDirectory() as raw:
        image = _make_image(Path(raw))
        evidence = review_shot(
            shot_id="s",
            image_path=image,
            caption_text=CAPTION,
            prompt_text=PROMPT,
            provider=_KeyedStubProvider(verdict=verdict),
        )
    payload = evidence.to_dict()
    payload.update(overrides)
    return payload


def _forged_claude_evidence(**overrides: Any) -> dict[str, Any]:
    """The old shortcut: a hand-authored dict naming claude-sonnet-4.5.

    It carries no valid signature, so ``verify`` / the decision gate must reject
    it. This is exactly the forged-call-id gap the independent re-review flagged.
    """

    payload = {
        "vision_provider": "claude-sonnet-4.5",
        "call_id": "toolu_realcall_001",
        "image_sha256": "c" * 64,
        "caption_sha256": "d" * 64,
        "prompt_sha256": "e" * 64,
        "parity_verdict": "match",
        "parity_reasoning": "画面里福贵牵着老牛走过田埂，与字幕一致。",
        "reviewed_pixels": True,
    }
    payload.update(overrides)
    return payload


def _decision_doc(*decisions: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "scene-review-decision.v1",
        "release_id": RELEASE,
        "director_stage_manifest_sha256": DIRECTOR_SHA,
        "scene_asset_manifest_sha256": ASSET_SHA,
        "reviewer": "human",
        "decisions": list(decisions),
    }


def _base_decision(task_id: str = "B01", **extra: Any) -> dict[str, Any]:
    item = {
        "task_id": task_id,
        "semantic_review_status": "pass",
        "reality_review_status": "pass",
        "identity_review_status": "pass",
        "note": "ok",
    }
    item.update(extra)
    return item


class ReviewVisionEvidenceGateTests(unittest.TestCase):
    def _run(self, doc: dict[str, Any], task_ids: list[str] | None = None) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scene-review-decision.json"
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return _decision(
                path,
                release_id=RELEASE,
                director_sha=DIRECTOR_SHA,
                asset_sha=ASSET_SHA,
                task_ids=task_ids or ["B01"],
            )

    def test_new_pass_decision_without_vision_evidence_is_rejected(self) -> None:
        doc = _decision_doc(_base_decision())
        with self.assertRaises(MissingVisionEvidenceError):
            self._run(doc)

    def test_legacy_pass_decision_without_evidence_is_rejected(self) -> None:
        doc = _decision_doc(_base_decision(legacy_pass=True))
        with self.assertRaises(MissingVisionEvidenceError):
            self._run(doc)

    def test_pass_decision_with_valid_signed_vision_evidence_is_accepted(self) -> None:
        doc = _decision_doc(_base_decision(vision_evidence=_signed_evidence("match")))
        value = self._run(doc)
        self.assertEqual(
            value["decisions"][0]["vision_evidence"]["parity_verdict"], "match"
        )

    def test_forged_claude_evidence_is_rejected(self) -> None:
        # The historical forgery shortcut must fail closed at the gate.
        doc = _decision_doc(_base_decision(vision_evidence=_forged_claude_evidence()))
        with self.assertRaises(VisionReviewError):
            self._run(doc)

    def test_fail_decision_with_mismatch_evidence_is_accepted_by_the_gate(self) -> None:
        doc = _decision_doc(
            _base_decision(
                semantic_review_status="fail",
                vision_evidence=_signed_evidence("mismatch"),
            )
        )
        value = self._run(doc)
        self.assertEqual(
            value["decisions"][0]["vision_evidence"]["parity_verdict"], "mismatch"
        )

    def test_vision_evidence_missing_call_id_is_rejected(self) -> None:
        broken = _signed_evidence()
        broken["call_id"] = ""
        doc = _decision_doc(_base_decision(vision_evidence=broken))
        with self.assertRaises(VisionReviewError):
            self._run(doc)

    def test_vision_evidence_that_did_not_read_pixels_is_rejected(self) -> None:
        doc = _decision_doc(
            _base_decision(vision_evidence=_signed_evidence(reviewed_pixels=False))
        )
        with self.assertRaises(VisionReviewError):
            self._run(doc)

    def test_vision_evidence_with_truncated_hash_is_rejected(self) -> None:
        doc = _decision_doc(
            _base_decision(vision_evidence=_signed_evidence(image_sha256="c" * 32))
        )
        with self.assertRaises(VisionReviewError):
            self._run(doc)

    def test_unknown_decision_field_is_still_rejected(self) -> None:
        doc = _decision_doc(_base_decision(legacy_pass=True, bogus="x"))
        with self.assertRaises(SceneReviewError):
            self._run(doc)

    def test_every_decision_is_gated_independently(self) -> None:
        doc = _decision_doc(
            _base_decision("B01", legacy_pass=True),
            _base_decision("B02"),
        )
        with self.assertRaises(MissingVisionEvidenceError):
            self._run(doc, task_ids=["B01", "B02"])


def _encoded_doc(*decisions: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "encoded-visual-review-decision.v1",
        "release_id": RELEASE,
        "video_sha256": VIDEO_SHA,
        "frame_plan_sha256": PLAN_SHA,
        "reviewer": "human",
        "decisions": list(decisions),
    }


def _encoded_decision(sample_id: str = "S01", **extra: Any) -> dict[str, Any]:
    item = {
        "sample_id": sample_id,
        "semantic_status": "pass",
        "visual_reality_status": "pass",
        "identity_status": "not_applicable",
        "caption_status": "not_applicable",
        "note": "",
        "caption_note": "",
    }
    item.update(extra)
    return item


class EncodedReviewVisionEvidenceGateTests(unittest.TestCase):
    """§10.2: the encoded frame review mirrors the per-shot vision gate."""

    def _run(self, doc: dict[str, Any], sample_ids: list[str] | None = None) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "encoded-visual-review-decision.json"
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return _review_decision(
                path,
                release_id=RELEASE,
                video_sha=VIDEO_SHA,
                plan_sha=PLAN_SHA,
                sample_ids=sample_ids or ["S01"],
                caption_sample_ids=set(),
            )

    def test_encoded_pass_without_vision_evidence_is_rejected(self) -> None:
        with self.assertRaises(MissingVisionEvidenceError):
            self._run(_encoded_doc(_encoded_decision()))

    def test_encoded_legacy_pass_without_evidence_is_rejected(self) -> None:
        with self.assertRaises(MissingVisionEvidenceError):
            self._run(_encoded_doc(_encoded_decision(legacy_pass=True)))

    def test_encoded_valid_signed_vision_evidence_is_accepted(self) -> None:
        value = self._run(
            _encoded_doc(_encoded_decision(vision_evidence=_signed_evidence("rough")))
        )
        self.assertEqual(
            value["decisions"][0]["vision_evidence"]["parity_verdict"], "rough"
        )

    def test_encoded_forged_claude_evidence_is_rejected(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._run(
                _encoded_doc(_encoded_decision(vision_evidence=_forged_claude_evidence()))
            )

    def test_encoded_evidence_without_pixels_is_rejected(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._run(
                _encoded_doc(
                    _encoded_decision(vision_evidence=_signed_evidence(reviewed_pixels=False))
                )
            )

    def test_encoded_unknown_field_is_still_rejected(self) -> None:
        with self.assertRaises(EncodedVisualQaError):
            self._run(_encoded_doc(_encoded_decision(legacy_pass=True, bogus="x")))


if __name__ == "__main__":
    unittest.main()
