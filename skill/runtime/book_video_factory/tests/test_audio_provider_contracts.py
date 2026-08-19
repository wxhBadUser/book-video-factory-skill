"""M4A provider-policy contract tests (A1-A3).

A1 minimax_required cannot silently fall back to Edge-TTS.
A2 missing MiniMax credentials fail closed (named env var, no fallback).
A3 Edge-TTS is explicitly legacy only (no implicit or cross-policy use).
"""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_audio_stage_input,
)
from book_video_factory.audio_stage.providers import (
    EdgeNarrationProvider,
    MiniMaxNarrationProvider,
    NarrationChunkRequest,
    NarrationCredentialError,
    NarrationProviderError,
    PROVIDER_POLICY_LEGACY_EDGE,
    PROVIDER_POLICY_MINIMAX_REQUIRED,
)


def _minimal_payload(*, provider: str = "edge-tts", provider_policy: str | None = None) -> dict:
    payload = {
        "schema_version": "audio-stage-input.v1",
        "release_id": "r1",
        "provider": provider,
        "voice": "zh-CN-YunjianNeural" if provider == "edge-tts" else "voice_test_0001",
        "body_rate": "+0%",
        "lead_rate": "+0%",
        "reveal_rate": "+0%",
        "pitch": "+0Hz",
        "lead_text": "名著值得读，但很多人读不进去。",
        "reveal_text": "《老人与海》，海明威。",
        "caption_min_chars": 6,
        "caption_max_chars": 18,
        "caption_min_duration": 0.55,
        "lead_start": 0.05,
        "flash_gap_after_lead": 0.02,
        "flash_duration": 1.667,
        "reveal_hold": 0.12,
        "body_gap": 0.22,
        "body_mode": "continuous",
        "bindings": {
            "script_md_sha256": "0" * 64,
            "project_spec_sha256": "0" * 64,
            "hbg_style_sha256": "0" * 64,
            "storyboard_base_sha256": "0" * 64,
            "visual_approval_sha256": "0" * 64,
            "pronunciation_lexicon_sha256": "0" * 64,
        },
    }
    if provider_policy is not None:
        payload["provider_policy"] = provider_policy
    return payload


class ProviderPolicyContractTests(unittest.TestCase):
    def test_A1_minimax_required_never_falls_back_to_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Edge provider under minimax_required policy must fail closed.
            with self.assertRaises(AudioStageContractError):
                validate_audio_stage_input(
                    root, _minimal_payload(provider="edge-tts", provider_policy="minimax_required")
                )
            # MiniMax provider under legacy_edge policy must fail closed.
            with self.assertRaises(AudioStageContractError):
                validate_audio_stage_input(
                    root, _minimal_payload(provider="minimax", provider_policy="legacy_edge")
                )

    def test_A1_policy_defaults_follow_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Legacy inputs without provider_policy keep working as legacy_edge.
            payload = _minimal_payload(provider="edge-tts")
            error = None
            try:
                validate_audio_stage_input(root, payload)
            except AudioStageContractError as exc:
                # The failure must be the stale bindings, not a policy mismatch.
                error = str(exc)
            self.assertIsNotNone(error)
            self.assertNotIn("provider_policy", error)
            self.assertNotIn("provider/provider_policy mismatch", error)
            # The failure must be the missing evidence files (bindings check),
            # proving the policy default did not block the legacy payload.
            self.assertIn("missing or unsafe", error)

    def test_A2_missing_minimax_key_fails_closed(self) -> None:
        # A developer machine may legitimately carry a real production key.
        # This contract checks the *absence* case, so isolate the process env
        # rather than accidentally reading that key through ``api_key=None``.
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": ""}, clear=False):
            provider = MiniMaxNarrationProvider(voice_id="voice_test_0001", api_key=None)
            with self.assertRaises(NarrationCredentialError) as caught:
                provider.preflight()
        self.assertIn("MINIMAX_API_KEY", str(caught.exception))

    def test_A2_present_key_passes_preflight(self) -> None:
        provider = MiniMaxNarrationProvider(
            voice_id="voice_test_0001",
            api_key="test-key-not-persisted",
            api_base="https://example.invalid",
        )
        provider.preflight()  # must not raise
        provider.close()

    def test_A2_empty_voice_id_fails_closed(self) -> None:
        provider = MiniMaxNarrationProvider(voice_id="  ", api_key="test-key")
        with self.assertRaises(NarrationCredentialError):
            provider.preflight()

    def test_A3_edge_is_explicit_legacy_only(self) -> None:
        # Constructing an Edge provider under minimax_required is rejected.
        with self.assertRaises(NarrationProviderError):
            EdgeNarrationProvider(policy=PROVIDER_POLICY_MINIMAX_REQUIRED)
        edge = EdgeNarrationProvider(policy=PROVIDER_POLICY_LEGACY_EDGE)
        edge.preflight()  # legacy marker is fine
        # The new provider pipeline must never synthesize Edge chunks.
        with self.assertRaises(NarrationProviderError):
            edge.synthesize(NarrationChunkRequest(
                chunk_id="c1",
                text="测试",
                provider="edge-tts",
                model="edge",
                voice_id="zh-CN-YunjianNeural",
            ))

    def test_A2_provider_digest_is_secret_free(self) -> None:
        provider = MiniMaxNarrationProvider(voice_id="voice_test_0001", api_key="sk-secret-value")
        request = NarrationChunkRequest(
            chunk_id="c1",
            text="测试旁白",
            provider="minimax",
            model="speech-2.8-hd",
            voice_id="voice_test_0001",
            sound_tags=["sighs"],
        )
        payload = provider.build_t2a_payload(request)
        self.assertNotIn("sk-secret-value", str(payload))
        self.assertNotIn("api_key", str(payload).lower().replace("pitch", ""))
        self.assertEqual(request.digest(), provider.request_digest(request))


if __name__ == "__main__":
    unittest.main()
