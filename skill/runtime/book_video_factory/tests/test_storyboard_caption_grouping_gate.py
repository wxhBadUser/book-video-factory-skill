"""L6 adversarial test: caption grouping must block one image serving two meanings.

Drives the public entry ``validate_storyboard_audio_plan``. A shot that merges
captions across a hard boundary (different characters / location / time /
narrative register) must raise ``CaptionGroupingError``.

Mutation criterion (plan §0): on the committed pre-remediation code this test
is RED because ``audit_shot_caption_groups`` was wired into zero call sites and
the gate never fired.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.audio_stage.storyboard_plan import validate_storyboard_audio_plan
from book_video_factory.semantic_alignment.caption_grouping import CaptionGroupingError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _caption(index: int, *, location: str = "", characters: tuple[str, ...] = ()) -> dict:
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
        "location": location,
        "characters": list(characters),
        "time_of_day": "",
        "narrative_function": "plot",
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
        "id": shot_id,
        "source_beat_ids": ["B001"],
        "chapter": 1,
        "cue": cue,
        "caption_ids": caption_ids,
        "description": f"{shot_id}的明确画面",
        "required_entities": ["老人"],
        "forbidden_entities": [],
        "risk_flags": [],
        "generation_mode": "2x2",
        "anchor_refs": [],
        "participants": {"count": 0, "allowed": []},
        "motion": "hold",
        "visual_load": "ordinary",
        "intentional_hold": False,
        "hold_reason": "",
        "semantic_rationale": "字幕与画面共享老人实体",
        "nonverbal_window": None,
    }


def _plan(project: Path, shots: list[dict]) -> dict:
    manifest = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    return {
        "schema_version": "storyboard-audio-plan.v1",
        "release_id": "r1",
        "preliminary_manifest_sha256": _sha(manifest.read_bytes()),
        "beat_dispositions": [{"beat_id": "B001", "mode": "merge", "shot_ids": [s["id"] for s in shots]}],
        "shots": shots,
    }


class StoryboardCaptionGroupingGateTests(unittest.TestCase):
    def test_compatible_captions_split_across_shots_pass(self) -> None:
        project, preliminary = _project()
        meta = {"opening": {"bodyStart": 2.0}, "captions": [_caption(i) for i in range(1, 9)]}
        shots = [
            _shot("as001", [f"caption-{i:04d}" for i in range(1, 5)], "字幕1"),
            _shot("as002", [f"caption-{i:04d}" for i in range(5, 9)], "字幕5"),
        ]
        # No hard boundary is crossed; the plan is valid.
        validate_storyboard_audio_plan(
            project, _plan(project, shots), preliminary, audio_meta=meta, phase2_beats=_beats()
        )

    def test_incompatible_captions_in_one_shot_blocked(self) -> None:
        project, preliminary = _project()
        meta = {"opening": {"bodyStart": 2.0}, "captions": [_caption(i) for i in range(1, 9)]}
        # 凤霞出嫁 (home) and 有庆雪地跑 (snowfield) pressed into the same shot.
        meta["captions"][0]["location"] = "凤霞家"
        meta["captions"][0]["characters"] = ["凤霞"]
        meta["captions"][1]["location"] = "雪地"
        meta["captions"][1]["characters"] = ["有庆"]
        shots = [
            _shot("as001", [f"caption-{i:04d}" for i in range(1, 5)], "字幕1"),
            _shot("as002", [f"caption-{i:04d}" for i in range(5, 9)], "字幕5"),
        ]
        with self.assertRaises(CaptionGroupingError):
            validate_storyboard_audio_plan(
                project, _plan(project, shots), preliminary, audio_meta=meta, phase2_beats=_beats()
            )


if __name__ == "__main__":
    unittest.main()
