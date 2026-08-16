"""P0-3: the director consumes the approved VISUAL_TIMELINE beats as its scene skeleton.

When ``04_audio/VISUAL_TIMELINE.json`` (visual-timeline.v1) exists, every beat must
become exactly one director scene / image task, one-to-one. The pre-existing
span-based path must be unchanged when the timeline is absent.

Note on theory/symbolic spans: a `theory`/`author_background` shot needs an
approved Profile symbolic mapping (classifier fail-closed). Splitting such a span
into fragments can isolate the caption text away from the mapping's source_concept,
which is a REAL semantic constraint on the visual-timeline builder (Part 8), not a
director defect. This test therefore splits only plot/literal spans into beats and
keeps theory spans whole, so it verifies the beat->scene WIRING rather than
re-litigating the pre-existing proposition gate.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import phase1_fixture_factory
import phase2_fixture_factory
from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    write_phase4_inputs,
)
from book_video_factory.audio_stage.compiler import generate_audio_stage, finalize_audio_stage
from book_video_factory.director_stage.compiler import DirectorStageError, compile_director_stage
from book_video_factory.semantic_alignment.caption_contract import build_caption_visual_contract_from_project
from book_video_factory.semantic_alignment.caption_grouping import build_caption_grouping_from_project
from book_video_factory.semantic_alignment.scene_continuity import (
    build_scene_continuity_from_project,
    build_span_review_groups,
)


def _prepare(base: Path) -> Path:
    """Build a complete audio→contract→grouping→continuity project (mirrors the
    existing Phase-5 test fixture so the director gate is satisfiable)."""
    inputs = copy.deepcopy(build_phase1_inputs())
    with mock.patch.object(phase1_fixture_factory, "build_phase1_inputs", return_value=inputs), \
         mock.patch.object(phase2_fixture_factory, "build_phase1_inputs", return_value=inputs):
        project = build_approved_phase3_project(base)
    input_path, lexicon_path = write_phase4_inputs(project)
    generate_audio_stage(project, input_path, lexicon_path, runner=fake_hbg_audio_runner)
    plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
    write_json(plan_path, build_storyboard_audio_plan(project))
    finalize_audio_stage(project, plan_path, runner=fake_hbg_audio_runner)
    build_caption_visual_contract_from_project(project, release_id="r1")
    build_caption_grouping_from_project(project)
    build_scene_continuity_from_project(project)
    return project


def _build_visual_timeline(project: Path) -> dict:
    """Derive a VISUAL_TIMELINE from the project's real spans.

    Plot/literal spans are chunked into 2-caption beats (so a multi-caption span
    yields several beats — the beat->scene wiring is exercised). Theory /
    author_background / opening / closing / transition spans stay whole so the
    approved symbolic mapping or abstract-allowed register still matches.
    """
    continuity = json.loads((project / "04_audio/SCENE_CONTINUITY_SPANS.json").read_text(encoding="utf-8"))
    grouping = json.loads((project / "04_audio/CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8"))
    bindings = json.loads((project / "04_audio/CAPTION_BINDINGS.json").read_text(encoding="utf-8"))
    captions = bindings["captions"] if isinstance(bindings["captions"], dict) else {
        str(item["caption_id"]): item for item in bindings["captions"]
    }
    all_groups = build_span_review_groups(continuity=continuity, grouping=grouping)
    # only plot/literal spans are safe to split into beats (they have concrete
    # referents); theory/author_background/opening/closing/transition stay whole
    _SPLITTABLE = {"plot"}
    beats = []
    for span in continuity["spans"]:
        span_id = str(span["span_id"])
        payload = all_groups[span_id]
        span_captions = [str(item) for item in payload["caption_ids"]]
        nf = str(span.get("narrative_function", ""))
        chunk = 2 if nf in _SPLITTABLE else len(span_captions)
        for start in range(0, len(span_captions), chunk):
            ids = span_captions[start:start + chunk]
            beat_id = f"VB_{len(beats) + 1:03d}"
            first = captions[ids[0]]
            last = captions[ids[-1]]
            beats.append({
                "beat_id": beat_id,
                "start": round(float(first["start"]), 3),
                "end": round(float(last["end"]), 3),
                "duration": round(float(last["end"]) - float(first["start"]), 3),
                "caption_ids": ids,
                "caption_texts": [str(captions[cid]["text"]) for cid in ids],
                "visual_proposition": str(span.get("representative_visual_core", "") or "hold"),
                "location_id": str(payload.get("location", "")),
                "participants": list(payload.get("characters", [])),
                "motion": "hold",
                "intentional_hold_reason": "",
            })
    return {
        "schema_version": "visual-timeline.v1",
        "release_id": "r1",
        "source_continuity_sha256": "0" * 64,
        "source_grouping_sha256": "0" * 64,
        "beat_count": len(beats),
        "median_duration": 3.0,
        "p95_duration": 4.0,
        "max_duration": 5.0,
        "holds": 0,
        "beats": beats,
    }


class DirectorConsumesVisualTimelineTests(unittest.TestCase):
    def test_without_timeline_span_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _prepare(Path(temp))
            result = compile_director_stage(project)
            self.assertEqual(result.status, "created")
            continuity = json.loads(
                (project / "04_audio/SCENE_CONTINUITY_SPANS.json").read_text(encoding="utf-8")
            )
            out = json.loads(
                (project / "05_director/DIRECTOR_TIMELINE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(out["scene_count"], len(continuity["spans"]))

    def test_director_scene_skeleton_follows_visual_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _prepare(Path(temp))
            timeline = _build_visual_timeline(project)
            self.assertGreater(timeline["beat_count"], 0)
            write_json(project / "04_audio/VISUAL_TIMELINE.json", timeline)

            result = compile_director_stage(project)
            self.assertEqual(result.status, "created")
            out = json.loads(
                (project / "05_director/DIRECTOR_TIMELINE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(out["scene_count"], timeline["beat_count"])
            by_beat = {str(b["beat_id"]): b for b in timeline["beats"]}
            scene_ids = {str(scene["scene_id"]) for scene in out["scenes"]}
            for beat in timeline["beats"]:
                expected_scene_id = "visual-beat-" + str(beat["beat_id"]).lower()
                self.assertIn(expected_scene_id, scene_ids)
                scene = next(s for s in out["scenes"] if str(s["scene_id"]) == expected_scene_id)
                self.assertEqual(scene["caption_ids"], beat["caption_ids"])
            # one image task per beat
            tasks = [line for line in (project / "05_director/IMAGE_TASKS.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(tasks), timeline["beat_count"])

    def test_multi_span_beat_fails_closed(self) -> None:
        # A beat whose captions belong to >1 span cannot be bound to one production
        # group -> the director must reject it rather than emit a mis-scoped scene.
        with tempfile.TemporaryDirectory() as temp:
            project = _prepare(Path(temp))
            continuity = json.loads((project / "04_audio/SCENE_CONTINUITY_SPANS.json").read_text(encoding="utf-8"))
            grouping = json.loads((project / "04_audio/CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8"))
            bindings = json.loads((project / "04_audio/CAPTION_BINDINGS.json").read_text(encoding="utf-8"))
            captions = bindings["captions"] if isinstance(bindings["captions"], dict) else {
                str(item["caption_id"]): item for item in bindings["captions"]
            }
            all_groups = build_span_review_groups(continuity=continuity, grouping=grouping)
            spans = list(all_groups)
            self.assertGreater(len(spans), 1)
            first_span = spans[0]
            other_span = next(s for s in spans if s != first_span)
            other_caption = next(cid for cid in all_groups[other_span]["caption_ids"])
            first_caption = next(cid for cid in all_groups[first_span]["caption_ids"])
            mixed_beat = {
                "beat_id": "VB_999",
                "start": round(float(captions[first_caption]["start"]), 3),
                "end": round(float(captions[other_caption]["end"]), 3),
                "duration": 3.0,
                "caption_ids": [first_caption, other_caption],
                "caption_texts": [str(captions[first_caption]["text"]), str(captions[other_caption]["text"])],
                "visual_proposition": "hold",
                "location_id": "",
                "participants": [],
                "motion": "hold",
                "intentional_hold_reason": "",
            }
            timeline = {
                "schema_version": "visual-timeline.v1",
                "release_id": "r1",
                "source_continuity_sha256": "0" * 64,
                "source_grouping_sha256": "0" * 64,
                "beat_count": 1,
                "median_duration": 3.0,
                "p95_duration": 3.0,
                "max_duration": 3.0,
                "holds": 0,
                "beats": [mixed_beat],
            }
            write_json(project / "04_audio/VISUAL_TIMELINE.json", timeline)
            with self.assertRaises(DirectorStageError):
                compile_director_stage(project)


if __name__ == "__main__":
    unittest.main()
