"""Part 5 - real vision review evidence, fail-closed.

The old pipeline let ``review.py`` and ``encoded_visual_qa.py`` pass an image by
reading a JSON status field, never the pixels. This file pins the replacement:

* a vision provider is only trusted if it is on the vision allowlist, and no
  image-generation provider may ever be used as a vision reviewer;
* a review is only valid if the provider was actually handed the image bytes -
  a text-only provider that "claims pass" without pixels fails closed;
* every review binds ``image_sha256``, ``caption_sha256`` and ``prompt_sha256``,
  so replacing the image, editing the caption or changing the prompt makes the
  stored evidence stale;
* a missing provider yields ``blocked_by_missing_vision_provider`` - the system
  never fabricates a verdict;
* a review decision without vision evidence is never accepted - ``legacy_pass``
  no longer substitutes for evidence, and anything else refuses to advance.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from book_video_factory.semantic_alignment.vision_review import (
    PARITY_VERDICTS,
    BaseVisionProvider,
    MissingVisionEvidenceError,
    NullVisionProvider,
    ParityResult,
    StaleVisionEvidenceError,
    UntrustedProviderError,
    VisionEvidence,
    VisionProviderError,
    VisionReviewError,
    build_vision_evidence_document,
    load_vision_evidence_document,
    review_shot,
    validate_review_decision,
    validate_vision_provider,
    verify_evidence_current,
)

CAPTION = "家珍的手一点点凉了下去。"
PROMPT = "[1/9 CAPTION]\n家珍的手一点点凉了下去。\n..."


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _StubVisionProvider(BaseVisionProvider):
    """A trusted multimodal stub that records that it received pixels."""

    name = "claude-sonnet-4.5"

    def __init__(self, verdict: str = "match", reasoning: str = "画面与台词一致，可见垂危的家珍与握着的手。") -> None:
        self.verdict = verdict
        self.reasoning = reasoning
        self.saw_pixels = False
        self.pixel_len = 0

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        self.saw_pixels = True
        self.pixel_len = len(image_bytes)
        return ParityResult(
            verdict=self.verdict,
            reasoning=self.reasoning,
            call_id="ve-2026-08-07T10-32-01-7c1d",
        )


class _NoCallIdProvider(_StubVisionProvider):
    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        return ParityResult(verdict="match", reasoning="ok", call_id="")


def _write_image(tmp: Path, payload: bytes = b"\x89PNG\r\n\x1a\nfake-pixels") -> Path:
    path = tmp / "shot.png"
    path.write_bytes(payload)
    return path


class ProviderPolicyTests(unittest.TestCase):
    def test_allowlisted_provider_is_accepted(self) -> None:
        self.assertEqual(validate_vision_provider("claude-sonnet-4.5"), "claude-sonnet-4.5")

    def test_image_generation_provider_is_forbidden_as_reviewer(self) -> None:
        for banned in ("host-imagegen", "gemini-web", "flow-web"):
            with self.subTest(provider=banned):
                with self.assertRaises(UntrustedProviderError):
                    validate_vision_provider(banned)

    def test_unknown_provider_fails_closed(self) -> None:
        with self.assertRaises(UntrustedProviderError):
            validate_vision_provider("text-only-model")

    def test_empty_provider_fails_closed(self) -> None:
        with self.assertRaises(UntrustedProviderError):
            validate_vision_provider("")


class VisionEvidenceModelTests(unittest.TestCase):
    def _evidence(self, **overrides) -> VisionEvidence:
        base = dict(
            shot_id="shot-b117-01",
            vision_provider="claude-sonnet-4.5",
            call_id="ve-1",
            image_sha256="a" * 64,
            caption_sha256="b" * 64,
            prompt_sha256="c" * 64,
            parity_verdict="match",
            parity_reasoning="画面与台词一致。",
            reviewed_pixels=True,
        )
        base.update(overrides)
        return VisionEvidence(**base)

    def test_round_trip_is_lossless(self) -> None:
        evidence = self._evidence()
        self.assertEqual(VisionEvidence.from_mapping(evidence.to_dict()), evidence)

    def test_verdict_must_be_in_the_enum(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._evidence(parity_verdict="great").validate()

    def test_hashes_must_be_64_hex(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._evidence(image_sha256="abc").validate()

    def test_reasoning_must_be_substantive(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._evidence(parity_reasoning="ok").validate()

    def test_a_review_that_did_not_read_pixels_is_invalid(self) -> None:
        with self.assertRaises(VisionReviewError):
            self._evidence(reviewed_pixels=False).validate()

    def test_all_declared_verdicts_validate(self) -> None:
        for verdict in PARITY_VERDICTS:
            with self.subTest(verdict=verdict):
                self._evidence(
                    parity_verdict=verdict,
                    parity_reasoning="画面与台词的对齐结论已由视觉模型给出并说明。",
                ).validate()


class ReviewShotTests(unittest.TestCase):
    def test_review_binds_the_actual_image_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            image = _write_image(tmp)
            provider = _StubVisionProvider()
            evidence = review_shot(
                shot_id="shot-b117-01",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=provider,
            )
            self.assertTrue(provider.saw_pixels, "the provider must be handed the pixels")
            self.assertEqual(evidence.image_sha256, hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual(evidence.caption_sha256, _sha_text(CAPTION))
            self.assertEqual(evidence.prompt_sha256, _sha_text(PROMPT))
            self.assertTrue(evidence.reviewed_pixels)
            evidence.validate()

    def test_missing_image_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "nope.png"
            with self.assertRaises(VisionReviewError):
                review_shot(
                    shot_id="s",
                    image_path=missing,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=_StubVisionProvider(),
                )

    def test_empty_image_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            image = _write_image(tmp, payload=b"")
            with self.assertRaises(VisionProviderError):
                review_shot(
                    shot_id="s",
                    image_path=image,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=_StubVisionProvider(),
                )

    def test_provider_without_call_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            with self.assertRaises(VisionReviewError):
                review_shot(
                    shot_id="s",
                    image_path=image,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=_NoCallIdProvider(),
                )

    def test_forbidden_provider_cannot_review(self) -> None:
        class _Banned(_StubVisionProvider):
            name = "gemini-web"

        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            with self.assertRaises(UntrustedProviderError):
                review_shot(
                    shot_id="s",
                    image_path=image,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=_Banned(),
                )

    def test_null_provider_reports_blocked_not_fabricated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            with self.assertRaises(VisionProviderError) as ctx:
                review_shot(
                    shot_id="s",
                    image_path=image,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=NullVisionProvider(),
                )
            self.assertIn("blocked_by_missing_vision_provider", str(ctx.exception))


class StalenessTests(unittest.TestCase):
    def _evidence_for(self, image: Path) -> VisionEvidence:
        return review_shot(
            shot_id="shot-b117-01",
            image_path=image,
            caption_text=CAPTION,
            prompt_text=PROMPT,
            provider=_StubVisionProvider(),
        )

    def test_current_evidence_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = self._evidence_for(image)
            verify_evidence_current(evidence, image_path=image, caption_text=CAPTION, prompt_text=PROMPT)

    def test_replaced_image_makes_evidence_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = self._evidence_for(image)
            image.write_bytes(b"\x89PNG\r\n\x1a\ndifferent-pixels")
            with self.assertRaises(StaleVisionEvidenceError):
                verify_evidence_current(evidence, image_path=image, caption_text=CAPTION, prompt_text=PROMPT)

    def test_edited_caption_makes_evidence_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = self._evidence_for(image)
            with self.assertRaises(StaleVisionEvidenceError):
                verify_evidence_current(
                    evidence, image_path=image, caption_text="福贵坐在门槛上。", prompt_text=PROMPT
                )

    def test_changed_prompt_makes_evidence_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = self._evidence_for(image)
            with self.assertRaises(StaleVisionEvidenceError):
                verify_evidence_current(
                    evidence, image_path=image, caption_text=CAPTION, prompt_text=PROMPT + "\nextra"
                )


class DecisionGateTests(unittest.TestCase):
    def test_decision_with_vision_evidence_is_accepted(self) -> None:
        decision = {
            "shot_id": "s",
            "vision_evidence": {
                "vision_provider": "claude-sonnet-4.5",
                "call_id": "ve-1",
                "image_sha256": "a" * 64,
                "caption_sha256": "b" * 64,
                "prompt_sha256": "c" * 64,
                "parity_verdict": "match",
                "parity_reasoning": "画面与台词一致，指称可见。",
                "reviewed_pixels": True,
            },
        }
        validate_review_decision(decision)

    def test_legacy_pass_without_vision_is_rejected(self) -> None:
        # legacy_pass no longer substitutes for vision evidence at the gate.
        with self.assertRaises(MissingVisionEvidenceError):
            validate_review_decision({"shot_id": "s", "legacy_pass": True})

    def test_missing_vision_and_no_legacy_flag_fails_closed(self) -> None:
        with self.assertRaises(MissingVisionEvidenceError):
            validate_review_decision({"shot_id": "s"})

    def test_mismatch_verdict_is_not_a_pass(self) -> None:
        decision = {
            "shot_id": "s",
            "vision_evidence": {
                "vision_provider": "claude-sonnet-4.5",
                "call_id": "ve-1",
                "image_sha256": "a" * 64,
                "caption_sha256": "b" * 64,
                "prompt_sha256": "c" * 64,
                "parity_verdict": "mismatch",
                "parity_reasoning": "画面是抱孩子的老人，台词是家珍垂死，完全不符。",
                "reviewed_pixels": True,
            },
        }
        with self.assertRaises(VisionReviewError):
            validate_review_decision(decision, require_pass=True)


class EvidenceDocumentTests(unittest.TestCase):
    def test_document_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = review_shot(
                shot_id="shot-b117-01",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=_StubVisionProvider(),
            )
            doc = build_vision_evidence_document("huozhe-r1", [evidence])
            self.assertEqual(doc["schema_version"], "vision-evidence.v1")
            self.assertEqual(doc["release_id"], "huozhe-r1")
            loaded = load_vision_evidence_document(doc)
            self.assertEqual(loaded["shot-b117-01"], evidence)


if __name__ == "__main__":
    unittest.main()
