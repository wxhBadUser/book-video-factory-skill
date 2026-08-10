"""Part 6: Render Preflight vision-evidence gate.

``_scene_review_vision_blockers`` is the final checkpoint before pixels are
spent. Every shot must carry authoritative, cryptographically-signed vision
evidence (a trusted, keyed multimodal provider that read the pixels, with a
passing verdict) bound to its frame. The decision artifact MUST exist and be a
regular file: a missing or symlinked decision fails closed and blocks the render
("no verified review means no render"). A mismatch verdict also blocks.

The vision_evidence payload is no longer a hand-authored dict: it is minted by a
real, keyed ``LocalVisionProvider`` through ``review_shot``, so the gate is
exercised against verifiable evidence -- and a forged/stale record is rejected.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.semantic_alignment.contract_bindings import (
    aggregate_contract_bindings_sha256,
)
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import compute_prompt_binding
from book_video_factory.semantic_alignment.vision_review import (
    LocalVisionProvider,
    ParityResult,
    review_shot,
)


CAPTION = "CAP"
PROMPT = "PROMPT"


class _KeyedStubProvider(LocalVisionProvider):
    """A local, keyed stub that returns a caller-chosen verdict.

    Inherits the real signing machinery from ``LocalVisionProvider`` (its secret
    is registered at import), so the evidence it mints is genuinely verifiable.
    """

    name = "local-vision-stub"

    def __init__(self, verdict: str = "match", reasoning: str = "The image clearly shows the subject described in the caption.") -> None:
        self.verdict = verdict
        self.reasoning = reasoning

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        call_id = hashlib.sha256(image_bytes + str(caption_text).encode("utf-8")).hexdigest()[:24]
        return ParityResult(verdict=self.verdict, reasoning=self.reasoning, call_id=call_id)


def _signed_evidence(
    verdict: str = "match",
    *,
    shot_id: str = "B01",
    image_path: Path | None = None,
    caption_text: str = CAPTION,
    prompt_text: str = PROMPT,
    **overrides: object,
) -> dict:
    """Mint a real, signed vision-evidence dict via the keyed local provider.

    The image must stay on disk for the duration of ``review_shot`` (it reads the
    bytes and hashes them), so the temp directory is only cleaned up afterwards.
    The caption/prompt default to the fixture task queue's values so the gate's
    re-verification against that queue passes until an artifact is actually
    edited.
    """

    if image_path is None:
        tmp = tempfile.TemporaryDirectory()
        raw = Path(tmp.name)
        img = raw / "frame.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake-pixels")
        image_path = img
    else:
        tmp = None
    try:
        evidence = review_shot(
            shot_id=shot_id, image_path=image_path,
            caption_text=caption_text, prompt_text=prompt_text,
            provider=_KeyedStubProvider(verdict=verdict),
        )
    finally:
        if tmp is not None:
            tmp.cleanup()
    payload = evidence.to_dict()
    payload.update(overrides)
    return payload


def _hand_evidence(**overrides: object) -> dict:
    """An intentionally *unsigned* hand-authored dict (used to prove rejection)."""

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
        # The gate re-verifies vision evidence against the director task queue,
        # so every fixture that enters the stale-check path needs a real queue.
        self._write_tasks("B01", "B02", "B03")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_tasks(self, *ids: str) -> Path:
        path = self.root / "05_director" / "IMAGE_TASKS.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # The gate re-verifies signed vision evidence against the director task
        # queue (IMAGE_TASKS.jsonl); every line must be a schema-valid production
        # image task, or the queue fails closed and the shot is blocked. The
        # tasks are built with the implementation helpers so the contract binding,
        # aggregate SHA, prompt binding and proposition hashes are internally
        # consistent and pass validate_production_image_task.
        proposition = VisualProposition(
            mode="Abstract",
            subject="subject",
            action="",
            environment="",
            mood="mood",
            lighting="lighting",
            palette="palette",
            rationale_text="rationale",
            entity_visibility=(),
            surrogate_objects=(),
            source_terms=(),
        )
        lines = []
        for index, tid in enumerate(ids, start=1):
            caption_id = f"caption-{index:04d}"
            caption_contract_bindings = [
                {"caption_id": caption_id, "content_sha256": "a" * 64}
            ]
            contract_sha = aggregate_contract_bindings_sha256(
                caption_contract_bindings, expected_caption_ids=[caption_id]
            )
            binding = compute_prompt_binding(
                caption_ids=[caption_id],
                caption_text=CAPTION,
                proposition=proposition,
                prompt=PROMPT,
                scene_id=tid,
                beat_ids=["beat-001"],
                shot_id=tid,
                caption_visual_contract_sha256=contract_sha,
                group_id=f"group-{index:04d}",
                caption_contract_bindings=caption_contract_bindings,
                caption_group_sha256="b" * 64,
            )
            task = {
                "schema_version": "production-image-task.v1",
                "task_id": tid,
                "scene_id": tid,
                "shot_id": tid,
                "generation_lane": "host-imagegen",
                "generation_mode": "single",
                "caption_ids": [caption_id],
                "caption_text": CAPTION,
                "caption_visual_contract_sha256": contract_sha,
                "narrative_function": "plot",
                "source_beat_ids": ["beat-001"],
                "prompt": PROMPT,
                "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                "visual_proposition": proposition.to_dict(),
                "prompt_binding": binding,
                "output_target": f"06_visual_production/SCENE_ASSETS/{tid}.png",
            }
            lines.append(json.dumps(task, ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

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
            _base_decision("B01", vision_evidence=_signed_evidence("match", shot_id="B01")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), [])

    def test_forged_claude_evidence_is_rejected(self) -> None:
        # The historical forgery shortcut (a JSON naming claude-sonnet-4.5 with
        # no signature) must fail closed at the gate.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_hand_evidence(shot_id="B01")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_mismatch_verdict_is_a_preflight_blocker(self) -> None:
        # With require_pass=True the preflight demands a passing verdict, not just
        # the presence of evidence: a mismatch blocks the render.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_signed_evidence("mismatch", shot_id="B01")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_partial_block_reports_only_unbound_shots(self) -> None:
        # Shots carrying authoritative signed evidence are accepted; only the
        # shot with no binding is reported. legacy_pass is no longer a binding.
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_signed_evidence("match", shot_id="B01")),
            _base_decision("B02"),
            _base_decision("B03", vision_evidence=_signed_evidence("match", shot_id="B03")),
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
        # (A real provider can never mint this: validate() rejects it first.)
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=_hand_evidence(shot_id="B01", parity_reasoning="ok")),
        ))
        self.assertEqual(_scene_review_vision_blockers(path), ["B01"])

    def test_stale_image_after_review_is_blocked(self) -> None:
        # A-stale (BLOCKER-2 change-invalidation): a shot whose reviewed frame is
        # swapped after the review must be blocked at the render gate, even when
        # the decision still carries a previously-valid (signed) vision record.
        image_path = self.root / "06_visual_production" / "SCENE_ASSETS" / "B01.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"original-frame-bytes-v1")

        # Mint real, signed evidence bound to the current frame + the fixture
        # task queue's caption/prompt.
        evidence = review_shot(
            shot_id="B01", image_path=image_path,
            caption_text=CAPTION, prompt_text=PROMPT, provider=_KeyedStubProvider("match"),
        )
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=evidence.to_dict()),
        ))
        assets_by_task = {"B01": image_path}

        # Current frame + caption + prompt match the stored (signed) evidence -> accepted.
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), [])

        # Frame swapped after review -> stale evidence must block the render.
        image_path.write_bytes(b"tampered-frame-bytes-v2-different")
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), ["B01"])

    def test_stale_caption_after_review_is_blocked(self) -> None:
        # B-stale (BLOCKER-4): an edited caption must invalidate the previously
        # approved, signed evidence even when the image and prompt are unchanged.
        image_path = self.root / "06_visual_production" / "SCENE_ASSETS" / "B01.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"original-frame-bytes-v1")

        evidence = review_shot(
            shot_id="B01", image_path=image_path,
            caption_text=CAPTION, prompt_text=PROMPT, provider=_KeyedStubProvider("match"),
        )
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=evidence.to_dict()),
        ))
        assets_by_task = {"B01": image_path}

        # Current caption matches -> accepted.
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), [])

        # Caption edited after review -> stale evidence must block the render.
        # Rewrite only B01's caption; B02/B03 are unchanged so they stay valid.
        p = self.root / "05_director" / "IMAGE_TASKS.jsonl"
        p.write_text("\n".join([
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B01", "scene_id": "B01", "caption_text": "EDITED-CAPTION", "prompt": PROMPT}, ensure_ascii=False),
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B02", "scene_id": "B02", "caption_text": CAPTION, "prompt": PROMPT}, ensure_ascii=False),
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B03", "scene_id": "B03", "caption_text": CAPTION, "prompt": PROMPT}, ensure_ascii=False),
        ]) + "\n", encoding="utf-8")
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), ["B01"])

    def test_stale_prompt_after_review_is_blocked(self) -> None:
        # C-stale (BLOCKER-4): a changed image prompt must invalidate the
        # previously approved, signed evidence even when the image and caption
        # are unchanged.
        image_path = self.root / "06_visual_production" / "SCENE_ASSETS" / "B01.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"original-frame-bytes-v1")

        evidence = review_shot(
            shot_id="B01", image_path=image_path,
            caption_text=CAPTION, prompt_text=PROMPT, provider=_KeyedStubProvider("match"),
        )
        path = self._write(_decision_document(
            _base_decision("B01", vision_evidence=evidence.to_dict()),
        ))
        assets_by_task = {"B01": image_path}

        # Current prompt matches -> accepted.
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), [])

        # Prompt edited after review -> stale evidence must block the render.
        p = self.root / "05_director" / "IMAGE_TASKS.jsonl"
        p.write_text("\n".join([
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B01", "scene_id": "B01", "caption_text": CAPTION, "prompt": "EDITED-PROMPT"}, ensure_ascii=False),
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B02", "scene_id": "B02", "caption_text": CAPTION, "prompt": PROMPT}, ensure_ascii=False),
            json.dumps({"schema_version": "production-image-task.v1", "task_id": "B03", "scene_id": "B03", "caption_text": CAPTION, "prompt": PROMPT}, ensure_ascii=False),
        ]) + "\n", encoding="utf-8")
        self.assertEqual(_scene_review_vision_blockers(path, assets_by_task), ["B01"])


# Imported at the bottom so the module-level helpers above are defined first and
# the import cost is only paid when the test class is collected.
from book_video_factory.render_stage.preflight import (  # noqa: E402
    RenderPreflightError,
    _scene_review_vision_blockers,
)


if __name__ == "__main__":
    unittest.main()
