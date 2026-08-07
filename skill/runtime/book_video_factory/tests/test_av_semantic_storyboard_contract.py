"""Adversarial tests for the storyboard semantic contract (root cause B).

Root cause B: ``compile_final_storyboard`` accepted any shot whose
``required_entities`` had zero intersection with its source beats as long as
``semantic_rationale`` was a nonempty string. Template boilerplate such as
``字幕与画面共享当前场景`` therefore silently bypassed entity validation, which is
how huozhe-r1 ended up with 173 shots illustrated by 14 recycled descriptions.

These tests pin the contract:

* an empty entity intersection makes the rationale *load bearing*;
* a load-bearing rationale must be substantive and must name both the source
  side (what the narration says) and the image side (what will be drawn);
* boilerplate never counts as justification on its own;
* a genuine symbolic bridge is still allowed, so the gate is not a blanket ban.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from book_video_factory.audio_stage.storyboard_plan import (
    StoryboardPlanError,
    compile_final_storyboard,
    validate_storyboard_audio_plan,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project(base: Path) -> tuple[Path, dict, dict, list[dict]]:
    project = base / "project"
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
    audio_meta = {
        "opening": {"bodyStart": 2.0},
        "captions": [
            {
                "id": f"caption-{i:04d}",
                "start": round((i - 1) * 0.8, 3),
                "end": round(i * 0.8, 3),
                "duration": 0.8,
                "text": f"字幕{i}",
                "text_sha256": _sha(f"字幕{i}".encode()),
                "restoration_status": "display-restored",
                "allowShort": True,
            }
            for i in range(1, 17)
        ],
    }
    beats = [
        {
            "id": "s001", "beatId": "B001", "chapter": 1, "cue": "第一段",
            "description": "凤霞出嫁那天队伍走过村口",
            "requiredEntities": ["凤霞", "婚礼队伍"], "forbiddenEntities": ["现代游艇"],
            "riskFlags": [], "generationMode": "2x2", "anchorRefs": ["C001"],
            "participants": {"count": 1, "allowed": ["C001"]}, "motion": "zoom-in",
        },
        {
            "id": "s002", "beatId": "B002", "chapter": 1, "cue": "第二段",
            "description": "福贵在田里收豆子",
            "requiredEntities": ["福贵", "豆子"], "forbiddenEntities": ["婚礼队伍"],
            "riskFlags": ["hands", "tool_use"], "generationMode": "single",
            "anchorRefs": ["C001"], "participants": {"count": 1, "allowed": ["C001"]},
            "motion": "pan-left",
        },
        {
            "id": "s003", "beatId": "B003", "chapter": 2, "cue": "第三段",
            "description": "老牛在暮色里低头吃草",
            "requiredEntities": ["老牛"], "forbiddenEntities": ["婚礼队伍"],
            "riskFlags": [], "generationMode": "2x2", "anchorRefs": [],
            "participants": {"count": 0, "allowed": []}, "motion": "static",
        },
    ]
    return project, preliminary, audio_meta, beats


def _shot(
    shot_id: str,
    beats: list[str],
    captions: list[str],
    chapter: int,
    cue: str,
    *,
    required: list[str],
    rationale: str,
    risks: list[str] | None = None,
    mode: str = "2x2",
    anchors: list[str] | None = None,
    participants: list[str] | None = None,
) -> dict:
    participants = participants or []
    return {
        "id": shot_id,
        "source_beat_ids": beats,
        "chapter": chapter,
        "cue": cue,
        "caption_ids": captions,
        "description": f"{shot_id}的明确画面",
        "required_entities": required,
        "forbidden_entities": ["现代游艇"],
        "risk_flags": risks or [],
        "generation_mode": mode,
        "anchor_refs": anchors or [],
        "participants": {"count": len(participants), "allowed": participants},
        "motion": "zoom-in",
        "visual_load": "ordinary",
        "intentional_hold": False,
        "hold_reason": "",
        "semantic_rationale": rationale,
        "nonverbal_window": None,
    }


GROUNDED_RATIONALE_ONE = "旁白讲凤霞出嫁，画面用婚礼队伍的红布来承接这句台词"
GROUNDED_RATIONALE_TWO = "旁白讲福贵收豆子，画面用豆子在竹匾里的特写来承接这句台词"
GROUNDED_RATIONALE_THREE = "旁白讲老牛，画面用老牛低头吃草的侧影来承接这句台词"


def _valid(project: Path) -> dict:
    manifest = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    return {
        "schema_version": "storyboard-audio-plan.v1",
        "release_id": "r1",
        "preliminary_manifest_sha256": _sha(manifest.read_bytes()),
        "beat_dispositions": [
            {"beat_id": "B001", "mode": "retain", "shot_ids": ["as001"]},
            {"beat_id": "B002", "mode": "retain", "shot_ids": ["as002"]},
            {"beat_id": "B003", "mode": "retain", "shot_ids": ["as003"]},
        ],
        "shots": [
            _shot(
                "as001", ["B001"], [f"caption-{i:04d}" for i in range(1, 6)], 1, "字幕1",
                required=["凤霞", "婚礼队伍"], rationale=GROUNDED_RATIONALE_ONE,
                anchors=["C001"], participants=["C001"],
            ),
            _shot(
                "as002", ["B002"], [f"caption-{i:04d}" for i in range(6, 11)], 1, "字幕6",
                required=["福贵", "豆子"], rationale=GROUNDED_RATIONALE_TWO,
                risks=["hands", "tool_use"], mode="single",
                anchors=["C001"], participants=["C001"],
            ),
            _shot(
                "as003", ["B003"], [f"caption-{i:04d}" for i in range(11, 17)], 2, "字幕11",
                required=["老牛"], rationale=GROUNDED_RATIONALE_THREE,
            ),
        ],
    }


class StoryboardSemanticContractTests(unittest.TestCase):
    def _compile(self, mutate) -> tuple[list[dict], dict]:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp))
            payload = _valid(project)
            mutate(payload)
            plan = validate_storyboard_audio_plan(
                project, payload, preliminary, audio_meta=meta, phase2_beats=beats
            )
            return compile_final_storyboard(plan, meta, beats)

    def test_baseline_grounded_plan_still_compiles(self) -> None:
        """Positive control: the gate must not become a blanket ban."""
        storyboard, bindings = self._compile(lambda payload: None)
        self.assertEqual([shot["id"] for shot in storyboard], ["as001", "as002", "as003"])
        self.assertEqual(
            bindings["shots"]["as001"]["shared_required_entities"], ["凤霞", "婚礼队伍"]
        )

    def test_wrong_entities_with_nonempty_rationale_is_rejected(self) -> None:
        """Adversarial #1: required_entities disjoint from the source beat.

        The rationale is nonempty, so the pre-fix code accepted this shot.
        """

        def mutate(payload: dict) -> None:
            payload["shots"][0]["required_entities"] = ["棉花地", "拖拉机"]

        with self.assertRaises(StoryboardPlanError) as ctx:
            self._compile(mutate)
        self.assertIn("as001", str(ctx.exception))

    def test_template_rationale_cannot_stand_in_for_entity_overlap(self) -> None:
        """Adversarial #2: boilerplate is never load-bearing justification."""
        templates = [
            "字幕与画面共享当前场景",
            "字幕与画面共享当前场景：凤霞出嫁",
            "画面呼应旁白",
            "illustrate the exact current narration beat",
            "与旁白语义一致",
        ]
        for template in templates:
            def mutate(payload: dict, template: str = template) -> None:
                payload["shots"][0]["required_entities"] = ["棉花地"]
                payload["shots"][0]["semantic_rationale"] = template

            with self.subTest(template=template), self.assertRaises(StoryboardPlanError):
                self._compile(mutate)

    def test_fenghua_wedding_cannot_be_illustrated_by_children_in_snow(self) -> None:
        """Adversarial #3: the real huozhe-r1 mismatch, 凤霞出嫁 -> 雪地孩子."""

        def mutate(payload: dict) -> None:
            payload["shots"][0]["required_entities"] = ["雪地", "孩子"]
            payload["shots"][0]["semantic_rationale"] = "字幕与画面共享当前场景：情绪一致"

        with self.assertRaises(StoryboardPlanError):
            self._compile(mutate)

    def test_beans_cannot_be_illustrated_by_a_cotton_field(self) -> None:
        """Adversarial #4: the real huozhe-r1 mismatch, 豆子 -> 棉花地."""

        def mutate(payload: dict) -> None:
            payload["shots"][1]["required_entities"] = ["棉花地"]
            payload["shots"][1]["semantic_rationale"] = "画面呼应旁白的劳作氛围"

        with self.assertRaises(StoryboardPlanError):
            self._compile(mutate)

    def test_symbolic_bridge_is_allowed_when_it_names_both_sides(self) -> None:
        """A genuine symbolic surrogate must still pass.

        The rationale names the source-side referent (老牛, present in beat
        B003 requiredEntities) and the image-side surrogate (空木轭).
        """

        def mutate(payload: dict) -> None:
            payload["shots"][2]["required_entities"] = ["空木轭"]
            payload["shots"][2]["semantic_rationale"] = (
                "旁白说的是老牛，画面改用挂在墙上的空木轭作为替身，"
                "让观众看见牛已不在，避免直接画牛脸造成的重复"
            )

        storyboard, bindings = self._compile(mutate)
        self.assertEqual(storyboard[2]["requiredEntities"], ["空木轭"])
        self.assertEqual(bindings["shots"]["as003"]["shared_required_entities"], [])
        self.assertEqual(bindings["shots"]["as003"]["semantic_bridge_mode"], "symbolic")

    def test_symbolic_bridge_must_name_the_source_side(self) -> None:
        """Naming only the image side is not a bridge, it is an assertion."""

        def mutate(payload: dict) -> None:
            payload["shots"][2]["required_entities"] = ["空木轭"]
            payload["shots"][2]["semantic_rationale"] = (
                "画面使用挂在墙上的空木轭，构图安静，留白较多，色调偏冷"
            )

        with self.assertRaises(StoryboardPlanError):
            self._compile(mutate)

    def test_symbolic_bridge_must_name_the_image_side(self) -> None:
        """Restating the narration is not a bridge either."""

        def mutate(payload: dict) -> None:
            payload["shots"][2]["required_entities"] = ["空木轭"]
            payload["shots"][2]["semantic_rationale"] = (
                "旁白讲的是老牛在暮色里低头吃草，这一句是全片情绪的落点"
            )

        with self.assertRaises(StoryboardPlanError):
            self._compile(mutate)

    def test_bindings_record_the_bridge_mode_for_direct_overlap(self) -> None:
        storyboard, bindings = self._compile(lambda payload: None)
        for shot_id in ("as001", "as002", "as003"):
            self.assertEqual(bindings["shots"][shot_id]["semantic_bridge_mode"], "direct")
        self.assertEqual(
            bindings["captions"]["caption-0001"]["semantic_bridge_mode"], "direct"
        )

    def test_boilerplate_is_flagged_even_when_entities_overlap(self) -> None:
        """Overlap justifies the shot, but boilerplate must not be silent."""

        def mutate(payload: dict) -> None:
            payload["shots"][0]["semantic_rationale"] = "字幕与画面共享当前场景"

        storyboard, bindings = self._compile(mutate)
        self.assertTrue(bindings["shots"]["as001"]["rationale_is_boilerplate"])
        self.assertFalse(bindings["shots"]["as002"]["rationale_is_boilerplate"])

    def test_rationale_that_merely_repeats_the_caption_cue_is_not_justification(self) -> None:
        def mutate(payload: dict) -> None:
            payload["shots"][0]["required_entities"] = ["棉花地"]
            payload["shots"][0]["semantic_rationale"] = "字幕1"

        with self.assertRaises(StoryboardPlanError):
            self._compile(mutate)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
