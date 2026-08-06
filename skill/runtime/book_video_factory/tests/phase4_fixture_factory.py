from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from phase2_fixture_factory import write_json
from test_phase3_visual_approval import decision_for
from test_phase3_visual_review import prepare, register_all, fake_contact_sheet
from book_video_factory.manifests import sha256_file
from book_video_factory.visual_stage.approval import approve_visual_stage
from book_video_factory.visual_stage.review import build_visual_review


def symlink_or_skip(test_case, link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            test_case.skipTest("Windows symlink privilege is unavailable")
        raise


def build_approved_phase3_project(base: Path) -> Path:
    project, tasks = prepare(base)
    register_all(base, project, tasks)
    with mock.patch(
        "book_video_factory.visual_stage.review._run_hbg_contact_sheet",
        side_effect=fake_contact_sheet,
    ):
        build_visual_review(project)
    decision_path = base / "visual-decision.json"
    write_json(decision_path, decision_for(project))
    with mock.patch(
        "book_video_factory.visual_stage.review._run_hbg_contact_sheet",
        side_effect=fake_contact_sheet,
    ):
        approve_visual_stage(
            project,
            release_id="r1",
            reviewer="phase4-fixture-reviewer",
            decision_path=decision_path,
            note="Phase 4 fixture visual approval",
        )
    return project


def audio_stage_input(project: Path) -> dict:
    approval = project / "03_images_生成图片/ANCHOR_APPROVAL.json"
    lexicon = project / "04_audio/PRONUNCIATION_LEXICON.json"
    return {
        "schema_version": "audio-stage-input.v1",
        "release_id": "r1",
        "provider": "edge-tts",
        "voice": "zh-CN-YunjianNeural",
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
            "script_md_sha256": sha256_file(project / "SCRIPT.md"),
            "project_spec_sha256": sha256_file(project / "PROJECT_SPEC.json"),
            "hbg_style_sha256": sha256_file(project / "HBG_STYLE.json"),
            "storyboard_base_sha256": sha256_file(project / "STORYBOARD_BASE.json"),
            "visual_approval_sha256": sha256_file(approval),
            "pronunciation_lexicon_sha256": sha256_file(lexicon) if lexicon.is_file() else "0" * 64,
        },
    }


def pronunciation_lexicon() -> dict:
    return {
        "schema_version": "pronunciation-lexicon.v1",
        "release_id": "r1",
        "entries": [
            {
                "entry_id": "author-hemingway",
                "display": "海明威",
                "spoken": "海明维",
                "scope": "reveal",
                "occurrence_policy": {"mode": "all"},
                "note": "作者名保持普通话读法",
            }
        ],
    }


def write_phase4_inputs(project: Path) -> tuple[Path, Path]:
    audio_dir = project / "04_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    lexicon_path = audio_dir / "PRONUNCIATION_LEXICON.json"
    write_json(lexicon_path, pronunciation_lexicon())
    payload = audio_stage_input(project)
    payload["bindings"]["pronunciation_lexicon_sha256"] = sha256_file(lexicon_path)
    input_path = audio_dir / "AUDIO_STAGE_INPUT.json"
    write_json(input_path, payload)
    return input_path, lexicon_path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic_chunks(text: str, *, maximum: int = 14) -> list[str]:
    import re
    from book_video_factory.audio_stage.media_probe import normalize_text

    units = re.findall(r"[^，。！？；：、,.!?;:]+[，。！？；：、,.!?;:]?", text)
    chunks: list[str] = []
    for unit in units:
        unit = unit.strip()
        if not normalize_text(unit):
            continue
        while len(unit) > maximum:
            piece = unit[:maximum]
            unit = unit[maximum:]
            if normalize_text(piece):
                chunks.append(piece)
            elif chunks:
                chunks[-1] += piece
        if unit:
            if normalize_text(unit):
                chunks.append(unit)
            elif chunks:
                chunks[-1] += unit
    if not chunks or any(not normalize_text(chunk) for chunk in chunks):
        raise AssertionError("fake HBG runner produced a punctuation-only VTT cue")
    return chunks


