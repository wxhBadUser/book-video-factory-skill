"""L8 adversarial test: a Voice Performance Plan must block narration render until approved.

Drives the public gate ``_enforce_voice_audition_gate`` (which ``finalize_audio_stage``
now calls before any HBG work). A project that opts into a VPP but lacks an
explicit audition approval must raise ``AudioStageError``; a project that never
opted in must pass untouched.

Mutation criterion (plan §0): on committed pre-remediation code the gate helper
does not exist and ``finalize_audio_stage`` never enforces an audition, so this
test is RED (ImportError / no block).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.compiler import (
    AudioStageError,
    _enforce_voice_audition_gate,
)


class VoiceAuditionGateTests(unittest.TestCase):
    def test_unapproved_vpp_blocks_render(self) -> None:
        root = Path(tempfile.mkdtemp())
        # A Voice Performance Plan opts the project in...
        (root / "04_audio").mkdir(parents=True)
        (root / "04_audio/VOICE_PERFORMANCE_PLAN.json").write_text(
            json.dumps({"schema_version": "voice-performance-plan.v1", "captions": {}}),
            encoding="utf-8",
        )
        # ...but no audition approval exists -> block.
        with self.assertRaises(AudioStageError):
            _enforce_voice_audition_gate(root)

    def test_no_vpp_is_not_gated(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(_enforce_voice_audition_gate(root))

    def test_approved_vpp_passes(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "04_audio").mkdir(parents=True)
        (root / "04_audio/VOICE_PERFORMANCE_PLAN.json").write_text(
            json.dumps({"schema_version": "voice-performance-plan.v1", "captions": {}}),
            encoding="utf-8",
        )
        (root / "04_audio/VOICE_AUDITION_APPROVAL.json").write_text(
            json.dumps({
                "schema_version": "voice-audition-approval.v1",
                "release_id": "r1",
                "voice_performance_plan_sha256": "e" * 64,
                "reviewer": "human-reviewer",
                "status": "approved",
                "note": "auditioned voice performance approved for full TTS",
            }),
            encoding="utf-8",
        )
        # Should not raise; returns the validated approval record.
        approval = _enforce_voice_audition_gate(root)
        self.assertIsInstance(approval, dict)
        self.assertEqual(approval.get("status"), "approved")


if __name__ == "__main__":
    unittest.main()
