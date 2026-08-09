"""Part 5 - real, cryptographically-bound vision review evidence, fail-closed.

The historical gap this file closes: a ``VisionEvidence`` used to be a JSON that
anyone could hand-author. A dict claiming ``vision_provider="claude-sonnet-4.5"``
was accepted with no real model call behind it. That shortcut is gone:

* a ``VisionEvidence`` is only valid if it carries a signature minted by a
  ``BaseVisionProvider`` that held the signing secret for its ``vision_provider``
  AND actually reviewed these exact pixels/caption/prompt;
* a hand-authored dict (even one naming ``claude-sonnet-4.5``) has no valid
  signature and is rejected by ``verify`` / ``validate_review_decision``;
* a review binds ``image_sha256`` / ``caption_sha256`` / ``prompt_sha256`` so any
  drift makes it stale;
* a missing provider yields ``blocked_by_missing_vision_provider`` - the system
  never fabricates a verdict;
* a review decision without verifiable evidence is never accepted.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from book_video_factory.semantic_alignment.vision_review import (
    PARITY_VERDICTS,
    LocalVisionProvider,
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


class _KeyedStubProvider(LocalVisionProvider):
    """A local, keyed stub that returns a caller-chosen verdict.

    It inherits the real signing machinery from ``LocalVisionProvider`` so the
    evidence it mints is genuinely verifiable; only the verdict/reasoning are
    overridden for the test scenario. Inheriting ``LocalVisionProvider`` also
    means its signing secret is registered at import, so its records verify.
    """

    name = "local-vision-stub"

    def __init__(self, verdict: str = "match", reasoning: str = "画面与台词一致，可见垂危的家珍与握着的手。") -> None:
        self.verdict = verdict
        self.reasoning = reasoning
        self.saw_pixels = False
        self.pixel_len = 0

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str) -> ParityResult:
        self.saw_pixels = True
        self.pixel_len = len(image_bytes)
        call_id = hashlib.sha256(image_bytes + str(caption_text).encode("utf-8")).hexdigest()[:24]
        return ParityResult(verdict=self.verdict, reasoning=self.reasoning, call_id=call_id)


class _NoCallIdProvider(_KeyedStubProvider):
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

    # --- The old "any JSON claiming Claude saw it" gap is now closed by verify().
    def test_hand_forged_claude_evidence_without_signature_fails_verify(self) -> None:
        # No signing key is registered for claude-sonnet-4.5 in this tree, so a
        # hand-authored record (the old shortcut to a green test) is rejected.
        with self.assertRaises(VisionReviewError):
            self._evidence().verify()

    def test_hand_forged_evidence_with_wrong_signature_fails_verify(self) -> None:
        forged = self._evidence(provider_signature="deadbeef" * 8)
        with self.assertRaises(VisionReviewError):
            forged.verify()

    def test_tampering_image_hash_invalidates_signature(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = review_shot(
                shot_id="shot-b117-01",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=_KeyedStubProvider(),
            )
        # Tamper the stored image hash; the signature must no longer match.
        tampered = replace(evidence, image_sha256="a" * 64)
        with self.assertRaises(VisionReviewError):
            tampered.verify()

    def test_spoofing_provider_name_invalidates_signature(self) -> None:
        # A genuinely-signed local record cannot be rebranded as claude-sonnet-4.5:
        # the provider identity is part of the signed payload.
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = review_shot(
                shot_id="shot-b117-01",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=_KeyedStubProvider(),
            )
        spoofed = replace(evidence, vision_provider="claude-sonnet-4.5")
        with self.assertRaises(VisionReviewError):
            spoofed.verify()


class ReviewShotTests(unittest.TestCase):
    def test_review_binds_the_actual_image_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            image = _write_image(tmp)
            provider = _KeyedStubProvider()
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
            # The signed record is verifiable - this is the new guarantee.
            evidence.verify()

    def test_missing_image_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "nope.png"
            with self.assertRaises(VisionReviewError):
                review_shot(
                    shot_id="s",
                    image_path=missing,
                    caption_text=CAPTION,
                    prompt_text=PROMPT,
                    provider=_KeyedStubProvider(),
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
                    provider=_KeyedStubProvider(),
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
        class _Banned(_KeyedStubProvider):
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
            provider=_KeyedStubProvider(),
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
    def _real_decision(self, verdict: str = "match") -> dict:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = review_shot(
                shot_id="s",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=_KeyedStubProvider(verdict=verdict),
            )
        return {"shot_id": "s", "vision_evidence": evidence.to_dict()}

    def test_decision_with_real_signed_evidence_is_accepted(self) -> None:
        validate_review_decision(self._real_decision("match"))

    def test_decision_with_forged_claude_evidence_is_rejected(self) -> None:
        # The exact old shortcut: a hand-authored dict naming claude-sonnet-4.5,
        # with no valid signature. It must be rejected, not green.
        forged = {
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
        with self.assertRaises(VisionReviewError):
            validate_review_decision(forged)

    def test_tampered_real_evidence_is_rejected_at_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            image = _write_image(Path(raw))
            evidence = review_shot(
                shot_id="s",
                image_path=image,
                caption_text=CAPTION,
                prompt_text=PROMPT,
                provider=_KeyedStubProvider(),
            )
        tampered = replace(evidence, image_sha256="a" * 64).to_dict()
        with self.assertRaises(VisionReviewError):
            validate_review_decision({"shot_id": "s", "vision_evidence": tampered})

    def test_legacy_pass_without_vision_is_rejected(self) -> None:
        with self.assertRaises(MissingVisionEvidenceError):
            validate_review_decision({"shot_id": "s", "legacy_pass": True})

    def test_missing_vision_and_no_legacy_flag_fails_closed(self) -> None:
        with self.assertRaises(MissingVisionEvidenceError):
            validate_review_decision({"shot_id": "s"})

    def test_mismatch_verdict_is_not_a_pass(self) -> None:
        decision = self._real_decision("mismatch")
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
                provider=_KeyedStubProvider(),
            )
            doc = build_vision_evidence_document("huozhe-r1", [evidence])
            self.assertEqual(doc["schema_version"], "vision-evidence.v1")
            self.assertEqual(doc["release_id"], "huozhe-r1")
            loaded = load_vision_evidence_document(doc)
            self.assertEqual(loaded["shot-b117-01"], evidence)


if __name__ == "__main__":
    unittest.main()
