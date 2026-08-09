"""Chain 4 (re-review #23): the voice-audition approval must be cryptographically
pinned to the exact ``VOICE_PERFORMANCE_PLAN.json`` bytes and must block BOTH the
preliminary and final narration render.

The re-review's core charge: an audition approval must not be a free-floating
"approved" flag that stays valid after the plan is edited. The gate therefore
compares ``SHA256(VOICE_PERFORMANCE_PLAN.json)`` against the digest the approval
records; any mismatch (missing approval, or a changed plan) fails closed and
blocks the whole narration render.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.compiler import (
    AudioStageError,
    _enforce_voice_audition_gate,
)

_VALID_VPP = {
    "schema_version": "voice-performance-plan.v1",
    "release_id": "r1",
    "audio_meta_sha256": "a" * 64,
    "status": "approved",
    "captions": {
        "caption-0001": {
            "rate": "+0%",
            "pitch": "+0Hz",
            "pause_ms_before": 0,
            "pause_ms_after": 0,
            "emphasis_words": [],
        },
    },
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VoiceAuditionGateTests(unittest.TestCase):
    def _project(self, tmp: str, *, vpp: dict | None, approval_sha: str | None) -> Path:
        root = Path(tmp)
        audio = root / "04_audio"
        audio.mkdir(parents=True, exist_ok=True)
        if vpp is not None:
            vpp_path = audio / "VOICE_PERFORMANCE_PLAN.json"
            vpp_path.write_text(json.dumps(vpp, ensure_ascii=False), encoding="utf-8")
        if approval_sha is not None:
            approval = {
                "schema_version": "voice-audition-approval.v1",
                "release_id": "r1",
                "voice_performance_plan_sha256": approval_sha,
                "reviewer": "reviewer-x",
                "status": "approved",
                "note": "",
            }
            (audio / "VOICE_AUDITION_APPROVAL.json").write_text(
                json.dumps(approval, ensure_ascii=False), encoding="utf-8"
            )
        return root

    def test_no_vpp_means_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, vpp=None, approval_sha=None)
            self.assertIsNone(_enforce_voice_audition_gate(root))

    def test_vpp_with_matching_approval_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, vpp=_VALID_VPP, approval_sha=None)
            current = _sha256_file(root / "04_audio" / "VOICE_PERFORMANCE_PLAN.json")
            # Re-write approval with the matching digest.
            self._project(tmp, vpp=_VALID_VPP, approval_sha=current)
            approval = _enforce_voice_audition_gate(root)
            self.assertIsNotNone(approval)
            self.assertEqual(approval["status"], "approved")
            self.assertEqual(approval["voice_performance_plan_sha256"], current)

    def test_vpp_without_approval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, vpp=_VALID_VPP, approval_sha=None)
            with self.assertRaises(AudioStageError):
                _enforce_voice_audition_gate(root)

    def test_vpp_with_wrong_approval_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, vpp=_VALID_VPP, approval_sha="b" * 64)
            with self.assertRaises(AudioStageError):
                _enforce_voice_audition_gate(root)

    def test_edited_vpp_invalidates_prior_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, vpp=_VALID_VPP, approval_sha=None)
            original = _sha256_file(root / "04_audio" / "VOICE_PERFORMANCE_PLAN.json")
            self._project(tmp, vpp=_VALID_VPP, approval_sha=original)
            # Sanity: original plan is approved.
            self.assertIsNotNone(_enforce_voice_audition_gate(root))
            # Now edit the plan (one extra caption) and re-pin nothing.
            edited = dict(_VALID_VPP)
            edited["captions"]["caption-0002"] = {
                "rate": "+5%",
                "pitch": "+2Hz",
                "pause_ms_before": 100,
                "pause_ms_after": 100,
                "emphasis_words": ["命运"],
            }
            self._project(tmp, vpp=edited, approval_sha=original)
            with self.assertRaises(AudioStageError):
                _enforce_voice_audition_gate(root)


if __name__ == "__main__":
    unittest.main()
