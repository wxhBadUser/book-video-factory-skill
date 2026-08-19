"""L13 adversarial test: §8 visual-lag kill switch must fail closed.

``compile_final_storyboard`` embeds a content hash of the narration
(``audio_meta_sha256``) into every scene; ``validate_visual_proposition_currency``
then blocks advancement when the current narration hashes to a different value
(i.e. the narration was edited after the visual proposition was frozen).

The freeze is content-based and wall-clock-free, so recompiling identical inputs
produces a byte-identical storyboard (the previous ``proposition_frozen_at``
wall-clock stamp broke that invariant).

Mutation criterion (plan §0): on committed pre-remediation HEAD the
``validate_visual_proposition_currency`` function and ``VisualPropositionStaleError``
do not exist, so this module fails collection (ImportError) -- RED there.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from book_video_factory.audio_stage.storyboard_plan import (
    VisualPropositionStaleError,
    _audio_meta_sha256,
    compile_final_storyboard,
    validate_visual_proposition_currency,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _caption(caption_id: str, *, narrative_function: str = "plot", text: str | None = None) -> dict:
    if text is None:
        text = f"字幕 {caption_id}"
    return {
        "id": caption_id,
        "start": 0.0, "end": 3.0, "duration": 3.0,
        "text": text,
        "text_sha256": _sha(text.encode("utf-8")),
        "restoration_status": "display-restored",
        "allowShort": True,
        "narrative_function": narrative_function,
    }


def _minimal_plan_and_meta() -> tuple[dict, dict, list[dict]]:
    captions = [_caption("caption-0001")]
    audio_meta = {
        "opening": {"bodyStart": 0.0},
        "body": {"duration": 6.0},
        "captions": captions,
    }
    plan = {
        "release_id": "r1",
        "shots": [{
            "id": "as001",
            "source_beat_ids": ["B001"],
            "chapter": 1,
            "cue": "字幕 caption-0001",
            "caption_ids": ["caption-0001"],
            "required_entities": ["X"],
            "description": "测试场景",
            "forbidden_entities": [],
            "risk_flags": [],
            "generation_mode": "single",
            "anchor_refs": [],
            "participants": {"count": 0, "allowed": []},
            "motion": "hold",
            "intentional_hold": False,
            "hold_reason": "",
            "semantic_rationale": "字幕与画面共享当前场景",
            "relative_start": 0.0,
            "relative_end": 3.0,
        }],
    }
    phase2_beats = [{"beatId": "B001", "chapter": 1, "requiredEntities": ["X"], "cue": "第一段", "description": "x", "forbiddenEntities": [], "riskFlags": [], "generationMode": "single", "anchorRefs": [], "participants": {"count": 0, "allowed": []}, "motion": "hold"}]
    return plan, audio_meta, phase2_beats


class VisualPropositionCurrencyTests(unittest.TestCase):
    def test_compile_final_storyboard_embeds_audio_meta_sha256(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        self.assertTrue(storyboard)
        self.assertIn("audio_meta_sha256", storyboard[0])
        stamp = storyboard[0]["audio_meta_sha256"]
        self.assertIsInstance(stamp, str)
        self.assertEqual(len(stamp), 64)
        # The embedded stamp is the deterministic content hash of the narration.
        self.assertEqual(stamp, _audio_meta_sha256(meta))
        # The removed wall-clock field must no longer be present.
        self.assertNotIn("proposition_frozen_at", storyboard[0])

    def test_stale_audio_meta_raises(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        # Narration content was edited after the proposition was frozen.
        stale_meta = {**meta, "captions": [_caption("caption-0001", text="篡改后的旁白文本")]}
        self.assertNotEqual(_audio_meta_sha256(stale_meta), _audio_meta_sha256(meta))
        with self.assertRaises(VisualPropositionStaleError):
            validate_visual_proposition_currency(storyboard, stale_meta)

    def test_unchanged_audio_meta_passes(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        # Same narration content -> same hash -> currency certified.
        validate_visual_proposition_currency(storyboard, meta)  # no raise

    def test_missing_frozen_stamp_fails_closed(self) -> None:
        storyboard = [{"id": "as001", "narrative_function": "plot"}]
        with self.assertRaises(VisualPropositionStaleError):
            validate_visual_proposition_currency(storyboard, {"captions": []})

    def test_recompile_is_byte_identical(self) -> None:
        # Chain 6 regression: identical input must recompile to a byte-identical
        # storyboard. A wall-clock stamp previously broke this invariant.
        plan, meta, beats = _minimal_plan_and_meta()
        first, _ = compile_final_storyboard(plan, meta, beats)
        second, _ = compile_final_storyboard(plan, meta, beats)
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )
        self.assertEqual(first[0]["audio_meta_sha256"], second[0]["audio_meta_sha256"])


if __name__ == "__main__":
    unittest.main()
