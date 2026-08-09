from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from book_video_factory.audio_stage.storyboard_plan import (
        StoryboardPlanError,
        compile_final_storyboard,
        validate_storyboard_audio_plan,
    )
except ModuleNotFoundError:
    StoryboardPlanError = RuntimeError  # type: ignore


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
            {"id": f"caption-{i:04d}", "start": round((i - 1) * 0.8, 3), "end": round(i * 0.8, 3),
             "duration": 0.8, "text": f"字幕{i}", "text_sha256": _sha(f"字幕{i}".encode()),
             "restoration_status": "display-restored", "allowShort": True, "narrative_function": "plot"}
            for i in range(1, 17)
        ],
    }
    beats = [
        {"id": "s001", "beatId": "B001", "chapter": 1, "cue": "第一段", "description": "老人离港",
         "requiredEntities": ["老人", "港口"], "forbiddenEntities": ["现代游艇"], "riskFlags": [],
         "generationMode": "2x2", "anchorRefs": ["C001"], "participants": {"count": 1, "allowed": ["C001"]}, "motion": "zoom-in"},
        {"id": "s002", "beatId": "B002", "chapter": 1, "cue": "第二段", "description": "老人拉紧钓线",
         "requiredEntities": ["老人", "钓线"], "forbiddenEntities": ["港口"], "riskFlags": ["hands", "tool_use"],
         "generationMode": "single", "anchorRefs": ["C001"], "participants": {"count": 1, "allowed": ["C001"]}, "motion": "pan-left"},
        {"id": "s003", "beatId": "B003", "chapter": 2, "cue": "第三段", "description": "海面空镜",
         "requiredEntities": ["海面"], "forbiddenEntities": ["港口"], "riskFlags": [],
         "generationMode": "2x2", "anchorRefs": [], "participants": {"count": 0, "allowed": []}, "motion": "static"},
    ]
    return project, preliminary, audio_meta, beats



def _extend_meta(meta: dict, count: int, *, cue_duration: float = 0.8) -> dict:
    result = deepcopy(meta)
    result["captions"] = [
        {"id": f"caption-{i:04d}", "start": round((i - 1) * cue_duration, 3),
         "end": round(i * cue_duration, 3), "duration": cue_duration, "text": f"字幕{i}",
         "text_sha256": _sha(f"字幕{i}".encode()), "restoration_status": "display-restored",
         "allowShort": True, "narrative_function": "plot"}
        for i in range(1, count + 1)
    ]
    return result

def _shot(shot_id: str, beats: list[str], captions: list[str], chapter: int, cue: str, *,
          duration_kind: str = "ordinary", risks: list[str] | None = None, mode: str = "2x2",
          anchors: list[str] | None = None, participants: list[str] | None = None) -> dict:
    participants = participants or []
    return {
        "id": shot_id,
        "source_beat_ids": beats,
        "chapter": chapter,
        "cue": cue,
        "caption_ids": captions,
        "description": f"{shot_id}的明确画面",
        "required_entities": ["老人"] if participants else ["海面"],
        "forbidden_entities": ["现代游艇"],
        "risk_flags": risks or [],
        "generation_mode": mode,
        "anchor_refs": anchors or [],
        "participants": {"count": len(participants), "allowed": participants},
        "motion": "zoom-in",
        "visual_load": duration_kind,
        "intentional_hold": False,
        "hold_reason": "",
        "semantic_rationale": "字幕与画面共享老人或海面实体",
        "nonverbal_window": None,
    }


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
            _shot("as001", ["B001"], [f"caption-{i:04d}" for i in range(1, 6)], 1, "字幕1", anchors=["C001"], participants=["C001"]),
            _shot("as002", ["B002"], [f"caption-{i:04d}" for i in range(6, 11)], 1, "字幕6", risks=["hands", "tool_use"], mode="single", anchors=["C001"], participants=["C001"]),
            _shot("as003", ["B003"], [f"caption-{i:04d}" for i in range(11, 17)], 2, "字幕11"),
        ],
    }


