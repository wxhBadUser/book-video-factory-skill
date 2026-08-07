"""§10.3 / §13.2 provider policy.

Two distinct gates:

* ``project.validate_provider`` — the scene-image *generation* provider gate.
  huozhe-r1 shipped 173 assets on ``gemini-web`` / ``flow-web``; those strings
  must now fail closed so no NEW artifact can be produced by an unvetted
  provider. Only the sanctioned host provider is allowed for new work.
* ``vision_review.load_vision_review_provider`` — reads
  ``qa/vision_review_provider.json`` and validates the declared vision-review
  provider against the vision allowlist, refusing the image-generation
  providers and any unknown string.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.project import ProviderPolicyError, validate_provider
from book_video_factory.semantic_alignment.vision_review import (
    UntrustedProviderError,
    VisionReviewError,
    load_vision_review_provider,
)


class GenerationProviderGateTests(unittest.TestCase):
    def test_sanctioned_host_provider_is_accepted(self) -> None:
        self.assertEqual(validate_provider("host-imagegen"), "host-imagegen")

    def test_gemini_web_fails_closed(self) -> None:
        with self.assertRaises(ProviderPolicyError):
            validate_provider("gemini-web")

    def test_flow_web_fails_closed(self) -> None:
        with self.assertRaises(ProviderPolicyError):
            validate_provider("flow-web")

    def test_empty_provider_fails_closed(self) -> None:
        with self.assertRaises(ProviderPolicyError):
            validate_provider("")

    def test_unknown_provider_fails_closed(self) -> None:
        with self.assertRaises(ProviderPolicyError):
            validate_provider("some-random-web")


class VisionReviewProviderDeclarationTests(unittest.TestCase):
    def _write(self, payload: object) -> Path:
        temp = Path(tempfile.mkdtemp())
        qa = temp / "qa"
        qa.mkdir(parents=True)
        (qa / "vision_review_provider.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return temp

    def test_declared_allowlisted_provider_is_returned(self) -> None:
        root = self._write(
            {
                "schema_version": "vision-review-provider.v1",
                "release_id": "r1",
                "active_provider": "claude-sonnet-4.5",
                "approved_by": "human",
            }
        )
        self.assertEqual(load_vision_review_provider(root), "claude-sonnet-4.5")

    def test_image_generation_provider_cannot_review(self) -> None:
        root = self._write(
            {
                "schema_version": "vision-review-provider.v1",
                "release_id": "r1",
                "active_provider": "gemini-web",
                "approved_by": "human",
            }
        )
        with self.assertRaises(UntrustedProviderError):
            load_vision_review_provider(root)

    def test_missing_declaration_fails_closed(self) -> None:
        root = Path(tempfile.mkdtemp())
        with self.assertRaises(VisionReviewError):
            load_vision_review_provider(root)

    def test_wrong_schema_fails_closed(self) -> None:
        root = self._write(
            {
                "schema_version": "vision-review-provider.v99",
                "active_provider": "claude-sonnet-4.5",
            }
        )
        with self.assertRaises(VisionReviewError):
            load_vision_review_provider(root)


if __name__ == "__main__":
    unittest.main()
