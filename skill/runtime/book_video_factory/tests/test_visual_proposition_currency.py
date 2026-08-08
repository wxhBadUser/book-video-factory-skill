"""L13 adversarial test: §8 visual-lag kill switch must fail closed.

``compile_final_storyboard`` freezes a wall-clock ``proposition_frozen_at`` on
every scene; ``validate_visual_proposition_currency`` then blocks advancement
when the narration (``audio_meta.last_modified``) was edited after that freeze.

Mutation criterion (plan §0): on committed pre-remediation HEAD the
``validate_visual_proposition_currency`` function and ``VisualPropositionStaleError``
do not exist, so this module fails collection (ImportError) -- RED there.
"""

from __future__ import annotations

import hashlib
import unittest

from book_video_factory.audio_stage.storyboard_plan import (
    VisualPropositionStaleError,
    compile_final_storyboard,
    validate_visual_proposition_currency,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _caption(caption_id: str, *, narrative_function: str = "plot") -> dict:
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
            "motion": "zoom-in",
            "intentional_hold": False,
            "hold_reason": "",
            "semantic_rationale": "字幕与画面共享当前场景",
            "relative_start": 0.0,
            "relative_end": 3.0,
        }],
    }
    phase2_beats = [{"beatId": "B001", "chapter": 1, "requiredEntities": ["X"], "cue": "第一段", "description": "x", "forbiddenEntities": [], "riskFlags": [], "generationMode": "single", "anchorRefs": [], "participants": {"count": 0, "allowed": []}, "motion": "static"}]
    return plan, audio_meta, phase2_beats


class VisualPropositionCurrencyTests(unittest.TestCase):
    def test_compile_final_storyboard_stamps_frozen_at(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        self.assertTrue(storyboard)
        self.assertIn("proposition_frozen_at", storyboard[0])
        self.assertIsInstance(storyboard[0]["proposition_frozen_at"], str)

    def test_stale_audio_meta_raises(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        # Narration edited well after the plan was frozen.
        stale_meta = {**meta, "last_modified": "2099-01-01T00:00:00+00:00"}
        with self.assertRaises(VisualPropositionStaleError):
            validate_visual_proposition_currency(storyboard, stale_meta)

    def test_current_audio_meta_passes(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        # Narration untouched (or older than the freeze).
        current_meta = {**meta, "last_modified": "2000-01-01T00:00:00+00:00"}
        validate_visual_proposition_currency(storyboard, current_meta)  # no raise

    def test_missing_last_modified_passes(self) -> None:
        plan, meta, beats = _minimal_plan_and_meta()
        storyboard, _ = compile_final_storyboard(plan, meta, beats)
        validate_visual_proposition_currency(storyboard, meta)  # no last_modified -> no raise

    def test_missing_frozen_stamp_fails_closed(self) -> None:
        storyboard = [{"id": "as001", "narrative_function": "plot"}]
        with self.assertRaises(VisualPropositionStaleError):
            validate_visual_proposition_currency(storyboard, {"last_modified": "2099-01-01T00:00:00+00:00"})


if __name__ == "__main__":
    unittest.main()
