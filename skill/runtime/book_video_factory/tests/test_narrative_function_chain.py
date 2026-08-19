"""L7 adversarial test: narrative_function must be propagated and never silently fall back to 'plot'.

Two checks:
1. ``compile_final_storyboard`` emits ``narrative_function`` / ``narrativeFunction``
   on every scene (so the director stage cannot collapse the register).
2. ``director_stage.compiler._scene_narrative_function`` fails closed when the
   field is missing instead of defaulting to ``"plot"``.

Mutation criterion (plan §0): on committed pre-remediation code (1) the final
dict lacks the field and (2) ``_scene_narrative_function`` returns ``"plot"``
for a field-less scene, so both assertions are RED there.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.storyboard_plan import (
    compile_final_storyboard,
    validate_storyboard_audio_plan,
)
from book_video_factory.director_stage.compiler import (
    DirectorStageError,
    _scene_narrative_function,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _caption(index: int, *, narrative_function: str = "plot") -> dict:
    text = f"字幕{index}"
    return {
        "id": f"caption-{index:04d}",
        "start": float(index - 1),
        "end": float(index),
        "duration": 1.0,
        "text": text,
        "text_sha256": _sha(text.encode("utf-8")),
        "restoration_status": "display-restored",
        "allowShort": True,
        "narrative_function": narrative_function,
    }


def _project() -> tuple[Path, dict]:
    project = Path(tempfile.mkdtemp()) / "project"
    project.mkdir()
    preliminary = {
        "schema_version": "audio-preliminary-manifest.v1",
        "release_id": "r1",
        "input_digest": "a" * 64,
        "display_text_sha256": "b" * 64,
        "spoken_text_sha256": "c" * 64,
        "output_hashes": {},
        "media_report": {},
        "tool_provenance": {},
        "external_edge_service_exercised": False,
        "stage_manifest_path": "manifests/stages/audio_preliminary/test.json",
        "stage_manifest_sha256": "d" * 64,
        "next_stage_status": "awaiting_audio_storyboard_plan",
    }
    manifest_path = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(preliminary, sort_keys=True), encoding="utf-8")
    return project, preliminary


def _beats() -> list[dict]:
    return [{
        "id": "s001", "beatId": "B001", "chapter": 1, "cue": "第一段",
        "description": "老人离港", "requiredEntities": ["老人"],
        "forbiddenEntities": [], "riskFlags": [], "generationMode": "2x2",
        "anchorRefs": [], "participants": {"count": 0, "allowed": []}, "motion": "hold",
    }]


def _shot(shot_id: str, caption_ids: list[str], cue: str) -> dict:
    return {
        "id": shot_id, "source_beat_ids": ["B001"], "chapter": 1, "cue": cue,
        "caption_ids": caption_ids, "description": f"{shot_id}画面",
        "required_entities": ["老人"], "forbidden_entities": [], "risk_flags": [],
        "generation_mode": "2x2", "anchor_refs": [],
        "participants": {"count": 0, "allowed": []}, "motion": "hold",
        "visual_load": "ordinary", "intentional_hold": False, "hold_reason": "",
        "semantic_rationale": "字幕与画面共享当前场景", "nonverbal_window": None,
    }


def _plan(project: Path, shots: list[dict]) -> dict:
    manifest = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    return {
        "schema_version": "storyboard-audio-plan.v1", "release_id": "r1",
        "preliminary_manifest_sha256": _sha(manifest.read_bytes()),
        "beat_dispositions": [{"beat_id": "B001", "mode": "merge", "shot_ids": [s["id"] for s in shots]}],
        "shots": shots,
    }


class NarrativeFunctionChainTests(unittest.TestCase):
    def test_compiled_storyboard_carries_narrative_function(self) -> None:
        project, preliminary = _project()
        # One caption is explicitly a theory beat; the rest stay plot.
        meta = {
            "opening": {"bodyStart": 2.0},
            "captions": [_caption(i, narrative_function="theory" if i == 1 else "plot") for i in range(1, 9)],
        }
        shots = [
            # The single theory caption must be its own shot: L6's caption-grouping
            # gate forbids one image serving captions across a narrative_function
            # boundary, so the theory beat cannot be merged with the plot captions.
            _shot("as001", ["caption-0001"], "字幕1"),
            _shot("as002", [f"caption-{i:04d}" for i in range(2, 9)], "字幕2"),
        ]
        plan = validate_storyboard_audio_plan(
            project, _plan(project, shots), preliminary, audio_meta=meta, phase2_beats=_beats()
        )
        storyboard, _ = compile_final_storyboard(plan, meta, _beats())
        self.assertEqual(len(storyboard), 2)
        self.assertEqual(storyboard[0]["narrative_function"], "theory")
        self.assertEqual(storyboard[0]["narrativeFunction"], "theory")
        self.assertEqual(storyboard[1]["narrative_function"], "plot")

    def test_scene_missing_narrative_function_fails_closed(self) -> None:
        # Before this fix the function silently returned "plot" for a field-less
        # scene, erasing the theory/author_background/closing distinction.
        with self.assertRaises(DirectorStageError):
            _scene_narrative_function({"id": "x"})


if __name__ == "__main__":
    unittest.main()