def _ffmpeg_silence(path: Path, duration: float, codec: str) -> None:
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{duration:.3f}",
        "-ar", "48000", "-ac", "1", "-c:a", codec,
    ]
    if codec == "aac": command.extend(["-b:a", "96k"])
    command.append(str(path))
    subprocess.run(command, check=True)


def _vtt_time(value: float) -> str:
    total_ms = int(round(value * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def fake_hbg_audio_runner(staging: Path) -> None:
    import re
    spec = json.loads((staging / "PROJECT_SPEC.json").read_text(encoding="utf-8"))
    narration = spec["narration"]
    script = (staging / "SCRIPT.md").read_text(encoding="utf-8")
    matches = list(re.finditer(r"^##\s+第[^\n｜]+章｜(.+)$", script, re.M))
    chapters=[]
    for index, match in enumerate(matches):
        body_start=match.end(); body_end=matches[index+1].start() if index+1<len(matches) else len(script)
        text="".join(line.strip() for line in script[body_start:body_end].splitlines() if line.strip())
        chapters.append((match.group(1).strip(), text))
    body_text="\n".join(text for _,text in chapters)
    chunks=_semantic_chunks(body_text,maximum=14)
    cue_duration=0.7
    duration=max(1.0,len(chunks)*cue_duration)
    audio=staging/"assets/audio"; opening=audio/"opening"; opening.mkdir(parents=True,exist_ok=True)
    for name,text in (("lead-natural",narration["leadText"]),("reveal-natural",narration["revealText"])):
        (opening/f"{name}.txt").write_text(text+"\n",encoding="utf-8")
        (opening/f"{name}.settings.json").write_text(json.dumps({"voice":"zh-CN-YunjianNeural","rate":"+0%","pitch":"+0Hz"},indent=2)+"\n")
        _ffmpeg_silence(opening/f"{name}.wav",0.5,"pcm_s16le")
        _ffmpeg_silence(opening/f"{name}.mp3",0.5,"libmp3lame")
        (opening/f"{name}.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:00.500\n"+text+"\n",encoding="utf-8")
    (audio/"narration-full.txt").write_text(body_text+"\n",encoding="utf-8")
    (audio/"narration-full.settings.json").write_text(json.dumps({"voice":"zh-CN-YunjianNeural","rate":"+0%","pitch":"+0Hz"},indent=2)+"\n")
    _ffmpeg_silence(audio/"narration-full.wav",duration,"pcm_s16le")
    _ffmpeg_silence(audio/"narration-full.mp3",duration,"libmp3lame")
    _ffmpeg_silence(audio/"narration.m4a",duration,"aac")
    vtt=["WEBVTT",""]
    captions=[]
    for index,chunk in enumerate(chunks):
        start=index*cue_duration; end=min(duration,(index+1)*cue_duration)
        vtt.extend([f"{_vtt_time(start)} --> {_vtt_time(end)}",chunk,""])
        captions.append({"id":f"caption-{index+1:04d}","start":round(start,3),"end":round(end,3),"duration":round(end-start,3),"text":chunk,"allowShort":len(chunk)<6})
    (audio/"narration-full.vtt").write_text("\n".join(vtt),encoding="utf-8")
    base=json.loads((staging/"STORYBOARD_BASE.json").read_text(encoding="utf-8"))
    scene_duration=duration/max(1,len(base)); storyboard=[]
    body_start=2.0
    for index,scene in enumerate(base):
        start=body_start+index*scene_duration; end=body_start+(index+1)*scene_duration
        storyboard.append({**scene,"cueTime":round(index*scene_duration,3),"narration":chapters[scene["chapter"]-1][1],"start":round(start,3),"duration":round(scene_duration,3),"end":round(end,3)})
    chapter_meta=[]
    for chapter_index,(title,text) in enumerate(chapters,1):
        scenes=[s for s in storyboard if s["chapter"]==chapter_index]
        start=scenes[0]["start"] if scenes else body_start
        next_scenes=[s for s in storyboard if s["chapter"]==chapter_index+1]
        end=next_scenes[0]["start"] if next_scenes else body_start+duration
        chapter_meta.append({"id":f"ch{chapter_index:02d}","chapter":chapter_index,"title":title,"text":text,"start":start,"end":end,"duration":round(end-start,3)})
    meta={"provider":"Edge TTS","voice":"zh-CN-YunjianNeural","rate":"+0%","pitch":"+0Hz","syncMode":"full-body-vtt-master","openingDuration":body_start,"opening":{"bodyStart":body_start},"body":{"text":body_text,"path":"assets/audio/narration.m4a","sourcePath":"assets/audio/narration-full.mp3","vtt":"assets/audio/narration-full.vtt","duration":duration},"chapters":chapter_meta,"captions":captions,"captionAudit":{"minChars":6,"maxChars":18,"minDuration":0.55,"defects":0},"narrationDuration":duration,"totalDuration":body_start+duration}
    (staging/"audio_meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (staging/"STORYBOARD.json").write_text(json.dumps(storyboard,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def build_storyboard_audio_plan(project: Path) -> dict:
    """Build a deterministic Agent-like plan for Phase 4 integration tests."""
    preliminary_path = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    preliminary = json.loads(preliminary_path.read_text(encoding="utf-8"))
    meta = json.loads((project / "audio_meta.json").read_text(encoding="utf-8"))
    beats = json.loads((project / "STORYBOARD_BASE.json").read_text(encoding="utf-8"))
    preliminary_storyboard = json.loads((project / "STORYBOARD.json").read_text(encoding="utf-8"))
    captions = meta["captions"]
    boundaries = [float(item["cueTime"]) for item in preliminary_storyboard]
    boundaries.append(float(meta["body"]["duration"]))
    assignments: list[list[dict]] = [[] for _ in beats]
    beat_index = 0
    for caption in captions:
        while beat_index + 1 < len(boundaries) - 1 and float(caption["start"]) >= boundaries[beat_index + 1]:
            beat_index += 1
        assignments[beat_index].append(caption)
    shots: list[dict] = []
    dispositions: list[dict] = []
    for beat, assigned in zip(beats, assignments):
        if not assigned:
            raise AssertionError(f"fixture beat has no captions: {beat['beatId']}")
        chunks = [assigned[index:index + 6] for index in range(0, len(assigned), 6)]
        shot_ids: list[str] = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            shot_id = f"audio-{beat['beatId'].lower()}-{chunk_index:02d}"
            shot_ids.append(shot_id)
            duration = float(chunk[-1]["end"]) - float(chunk[0]["start"])
            shots.append({
                "id": shot_id,
                "source_beat_ids": [beat["beatId"]],
                "chapter": int(beat["chapter"]),
                "cue": str(chunk[0]["text"]),
                "caption_ids": [item["id"] for item in chunk],
                "description": f"{beat['description']}，第{chunk_index}个真实语音镜头",
                "required_entities": list(beat["requiredEntities"]),
                "forbidden_entities": list(beat["forbiddenEntities"]),
                "risk_flags": list(beat["riskFlags"]),
                "generation_mode": "single" if beat["riskFlags"] else beat["generationMode"],
                "anchor_refs": list(beat["anchorRefs"]),
                "participants": dict(beat["participants"]),
                "motion": beat["motion"],
                "visual_load": "strong" if duration >= 8.0 else "ordinary",
                "intentional_hold": False,
                "hold_reason": "",
                "semantic_rationale": "字幕与源 Beat 共享人物、物件或场景实体",
                "nonverbal_window": None,
            })
        dispositions.append({
            "beat_id": beat["beatId"],
            "mode": "split" if len(shot_ids) > 1 else "retain",
            "shot_ids": shot_ids,
        })
    return {
        "schema_version": "storyboard-audio-plan.v1",
        "release_id": preliminary["release_id"],
        "preliminary_manifest_sha256": sha256_file(preliminary_path),
        "beat_dispositions": dispositions,
        "shots": shots,
    }