class Phase4StoryboardPlanTests(unittest.TestCase):
    def test_valid_plan_compiles_timing_and_bidirectional_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp))
            plan = validate_storyboard_audio_plan(project, _valid(project), preliminary, audio_meta=meta, phase2_beats=beats)
            storyboard, bindings = compile_final_storyboard(plan, meta, beats)
            self.assertEqual([item["id"] for item in storyboard], ["as001", "as002", "as003"])
            self.assertAlmostEqual(storyboard[0]["start"], 2.0)
            self.assertAlmostEqual(storyboard[0]["duration"], 4.0)
            self.assertEqual(bindings["captions"]["caption-0001"]["shot_ids"], ["as001"])
            self.assertEqual(bindings["shots"]["as003"]["caption_ids"][-1], "caption-0016")

    def test_compiled_storyboard_covers_real_audio_lead_and_trailing_silence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp))
            meta["body"] = {"duration": 13.0}
            for caption in meta["captions"]:
                caption["start"] = round(caption["start"] + 0.1, 3)
                caption["end"] = round(caption["end"] + 0.1, 3)
            plan = validate_storyboard_audio_plan(
                project, _valid(project), preliminary, audio_meta=meta, phase2_beats=beats,
            )

            storyboard, bindings = compile_final_storyboard(plan, meta, beats)

            self.assertEqual(storyboard[0]["start"], 2.0)
            self.assertEqual(storyboard[-1]["end"], 15.0)
            self.assertEqual(bindings["shots"]["as001"]["start"], 2.0)
            self.assertEqual(bindings["shots"]["as003"]["end"], 15.0)

    def test_every_beat_has_exactly_one_disposition_and_reverse_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            for mutation in ("missing", "duplicate", "unknown", "wrong_reverse"):
                candidate = deepcopy(payload)
                if mutation == "missing": candidate["beat_dispositions"].pop()
                elif mutation == "duplicate": candidate["beat_dispositions"].append(deepcopy(candidate["beat_dispositions"][0]))
                elif mutation == "unknown": candidate["beat_dispositions"][0]["beat_id"] = "B999"
                else: candidate["shots"][0]["source_beat_ids"] = ["B002"]
                with self.subTest(mutation=mutation), self.assertRaises(StoryboardPlanError):
                    validate_storyboard_audio_plan(project, candidate, preliminary, audio_meta=meta, phase2_beats=beats)

    def test_split_and_merge_dispositions_are_structurally_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            payload["beat_dispositions"][0] = {"beat_id": "B001", "mode": "split", "shot_ids": ["as001a", "as001b"]}
            first = payload["shots"].pop(0)
            a = deepcopy(first); a["id"]="as001a"; a["caption_ids"]=["caption-0001","caption-0002"]; a["cue"]="字幕1"
            b = deepcopy(first); b["id"]="as001b"; b["caption_ids"]=["caption-0003","caption-0004","caption-0005"]; b["cue"]="字幕3"
            payload["shots"] = [a,b,*payload["shots"]]
            validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)
            payload["beat_dispositions"][0]["shot_ids"]=["as001a"]
            with self.assertRaisesRegex(StoryboardPlanError,"split"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_caption_coverage_and_order_are_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            cases=[]
            missing=deepcopy(payload); missing["shots"][2]["caption_ids"].pop(); cases.append(missing)
            duplicate=deepcopy(payload); duplicate["shots"][1]["caption_ids"].append("caption-0001"); cases.append(duplicate)
            unknown=deepcopy(payload); unknown["shots"][0]["caption_ids"][0]="caption-9999"; cases.append(unknown)
            reversed_plan=deepcopy(payload); reversed_plan["shots"][0],reversed_plan["shots"][1]=reversed_plan["shots"][1],reversed_plan["shots"][0]; cases.append(reversed_plan)
            for case in cases:
                with self.subTest(), self.assertRaises(StoryboardPlanError):
                    validate_storyboard_audio_plan(project,case,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_chapter_and_cue_order_cannot_move_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            payload["shots"][2]["chapter"] = 1
            with self.assertRaisesRegex(StoryboardPlanError,"chapter"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)
            payload=_valid(project); payload["shots"][0]["cue"]="字幕6"
            with self.assertRaisesRegex(StoryboardPlanError,"cue"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_entities_participants_and_anchors_must_be_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            intersect=deepcopy(payload); intersect["shots"][0]["forbidden_entities"]=["老人"]
            missing_anchor=deepcopy(payload); missing_anchor["shots"][0]["anchor_refs"]=[]
            unknown_participant=deepcopy(payload); unknown_participant["shots"][0]["participants"]={"count":1,"allowed":["C999"]}
            for case in (intersect,missing_anchor,unknown_participant):
                with self.assertRaises(StoryboardPlanError):
                    validate_storyboard_audio_plan(project,case,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_high_risk_requires_single_and_closed_risk_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            payload["shots"][1]["generation_mode"]="2x2"
            with self.assertRaisesRegex(StoryboardPlanError,"single"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)
            payload=_valid(project); payload["shots"][0]["risk_flags"]=["handz"]
            with self.assertRaisesRegex(StoryboardPlanError,"risk"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_density_rules_reject_undercoverage_and_fake_motion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            # 12.8 seconds in one shot is illegal without a concrete intentional hold.
            meta_long = _extend_meta(meta, 22)
            long=deepcopy(payload)
            long["shots"]=[
                _shot("as-all",["B001","B002"],[f"caption-{i:04d}" for i in range(1,18)],1,"字幕1",anchors=["C001"],participants=["C001"]),
                _shot("as003",["B003"],[f"caption-{i:04d}" for i in range(18,23)],2,"字幕18"),
            ]
            long["beat_dispositions"]=[
                {"beat_id":"B001","mode":"merge","shot_ids":["as-all"]},
                {"beat_id":"B002","mode":"merge","shot_ids":["as-all"]},
                {"beat_id":"B003","mode":"retain","shot_ids":["as003"]},
            ]
            with self.assertRaisesRegex(StoryboardPlanError,"12|hold"):
                validate_storyboard_audio_plan(project,long,preliminary,audio_meta=meta_long,phase2_beats=beats)
            long["shots"][0]["intentional_hold"]=True; long["shots"][0]["hold_reason"]="只用更强的zoom-in来拖住画面"
            with self.assertRaisesRegex(StoryboardPlanError,"zoom|pan|coverage"):
                validate_storyboard_audio_plan(project,long,preliminary,audio_meta=meta_long,phase2_beats=beats)

    def test_eight_to_twelve_seconds_requires_strong_load_and_over_sixteen_always_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            # 8.8 seconds for first shot while the project median remains 4.0 seconds.
            meta_strong = _extend_meta(meta, 21)
            payload["shots"][0]["caption_ids"]=[f"caption-{i:04d}" for i in range(1,12)]
            payload["shots"][1]["caption_ids"]=[f"caption-{i:04d}" for i in range(12,17)]; payload["shots"][1]["cue"]="字幕12"
            payload["shots"][2]["caption_ids"]=[f"caption-{i:04d}" for i in range(17,22)]; payload["shots"][2]["cue"]="字幕17"
            with self.assertRaisesRegex(StoryboardPlanError,"visual_load"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta_strong,phase2_beats=beats)
            payload["shots"][0]["visual_load"]="strong"
            validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta_strong,phase2_beats=beats)

            meta_over = _extend_meta(meta, 21)
            for index, caption in enumerate(meta_over["captions"]):
                if index < 10:
                    continue
                if index == 10:
                    caption["end"] = 17.0; caption["duration"] = round(17.0-caption["start"],3)
                else:
                    caption["start"] = round(17.0 + (index-11)*0.8,3)
                    caption["end"] = round(17.0 + (index-10)*0.8,3)
                    caption["duration"] = 0.8
            over = _valid(project)
            over["shots"][0]["caption_ids"]=[f"caption-{i:04d}" for i in range(1,12)]
            over["shots"][1]["caption_ids"]=[f"caption-{i:04d}" for i in range(12,17)]; over["shots"][1]["cue"]="字幕12"
            over["shots"][2]["caption_ids"]=[f"caption-{i:04d}" for i in range(17,22)]; over["shots"][2]["cue"]="字幕17"
            with self.assertRaisesRegex(StoryboardPlanError,"16"):
                validate_storyboard_audio_plan(project,over,preliminary,audio_meta=meta_over,phase2_beats=beats)

    def test_nonverbal_hold_requires_explicit_window_reason_and_no_caption_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            hold=_shot("hold01",["B003"],[],2,"第三段")
            hold["intentional_hold"]=True; hold["hold_reason"]="老人沉默后只剩海浪，保留一次完整呼吸"; hold["nonverbal_window"]={"start":12.0,"end":12.8}
            payload["beat_dispositions"][2]={"beat_id":"B003","mode":"split","shot_ids":["as003","hold01"]}
            payload["shots"].append(hold)
            with self.assertRaisesRegex(StoryboardPlanError,"overlap|order"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)
            payload["shots"][-1]["caption_ids"]=[]; payload["shots"][-1]["hold_reason"]=""
            payload["shots"][-1]["nonverbal_window"]={"start":12.8,"end":13.2}
            with self.assertRaisesRegex(StoryboardPlanError,"reason|timeline"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)

    def test_median_density_must_remain_in_target_band(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, preliminary, meta, beats = _project(Path(temp)); payload = _valid(project)
            # Split every caption into a shot: median 0.8 seconds.
            shots=[]; dispositions=[]
            groups={"B001":range(1,6),"B002":range(6,11),"B003":range(11,17)}
            beat_map={"B001":beats[0],"B002":beats[1],"B003":beats[2]}
            for beat_id,indices in groups.items():
                ids=[]
                for i in indices:
                    sid=f"tiny-{i:02d}"; ids.append(sid); source=beat_map[beat_id]
                    shots.append(_shot(sid,[beat_id],[f"caption-{i:04d}"],source["chapter"],f"字幕{i}",
                                      risks=source["riskFlags"],mode="single" if source["riskFlags"] else "2x2",
                                      anchors=source["anchorRefs"],participants=source["participants"]["allowed"]))
                dispositions.append({"beat_id":beat_id,"mode":"split","shot_ids":ids})
            payload["shots"]=shots; payload["beat_dispositions"]=dispositions
            with self.assertRaisesRegex(StoryboardPlanError,"median"):
                validate_storyboard_audio_plan(project,payload,preliminary,audio_meta=meta,phase2_beats=beats)


if __name__ == "__main__":
    unittest.main()
