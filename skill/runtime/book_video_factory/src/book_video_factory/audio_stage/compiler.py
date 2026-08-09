from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from book_video_factory.manifests import safe_project_output, sha256_file

from .captions import CaptionAlignmentError, restore_display_captions
from .contracts import (
    AudioStageContractError,
    validate_audio_stage_input,
    validate_pronunciation_lexicon,
    verify_phase4_prerequisites,
)
from .hbg_adapter import (
    AudioHbgAdapterError,
    prepare_audio_staging_project,
    run_hbg_caption_audit,
    run_hbg_density_audit,
    run_hbg_narration,
)
from .media_probe import MediaValidationError, VttCue, parse_vtt, probe_audio, validate_vtt
from .pronunciation import PronunciationError, SpokenCompilation, SpokenSegment, compile_spoken_script
from .provenance import tool_provenance
from ..semantic_alignment.caption_grouping import (
    CaptionSectionRegister,
    normalize_script_register,
)


class AudioStageError(RuntimeError):
    pass


class AudioStageConflict(AudioStageError):
    pass


@dataclass(frozen=True)
class AudioStageResult:
    status: str
    manifest_path: Path
    stage_manifest_path: Path
    next_stage_status: str


AudioRunner = Callable[[Path], None]
_CHAPTER_RE = re.compile(r"^##\s+第[^\n｜]+章｜(.+)$", re.M)
_PRELIMINARY_MANIFEST_KEYS = {
    "schema_version",
    "release_id",
    "input_digest",
    "display_text_sha256",
    "spoken_text_sha256",
    "output_hashes",
    "media_report",
    "tool_provenance",
    "external_edge_service_exercised",
    "stage_manifest_path",
    "stage_manifest_sha256",
    "next_stage_status",
}
_TOOL_PROVENANCE_KEYS = {
    "python",
    "node",
    "ffmpeg",
    "ffprobe",
    "edge_tts",
    "external_edge_service_exercised",
}


def _edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise AudioStageError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioStageError(f"{label} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise AudioStageError(f"{label} must be a JSON object")
    return payload


def _official_project_file(root: Path, supplied: Path, relative: str, label: str) -> Path:
    """Resolve one fixed Phase 4 input without accepting aliases or symlinks."""
    expected = root / relative
    try:
        safe_expected = safe_project_output(root, expected)
        safe_candidate = safe_project_output(root, supplied)
    except ValueError as error:
        raise AudioStageError(f"{label} must be a non-symlink project-local official file") from error
    if safe_candidate != safe_expected or not safe_expected.is_file():
        raise AudioStageError(f"{label} must be the official project-local file: {relative}")
    return safe_expected


def _safe_project_artifact(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise AudioStageConflict(f"{label} has an unsafe path")
    try:
        target = safe_project_output(root, Path(relative))
    except ValueError as error:
        raise AudioStageConflict(f"{label} has an unsafe or symlinked path: {relative}") from error
    if not target.is_file():
        raise AudioStageConflict(f"{label} is missing or not a regular file: {relative}")
    return target


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_script(script: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_CHAPTER_RE.finditer(script))
    if not matches:
        raise AudioStageError("SCRIPT.md contains no HBG chapters")
    prefix = script[:matches[0].start()]
    chapters: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        body = script[match.end():end]
        body_text = "".join(line.strip() for line in body.splitlines() if line.strip())
        if not body_text:
            raise AudioStageError(f"chapter {index + 1} has no narration text")
        chapters.append((match.group(1).strip(), body_text))
    return prefix, chapters


def _lexicon_for_chapter(lexicon: Mapping[str, Any], chapter: int) -> dict[str, Any]:
    entries=[]
    for entry in lexicon["entries"]:
        if entry["scope"] in {"body", f"chapter:{chapter}"}:
            item=copy.deepcopy(entry); item["scope"]="body"; entries.append(item)
    return {"schema_version":"pronunciation-lexicon.v1","release_id":lexicon["release_id"],"entries":entries}


def _combine(compilations: Sequence[SpokenCompilation]) -> tuple[SpokenCompilation, list[tuple[float, float]]]:
    display_parts=[]; spoken_parts=[]; segments=[]; content_spans=[]
    d_off=0; s_off=0
    for comp in compilations:
        if display_parts:
            display_parts.append("\n"); spoken_parts.append("\n")
            segments.append(SpokenSegment(d_off,d_off+1,s_off,s_off+1,None)); d_off+=1; s_off+=1
        content_start = d_off
        display_parts.append(comp.display_text); spoken_parts.append(comp.spoken_text)
        for seg in comp.segments:
            segments.append(SpokenSegment(seg.display_start+d_off,seg.display_end+d_off,seg.spoken_start+s_off,seg.spoken_end+s_off,seg.entry_id))
        d_off+=len(comp.display_text); s_off+=len(comp.spoken_text)
        content_spans.append((content_start, d_off))
    # Each chapter's register span runs from its content start to the next
    # chapter's content start (the inter-chapter separator is folded into the
    # preceding chapter's trailing caption by restore_display_captions, because
    # normalize_text drops punctuation and the trailing ".\n" lands on the last
    # content character's display span) and the final chapter runs to its
    # content end. This keeps every restored caption covered by exactly one
    # section register instead of being rejected for straddling a boundary.
    chapter_ranges=[]
    for index,(cstart,cend) in enumerate(content_spans):
        if index + 1 < len(content_spans):
            chapter_ranges.append((cstart, content_spans[index + 1][0]))
        else:
            chapter_ranges.append((cstart, cend))
    display="".join(display_parts); spoken="".join(spoken_parts)
    return SpokenCompilation(display,spoken,tuple(segments),_sha_bytes(display.encode()),_sha_bytes(spoken.encode()),"body"), chapter_ranges


def _compile_scripts(script: str, lexicon: Mapping[str, Any]) -> tuple[str, SpokenCompilation, list[str], list[tuple[float, float]]]:
    prefix, chapters=_parse_script(script)
    comps=[]; rendered=[prefix.rstrip()+"\n\n"]
    display_chapters=[]
    for index,(title,body) in enumerate(chapters,1):
        comp=compile_spoken_script(body,_lexicon_for_chapter(lexicon,index),scope="body")
        comps.append(comp); display_chapters.append(body)
        rendered.append(f"## 第{index}章｜{title}\n\n{comp.spoken_text}\n\n")
    compiled, chapter_ranges=_combine(comps)
    return "".join(rendered).rstrip()+"\n", compiled, display_chapters, chapter_ranges


def _load_script_package(root: Path) -> dict[str, Any]:
    """Locate the script package that carries each section's narrative_function."""

    candidates = [
        root / "SCRIPT_PACKAGE.json",
        root / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json",
        root / "02_script" / "SCRIPT_PACKAGE.json",
    ]
    for path in candidates:
        if path.is_file():
            return _load_object(path, "script package")
    raise AudioStageError(
        "SCRIPT_PACKAGE.json is required to tag caption registers but was not found in the project"
    )


def _extract_script_sections(package: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    """Find the per-section register source of truth across SCRIPT_PACKAGE schemas.

    Two layouts exist in the wild: the canonical top-level
    ``performance_version.sections`` and the older ``script.performance_version.sections``
    used by shipped book projects. Both are accepted; an unknown layout (or one
    without a usable section list) is rejected so caption tagging cannot fall
    back to a silent "plot".
    """

    top = package.get("performance_version")
    if isinstance(top, dict) and isinstance(top.get("sections"), list) and top["sections"]:
        return top["sections"]
    nested = (package.get("script") or {}).get("performance_version")
    if isinstance(nested, dict) and isinstance(nested.get("sections"), list) and nested["sections"]:
        return nested["sections"]
    return None


def _build_section_register(
    root: Path,
    chapter_ranges: list[tuple[float, float]],
    *,
    display_text: str | None = None,
) -> list[CaptionSectionRegister]:
    """Deterministically map each compiled chapter to its script-section register.

    The script package's ``performance_version.sections`` carry the real
    narrative_function (and any characters/location/time_of_day) per section,
    in the same order as the compiled chapters. This is the B06/B07 source of
    truth: production captions are tagged with their *real* register instead of
    being left empty and silently collapsed to "plot".
    """

    package = _load_script_package(root)
    sections = _extract_script_sections(package)
    if not sections:
        raise AudioStageError("SCRIPT_PACKAGE.json has no performance_version.sections to tag captions")
    if display_text is None and len(sections) != len(chapter_ranges):
        raise AudioStageError(
            f"SCRIPT_PACKAGE sections ({len(sections)}) do not match compiled chapters "
            f"({len(chapter_ranges)}); caption tagging cannot be deterministic"
        )
    if display_text is None:
        section_ranges = chapter_ranges
    else:
        compact_chars: list[str] = []
        compact_to_display: list[int] = []
        for index, char in enumerate(display_text):
            if not char.isspace():
                compact_chars.append(char)
                compact_to_display.append(index)
        compact = "".join(compact_chars)
        cursor = 0
        starts: list[int] = []
        for section in sections:
            needle = "".join(char for char in str(section.get("text") or "") if not char.isspace())
            if not needle:
                raise AudioStageError("SCRIPT_PACKAGE section text is empty; caption tagging cannot be deterministic")
            found = compact.find(needle, cursor)
            if found < 0:
                raise AudioStageError(
                    "SCRIPT_PACKAGE section text does not occur in the compiled display script in order"
                )
            starts.append(compact_to_display[found])
            cursor = found + len(needle)
        if starts[0] != 0 or cursor != len(compact):
            raise AudioStageError(
                "SCRIPT_PACKAGE sections do not exactly cover the compiled display script"
            )
        section_ranges = [
            (start, starts[index + 1] if index + 1 < len(starts) else len(display_text))
            for index, start in enumerate(starts)
        ]
    register: list[CaptionSectionRegister] = []
    for (start, end), section in zip(section_ranges, sections):
        register.append(CaptionSectionRegister(
            display_start=start,
            display_end=end,
            narrative_function=normalize_script_register(str(section.get("narrative_function") or "")),
            characters=tuple(str(item) for item in (section.get("characters") or ())),
            location=str(section.get("location") or ""),
            time_of_day=str(section.get("timeOfDay") or section.get("time_of_day") or ""),
        ))
    return register


def _compilation_json(comp: SpokenCompilation) -> dict[str, Any]:
    return {"schema_version":"spoken-display-map.v1","display_sha256":comp.display_sha256,"spoken_sha256":comp.spoken_sha256,"scope":comp.scope,"segments":[seg.__dict__ for seg in comp.segments]}


def _durations_match(left: float, right: float) -> bool:
    return abs(left - right) <= max(0.25, max(left, right) * 0.01)


def _ensure_opening_media(
    audio: Path,
    stem: str,
    *,
    expected_text: str,
    expected_voice: str,
    expected_rate: str,
    expected_pitch: str,
) -> dict[str, Any]:
    opening = audio / "opening"
    label = "lead" if stem == "lead-natural" else "reveal"
    text_path = opening / f"{stem}.txt"
    settings_path = opening / f"{stem}.settings.json"
    vtt_path = opening / f"{stem}.vtt"
    if not text_path.is_file() or text_path.stat().st_size <= 0:
        raise AudioStageError(f"opening {label} text is missing")
    if not settings_path.is_file() or settings_path.stat().st_size <= 0:
        raise AudioStageError(f"opening {label} settings are missing")
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioStageError(f"opening {label} settings are unreadable") from error
    expected_settings = {"voice": expected_voice, "rate": expected_rate, "pitch": expected_pitch}
    if not isinstance(settings, dict) or settings != expected_settings:
        raise AudioStageError(f"opening {label} settings are invalid")
    wav = probe_audio(opening / f"{stem}.wav")
    mp3 = probe_audio(opening / f"{stem}.mp3")
    if (wav.codec, wav.sample_rate, wav.channels) != ("pcm_s16le", 48000, 1):
        raise AudioStageError(f"opening {label} WAV must be mono PCM s16le at 48 kHz")
    if mp3.codec != "mp3" or mp3.channels != 1:
        raise AudioStageError(f"opening {label} MP3 must be mono MP3")
    if not _durations_match(wav.duration, mp3.duration):
        raise AudioStageError(f"opening {label} MP3/WAV duration mismatch")
    if text_path.read_text(encoding="utf-8") != expected_text + "\n":
        raise AudioStageError(f"opening {label} text differs from the compiled project input")
    cues = parse_vtt(vtt_path)
    vtt_report = validate_vtt(cues, duration=wav.duration, expected_text=expected_text)
    return {
        "wav": {"path": f"assets/audio/opening/{stem}.wav", "codec": wav.codec, "sample_rate": wav.sample_rate, "channels": wav.channels, "duration": wav.duration, "size": wav.size, "sha256": sha256_file(wav.path)},
        "mp3": {"path": f"assets/audio/opening/{stem}.mp3", "codec": mp3.codec, "sample_rate": mp3.sample_rate, "channels": mp3.channels, "duration": mp3.duration, "size": mp3.size, "sha256": sha256_file(mp3.path)},
        "vtt": vtt_report,
        "text_sha256": sha256_file(text_path),
        "settings_sha256": sha256_file(settings_path),
    }


def _ensure_media(
    staging: Path,
    comp: SpokenCompilation,
    input_data: Mapping[str, Any],
    *,
    lead_comp: SpokenCompilation,
    reveal_comp: SpokenCompilation,
    raw_metadata_path: Path | None = None,
    section_register: Sequence[CaptionSectionRegister] | None = None,
) -> tuple[dict[str, Any],list[dict[str,Any]],dict[str,Any]]:
    audio=staging/"assets/audio"
    body_text_path=audio/"narration-full.txt"
    body_settings_path=audio/"narration-full.settings.json"
    if not body_text_path.is_file() or body_text_path.read_text(encoding="utf-8") != comp.spoken_text + "\n":
        raise AudioStageError("body narration text differs from the compiled spoken script")
    try:
        body_settings=json.loads(body_settings_path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as error:
        raise AudioStageError("body narration settings are missing or unreadable") from error
    if body_settings != {"voice":input_data["voice"],"rate":input_data["body_rate"],"pitch":input_data["pitch"]}:
        raise AudioStageError("body narration settings differ from the approved Phase 4 input")
    wav=probe_audio(audio/"narration-full.wav"); m4a=probe_audio(audio/"narration.m4a"); mp3=probe_audio(audio/"narration-full.mp3")
    if (wav.codec,wav.sample_rate,wav.channels)!=("pcm_s16le",48000,1): raise AudioStageError("body WAV must be mono PCM s16le at 48 kHz")
    if m4a.codec!="aac" or m4a.sample_rate!=48000 or m4a.channels!=1: raise AudioStageError("body M4A must be mono AAC at 48 kHz")
    if mp3.codec!="mp3" or mp3.channels!=1: raise AudioStageError("body MP3 must be mono MP3")
    if not _durations_match(wav.duration,m4a.duration) or not _durations_match(wav.duration,mp3.duration):
        raise AudioStageError("body MP3/WAV/M4A duration mismatch")
    cues=parse_vtt(audio/"narration-full.vtt")
    vtt_report=validate_vtt(cues,duration=wav.duration,expected_text=comp.spoken_text)
    raw=_load_object(raw_metadata_path or staging/"audio_meta.json","raw HBG audio metadata")
    if raw.get("syncMode")!="full-body-vtt-master": raise AudioStageError("HBG syncMode is not full-body-vtt-master")
    if (raw.get("body") or {}).get("text")!=comp.spoken_text: raise AudioStageError("HBG body text differs from spoken compilation")
    raw_caps=raw.get("captions")
    if not isinstance(raw_caps,list) or not raw_caps: raise AudioStageError("HBG returned no captions")
    cap_cues=[]; allow=set()
    for i,item in enumerate(raw_caps):
        try: cap_cues.append(VttCue(float(item["start"]),float(item["end"]),str(item["text"])))
        except (KeyError,TypeError,ValueError) as error: raise AudioStageError("HBG caption record is invalid") from error
        if item.get("allowShort") is True: allow.add(i)
    restored=restore_display_captions(cap_cues,comp,min_chars=int(input_data["caption_min_chars"]),max_chars=int(input_data["caption_max_chars"]),min_duration=float(input_data["caption_min_duration"]),allow_short_cues=allow,section_register=section_register)
    def evidence(item, relative):
        return {
            "path": relative,
            "codec": item.codec,
            "sample_rate": item.sample_rate,
            "channels": item.channels,
            "duration": item.duration,
            "size": item.size,
            "sha256": sha256_file(item.path),
        }
    return raw,restored,{
        "wav":evidence(wav,"assets/audio/narration-full.wav"),
        "m4a":evidence(m4a,"assets/audio/narration.m4a"),
        "mp3":evidence(mp3,"assets/audio/narration-full.mp3"),
        "vtt":vtt_report,
        "opening": {
            "lead": _ensure_opening_media(
                audio,"lead-natural",expected_text=lead_comp.spoken_text,
                expected_voice=input_data["voice"],expected_rate=input_data["lead_rate"],expected_pitch=input_data["pitch"],
            ),
            "reveal": _ensure_opening_media(
                audio,"reveal-natural",expected_text=reveal_comp.spoken_text,
                expected_voice=input_data["voice"],expected_rate=input_data["reveal_rate"],expected_pitch=input_data["pitch"],
            ),
        },
    }


def _display_outputs(staging: Path, raw: dict[str,Any], captions:list[dict[str,Any]], comp:SpokenCompilation, display_chapters:list[str]) -> tuple[dict[str,Any],list[dict[str,Any]]]:
    meta=copy.deepcopy(raw)
    meta["captions"]=captions
    meta["body"]["text"]=comp.display_text
    meta["body"]["displayTextSha256"]=comp.display_sha256
    meta["body"]["spokenTextSha256"]=comp.spoken_sha256
    meta["body"]["captionTimingSource"]="edge_vtt"
    for item in meta.get("chapters",[]):
        chapter=int(item["chapter"]); item["text"]=display_chapters[chapter-1]
    storyboard=json.loads((staging/"STORYBOARD.json").read_text(encoding="utf-8"))
    if not isinstance(storyboard,list) or not storyboard: raise AudioStageError("HBG returned no storyboard")
    for scene in storyboard:
        chapter=int(scene["chapter"]); scene["narration"]=display_chapters[chapter-1]
    return meta,storyboard


def _gap_report(storyboard:list[dict[str,Any]], captions:list[dict[str,Any]]) -> dict[str,Any]:
    durations=[float(item["duration"]) for item in storyboard]
    return {"schema_version":"audio-storyboard-gaps.v1","scene_count":len(storyboard),"caption_count":len(captions),"median_scene_duration":sorted(durations)[len(durations)//2],"over_5_5_seconds":[item["id"] for item in storyboard if float(item["duration"])>5.5],"over_12_seconds":[item["id"] for item in storyboard if float(item["duration"])>12],"over_16_seconds":[item["id"] for item in storyboard if float(item["duration"])>16],"status":"awaiting_agent_storyboard_plan"}


def _collect_audio_payloads(staging:Path) -> dict[str,bytes]:
    payloads={}
    for path in (staging/"assets/audio").rglob("*"):
        if path.is_file() and path.name != ".gitkeep":
            payloads[path.relative_to(staging).as_posix()] = path.read_bytes()
    return payloads


def _snapshot(targets: Mapping[str, Path]) -> dict[str, bytes | None]:
    return {relative: (target.read_bytes() if target.is_file() else None) for relative, target in targets.items()}


def _restore(targets: Mapping[str, Path], originals: Mapping[str, bytes | None]) -> None:
    for relative, data in originals.items():
        target = targets[relative]
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _publish(root: Path, payloads: Mapping[str, bytes]) -> None:
    targets: dict[str, Path] = {}
    try:
        for relative in payloads:
            targets[relative] = safe_project_output(root, Path(relative))
    except ValueError as error:
        raise AudioStageConflict(f"refusing unsafe Phase 4 output path: {error}") from error
    originals = _snapshot(targets)
    temps: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for relative, data in payloads.items():
            target = targets[relative]
            target.parent.mkdir(parents=True, exist_ok=True)
            # Re-check after creating parents so a symlinked ancestor cannot be followed.
            if safe_project_output(root, Path(relative)) != target:
                raise AudioStageConflict(f"Phase 4 output path changed during publish: {relative}")
            temp = target.with_name(f".{target.name}.phase4-{os.getpid()}.tmp")
            temp.write_bytes(data)
            temps[relative] = temp
        for relative, temp in temps.items():
            target = safe_project_output(root, Path(relative))
            if target != targets[relative]:
                raise AudioStageConflict(f"Phase 4 output path changed during publish: {relative}")
            os.replace(temp, target)
            replaced.append(relative)
    except Exception:
        _restore(targets, {relative: originals[relative] for relative in replaced})
        raise
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)


def _verify_existing_preliminary(
    root: Path,
    manifest_path: Path,
    *,
    release_id: str,
    input_digest: str,
    display_sha256: str,
    spoken_sha256: str,
    expected_media_report: Mapping[str, Any],
    expected_tool_provenance: Mapping[str, Any],
    superseded: set[str] | None = None,
) -> Path:
    superseded = superseded or set()
    existing = _load_object(manifest_path, "existing audio preliminary manifest")
    if set(existing) != _PRELIMINARY_MANIFEST_KEYS:
        raise AudioStageConflict("existing preliminary manifest fields are invalid")
    expected_identity = {
        "schema_version": "audio-preliminary-manifest.v1",
        "release_id": release_id,
        "input_digest": input_digest,
        "display_text_sha256": display_sha256,
        "spoken_text_sha256": spoken_sha256,
        "next_stage_status": "awaiting_audio_storyboard_plan",
    }
    for key, expected in expected_identity.items():
        if existing.get(key) != expected:
            raise AudioStageConflict(f"existing preliminary manifest identity mismatch: {key}")
    provenance = existing.get("tool_provenance")
    external = existing.get("external_edge_service_exercised")
    if not isinstance(external, bool) or not isinstance(provenance, Mapping) or set(provenance) != _TOOL_PROVENANCE_KEYS:
        raise AudioStageConflict("existing preliminary tool provenance is invalid")
    if provenance.get("external_edge_service_exercised") is not external:
        raise AudioStageConflict("existing preliminary Edge-service provenance is inconsistent")
    if existing.get("media_report") != dict(expected_media_report):
        raise AudioStageConflict("existing preliminary media report differs from probed media")
    if provenance != dict(expected_tool_provenance):
        raise AudioStageConflict("existing preliminary tool provenance differs from the verified environment")

    output_hashes = existing.get("output_hashes")
    if not isinstance(output_hashes, Mapping) or not output_hashes:
        raise AudioStageConflict("existing preliminary output hash table is invalid")
    normalized_hashes: dict[str, str] = {}
    for relative, expected in output_hashes.items():
        if not isinstance(relative, str) or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            raise AudioStageConflict("existing preliminary output hash entry is invalid")
        target = _safe_project_artifact(root, relative, "preliminary output")
        if relative not in superseded and sha256_file(target) != expected:
            raise AudioStageConflict(f"existing preliminary output hash mismatch: {relative}")
        normalized_hashes[relative] = str(expected)

    stage_relative = existing.get("stage_manifest_path")
    stage_sha = existing.get("stage_manifest_sha256")
    if not isinstance(stage_relative, str) or normalized_hashes.get(stage_relative) != stage_sha:
        raise AudioStageConflict("existing preliminary stage binding is invalid")
    stage_path = _safe_project_artifact(root, stage_relative, "preliminary stage manifest")
    stage = _load_object(stage_path, "preliminary stage manifest")
    required_stage_keys = {"schema_version","manifest_id","project_id","stage","release_id","producer","status","inputs","outputs","checks"}
    if set(stage) != required_stage_keys:
        raise AudioStageConflict("preliminary stage manifest fields are invalid")
    if (
        stage.get("schema_version") != "1.0"
        or stage.get("stage") != "audio_preliminary"
        or stage.get("release_id") != release_id
        or stage.get("status") != "success"
        or stage.get("project_id") != root.name
        or stage.get("producer") != {"tool": "book-video-factory-audio-stage"}
        or stage.get("inputs") != {"digest": input_digest}
    ):
        raise AudioStageConflict("preliminary stage manifest identity is invalid")
    stage_outputs = stage.get("outputs")
    if not isinstance(stage_outputs, list):
        raise AudioStageConflict("preliminary stage output records are invalid")
    records: dict[str, tuple[int, str]] = {}
    for item in stage_outputs:
        if not isinstance(item, Mapping) or set(item) != {"path","bytes","sha256"}:
            raise AudioStageConflict("preliminary stage output record is invalid")
        relative = item.get("path")
        size = item.get("bytes")
        digest = item.get("sha256")
        if not isinstance(relative, str) or isinstance(size, bool) or not isinstance(size, int) or size <= 0 or not isinstance(digest, str):
            raise AudioStageConflict("preliminary stage output record values are invalid")
        if relative in records:
            raise AudioStageConflict("preliminary stage contains duplicate output records")
        records[relative] = (size, digest)
    expected_records = {path: digest for path, digest in normalized_hashes.items() if path != stage_relative}
    if set(records) != set(expected_records):
        raise AudioStageConflict("preliminary stage output set disagrees with manifest")
    for relative, digest in expected_records.items():
        target = _safe_project_artifact(root, relative, "preliminary stage output")
        record_size, record_digest = records[relative]
        if record_digest != digest:
            raise AudioStageConflict(f"preliminary stage output digest mismatch: {relative}")
        if relative not in superseded and record_size != target.stat().st_size:
            raise AudioStageConflict(f"preliminary stage output size mismatch: {relative}")
    checks = stage.get("checks")
    expected_checks = {
        ("real_vtt_master", "pass", "error"),
        ("display_caption_restoration", "pass", "error"),
    }
    if (
        not isinstance(checks, list)
        or len(checks) != len(expected_checks)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"id", "result", "severity"}
            for item in checks
        )
        or {
            (item.get("id"), item.get("result"), item.get("severity"))
            for item in checks
        } != expected_checks
    ):
        raise AudioStageConflict("preliminary stage checks are invalid")
    return stage_path


def generate_audio_stage(project:Path,input_path:Path,lexicon_path:Path,*,runner:AudioRunner|None=None)->AudioStageResult:
    root=project.expanduser().resolve()
    try:
        input_path=_official_project_file(root,input_path,"04_audio/AUDIO_STAGE_INPUT.json","audio stage input")
        lexicon_path=_official_project_file(root,lexicon_path,"04_audio/PRONUNCIATION_LEXICON.json","pronunciation lexicon")
        # L8 (BLOCKER-6): fail closed fast -- a project that opts into a VPP must
        # have its per-caption voice audition explicitly approved (and pinned to
        # the exact VPP bytes) before ANY narration render, including the
        # preliminary pass. A VPP edited after approval invalidates the prior
        # approval and blocks both preliminary and final narration.
        _enforce_voice_audition_gate(root)
        input_raw=_load_object(input_path,"audio stage input"); lex_raw=_load_object(lexicon_path,"pronunciation lexicon")
        input_data=validate_audio_stage_input(root,input_raw); lexicon=validate_pronunciation_lexicon(root,lex_raw)
        if input_data["release_id"]!=lexicon["release_id"]: raise AudioStageError("input and lexicon release mismatch")
        prior=verify_phase4_prerequisites(root,input_data["release_id"])
        original_script=(root/"SCRIPT.md").read_text(encoding="utf-8")
        spoken_script,comp,display_chapters,chapter_ranges=_compile_scripts(original_script,lexicon)
        section_register=_build_section_register(root, chapter_ranges, display_text=comp.display_text)
        lead_comp=compile_spoken_script(input_data["lead_text"],lexicon,scope="lead")
        reveal_comp=compile_spoken_script(input_data["reveal_text"],lexicon,scope="reveal")
        staging_input=dict(input_data); staging_input["lead_text"]=lead_comp.spoken_text; staging_input["reveal_text"]=reveal_comp.spoken_text
        storyboard_base=json.loads((root/"STORYBOARD_BASE.json").read_text(encoding="utf-8"))
        input_digest=_sha_bytes(_canonical({"input":input_data,"lexicon":lexicon,"prior":prior,"spoken":comp.spoken_sha256}))
        manifest_relative="04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
        existing_path=root/manifest_relative
        if existing_path.is_file():
            _raw_existing, _captions_existing, expected_media_report = _ensure_media(root, comp, input_data, lead_comp=lead_comp, reveal_comp=reveal_comp, raw_metadata_path=root/"04_audio/raw/audio_meta.hbg.json", section_register=section_register)
            expected_provenance = tool_provenance(external_edge_service_exercised=runner is None)
            stage_path=_verify_existing_preliminary(
                root,
                existing_path,
                release_id=input_data["release_id"],
                input_digest=input_digest,
                display_sha256=comp.display_sha256,
                spoken_sha256=comp.spoken_sha256,
                expected_media_report=expected_media_report,
                expected_tool_provenance=expected_provenance,
            )
            return AudioStageResult("unchanged",existing_path,stage_path,"awaiting_audio_storyboard_plan")
        with tempfile.TemporaryDirectory(prefix="book-video-phase4-") as temp:
            staging=Path(temp)/"project"
            prepare_audio_staging_project(root,staging,spoken_script=spoken_script,phase4_input=staging_input,storyboard_base=storyboard_base,reuse_audio_from=None)
            if runner is None:
                if not _edge_tts_available(): raise AudioStageError("edge-tts is missing; install it before Phase 4")
                run_hbg_narration(staging); run_hbg_caption_audit(staging); run_hbg_density_audit(staging)
            else: runner(staging)
            raw,captions,media_report=_ensure_media(staging,comp,input_data,lead_comp=lead_comp,reveal_comp=reveal_comp,section_register=section_register)
            display_meta,storyboard=_display_outputs(staging,raw,captions,comp,display_chapters)
            gaps=_gap_report(storyboard,captions)
            payloads=_collect_audio_payloads(staging)
            payloads.update({
                "04_audio/SCRIPT_SPOKEN.md":spoken_script.encode("utf-8"),
                "04_audio/SPOKEN_DISPLAY_MAP.json":_pretty(_compilation_json(comp)),
                "04_audio/raw/audio_meta.hbg.json":_pretty(raw),
                "04_audio/AUDIO_STORYBOARD_GAPS.json":_pretty(gaps),
                "audio_meta.json":_pretty(display_meta),
                "STORYBOARD.json":_pretty(storyboard),
            })
            output_hashes={r:_sha_bytes(data) for r,data in payloads.items()}
            stage_relative=f"manifests/stages/audio_preliminary/audio-preliminary-{input_digest[:16]}.json"
            stage={"schema_version":"1.0","manifest_id":f"audio-preliminary-{input_digest[:16]}","project_id":root.name,"stage":"audio_preliminary","release_id":input_data["release_id"],"producer":{"tool":"book-video-factory-audio-stage"},"status":"success","inputs":{"digest":input_digest},"outputs":[{"path":r,"bytes":len(payloads[r]),"sha256":output_hashes[r]} for r in sorted(payloads)],"checks":[{"id":"real_vtt_master","result":"pass","severity":"error"},{"id":"display_caption_restoration","result":"pass","severity":"error"}]}
            payloads[stage_relative]=_pretty(stage)
            output_hashes[stage_relative]=_sha_bytes(payloads[stage_relative])
            manifest={"schema_version":"audio-preliminary-manifest.v1","release_id":input_data["release_id"],"input_digest":input_digest,"display_text_sha256":comp.display_sha256,"spoken_text_sha256":comp.spoken_sha256,"output_hashes":output_hashes,"media_report":media_report,"tool_provenance":tool_provenance(external_edge_service_exercised=runner is None),"external_edge_service_exercised":runner is None,"stage_manifest_path":stage_relative,"stage_manifest_sha256":output_hashes[stage_relative],"next_stage_status":"awaiting_audio_storyboard_plan"}
            manifest_bytes=_pretty(manifest)
            payloads[manifest_relative]=manifest_bytes
            for r in payloads:
                if (root/r).exists() and not r.startswith("assets/audio/"):
                    raise AudioStageConflict(f"refusing to overwrite pre-existing Phase 4 output: {r}")
            _publish(root,payloads)
            return AudioStageResult("created",root/manifest_relative,root/stage_relative,"awaiting_audio_storyboard_plan")
    except AudioStageConflict:
        raise
    except (AudioStageContractError, PronunciationError, AudioHbgAdapterError, MediaValidationError,
            CaptionAlignmentError, OSError, ValueError, KeyError, json.JSONDecodeError,
            RuntimeError) as error:
        if isinstance(error, AudioStageError):
            raise
        raise AudioStageError(str(error)) from error


def _verify_preliminary_outputs(root: Path, preliminary: Mapping[str, Any], *, final_exists: bool) -> dict[str, str]:
    output_hashes = preliminary.get("output_hashes")
    if not isinstance(output_hashes, Mapping) or not output_hashes:
        raise AudioStageError("preliminary audio manifest has no output hash table")
    normalized: dict[str, str] = {}
    superseded = {"audio_meta.json", "STORYBOARD.json"} if final_exists else set()
    for relative, expected in output_hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise AudioStageError("preliminary output hash table is invalid")
        normalized[relative] = expected
        if relative in superseded:
            continue
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise AudioStageError(f"preliminary audio evidence is stale: {relative}")
    stage_path = preliminary.get("stage_manifest_path")
    stage_sha = preliminary.get("stage_manifest_sha256")
    if not isinstance(stage_path, str) or not isinstance(stage_sha, str):
        raise AudioStageError("preliminary stage manifest binding is missing")
    if normalized.get(stage_path) != stage_sha:
        raise AudioStageError("preliminary stage manifest hash disagrees with output table")
    return normalized


def _audio_hashes(output_hashes: Mapping[str, str]) -> dict[str, str]:
    result = {path: digest for path, digest in output_hashes.items() if path.startswith("assets/audio/")}
    if not result:
        raise AudioStageError("preliminary manifest contains no audio assets")
    return dict(sorted(result.items()))


def _hbg_storyboard_base(
    plan: Mapping[str, Any],
    display_meta: Mapping[str, Any],
    raw_meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    display_captions = display_meta.get("captions")
    raw_captions = raw_meta.get("captions")
    if not isinstance(display_captions, list) or not isinstance(raw_captions, list):
        raise AudioStageError("caption evidence is missing for HBG finalization")
    display_index = {item.get("id"): item for item in display_captions if isinstance(item, Mapping)}
    result: list[dict[str, Any]] = []
    for shot in plan["shots"]:
        caption_ids = shot["caption_ids"]
        if caption_ids:
            first = display_index.get(caption_ids[0])
            raw_indexes = first.get("rawCueIndexes") if isinstance(first, Mapping) else None
            if not isinstance(raw_indexes, list) or not raw_indexes or not all(isinstance(i, int) for i in raw_indexes):
                raise AudioStageError(f"shot {shot['id']} has no raw Edge cue provenance")
            raw_index = raw_indexes[0]
            if raw_index < 0 or raw_index >= len(raw_captions) or not isinstance(raw_captions[raw_index], Mapping):
                raise AudioStageError(f"shot {shot['id']} raw Edge cue provenance is invalid")
            cue = str(raw_captions[raw_index].get("text", ""))
        else:
            cue = shot["cue"]
        if not cue.strip():
            raise AudioStageError(f"shot {shot['id']} has no HBG cue")
        result.append({
            "id": shot["id"],
            "beatId": shot["source_beat_ids"][0],
            "sourceBeatIds": list(shot["source_beat_ids"]),
            "chapter": shot["chapter"],
            "cue": cue,
            "description": shot["description"],
            "captionIntent": shot["cue"],
            "requiredEntities": list(shot["required_entities"]),
            "forbiddenEntities": list(shot["forbidden_entities"]),
            "riskFlags": list(shot["risk_flags"]),
            "generationMode": shot["generation_mode"],
            "anchorRefs": list(shot["anchor_refs"]),
            "participants": dict(shot["participants"]),
            "highRisk": bool(shot["risk_flags"]),
            "asset": f"assets/generated/scenes/{shot['id']}.png",
            "motion": shot["motion"],
        })
    return result


def _timeline_audit(
    plan: Mapping[str, Any],
    storyboard: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Any],
    audio_hashes: Mapping[str, str],
) -> dict[str, Any]:
    durations = [float(item["duration"]) for item in storyboard]
    return {
        "schema_version": "audio-timeline-audit.v1",
        "status": "pass",
        "shot_count": len(storyboard),
        "caption_count": len(bindings["captions"]),
        "median_shot_duration": plan["density"]["median_duration"],
        "over_8_seconds": [item["id"] for item in storyboard if float(item["duration"]) >= 8.0],
        "over_12_seconds": [item["id"] for item in storyboard if float(item["duration"]) > 12.0],
        "over_16_seconds": [item["id"] for item in storyboard if float(item["duration"]) > 16.0],
        "minimum_duration": min(durations),
        "maximum_duration": max(durations),
        "unbound_captions": list(bindings["coverage"]["unbound_captions"]),
        "audio_hash_invariance": dict(audio_hashes),
        "checks": [
            {"id": "all_phase2_beats_disposed", "result": "pass"},
            {"id": "all_display_captions_bound", "result": "pass"},
            {"id": "real_audio_timing_only", "result": "pass"},
            {"id": "audio_hashes_unchanged", "result": "pass"},
            {"id": "density_contract", "result": "pass"},
        ],
    }


def finalize_audio_stage(
    project: Path,
    plan_path: Path,
    *,
    runner: AudioRunner | None = None,
) -> AudioStageResult:
    from .storyboard_plan import StoryboardPlanError, compile_final_storyboard, validate_storyboard_audio_plan

    root = project.expanduser().resolve()
    plan_path = _official_project_file(
        root,
        plan_path,
        "04_audio/STORYBOARD_AUDIO_PLAN.json",
        "storyboard plan",
    )
    final_manifest_path = root / "04_audio/AUDIO_STAGE_MANIFEST.json"
    try:
        preliminary_path = root / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
        preliminary = _load_object(preliminary_path, "audio preliminary manifest")
        final_exists = final_manifest_path.is_file()
        # L8 (BLOCKER-6): a project that opts into a Voice Performance Plan must
        # have its per-caption voice audition explicitly approved before any
        # narration is rendered. This is enforced before the heavy HBG work so
        # an unapproved audition fails closed and fast.
        _enforce_voice_audition_gate(root)
        plan_raw = _load_object(plan_path, "storyboard audio plan")
        input_path = _official_project_file(root, root / "04_audio/AUDIO_STAGE_INPUT.json", "04_audio/AUDIO_STAGE_INPUT.json", "audio stage input")
        lexicon_path = _official_project_file(root, root / "04_audio/PRONUNCIATION_LEXICON.json", "04_audio/PRONUNCIATION_LEXICON.json", "pronunciation lexicon")
        input_data = validate_audio_stage_input(root, _load_object(input_path, "audio stage input"))
        lexicon = validate_pronunciation_lexicon(root, _load_object(lexicon_path, "pronunciation lexicon"))
        if input_data["release_id"] != lexicon["release_id"] or input_data["release_id"] != preliminary.get("release_id"):
            raise AudioStageError("Phase 4 release IDs disagree")
        prior = verify_phase4_prerequisites(root, input_data["release_id"])
        original_script = (root / "SCRIPT.md").read_text(encoding="utf-8")
        spoken_script, compilation, display_chapters, chapter_ranges = _compile_scripts(original_script, lexicon)
        section_register = _build_section_register(
            root, chapter_ranges, display_text=compilation.display_text
        )
        lead_comp = compile_spoken_script(input_data["lead_text"], lexicon, scope="lead")
        reveal_comp = compile_spoken_script(input_data["reveal_text"], lexicon, scope="reveal")
        preliminary_input_digest = _sha_bytes(_canonical({
            "input": input_data,
            "lexicon": lexicon,
            "prior": prior,
            "spoken": compilation.spoken_sha256,
        }))
        _raw_verified, _captions_verified, expected_preliminary_media = _ensure_media(root, compilation, input_data, lead_comp=lead_comp, reveal_comp=reveal_comp, raw_metadata_path=root/"04_audio/raw/audio_meta.hbg.json", section_register=section_register)
        preliminary_external = preliminary.get("external_edge_service_exercised")
        if not isinstance(preliminary_external, bool):
            raise AudioStageError("preliminary Edge-service provenance is invalid")
        expected_preliminary_provenance = tool_provenance(
            external_edge_service_exercised=preliminary_external
        )
        _verify_existing_preliminary(
            root,
            preliminary_path,
            release_id=input_data["release_id"],
            input_digest=preliminary_input_digest,
            display_sha256=compilation.display_sha256,
            spoken_sha256=compilation.spoken_sha256,
            expected_media_report=expected_preliminary_media,
            expected_tool_provenance=expected_preliminary_provenance,
            superseded={"audio_meta.json", "STORYBOARD.json"} if final_exists else set(),
        )
        preliminary_hashes = _verify_preliminary_outputs(root, preliminary, final_exists=final_exists)
        preliminary_audio_hashes = _audio_hashes(preliminary_hashes)
        display_meta = _load_object(root / "audio_meta.json", "display-safe audio metadata")
        phase2_beats_raw = json.loads((root / "STORYBOARD_BASE.json").read_text(encoding="utf-8"))
        if not isinstance(phase2_beats_raw, list):
            raise AudioStageError("Phase 2 storyboard base must be an array")
        normalized_plan = validate_storyboard_audio_plan(
            root, plan_raw, preliminary, audio_meta=display_meta, phase2_beats=phase2_beats_raw
        )
        staging_input = dict(input_data)
        staging_input["lead_text"] = lead_comp.spoken_text
        staging_input["reveal_text"] = reveal_comp.spoken_text
        raw_preliminary = _load_object(root / "04_audio/raw/audio_meta.hbg.json", "raw HBG audio metadata")
        final_hbg_base = _hbg_storyboard_base(normalized_plan, display_meta, raw_preliminary)
        plan_digest = _sha_bytes(_canonical(plan_raw))
        input_digest = _sha_bytes(_canonical({
            "preliminary_manifest_sha256": sha256_file(preliminary_path),
            "plan_sha256": sha256_file(plan_path),
            "plan_digest": plan_digest,
            "prior": prior,
            "input": input_data,
            "lexicon": lexicon,
        }))
        with tempfile.TemporaryDirectory(prefix="book-video-phase4-final-") as temp:
            staging = Path(temp) / "project"
            prepare_audio_staging_project(
                root,
                staging,
                spoken_script=spoken_script,
                phase4_input=staging_input,
                storyboard_base=final_hbg_base,
                reuse_audio_from=root,
            )
            if runner is None:
                if not _edge_tts_available():
                    raise AudioStageError("edge-tts is missing; install it before Phase 4 finalization")
                run_hbg_narration(staging)
                run_hbg_caption_audit(staging)
            else:
                runner(staging)
            raw_final, restored_captions, media_report = _ensure_media(
                staging,
                compilation,
                input_data,
                lead_comp=lead_comp,
                reveal_comp=reveal_comp,
                section_register=section_register,
            )
            fresh_meta, _hbg_storyboard = _display_outputs(
                staging, raw_final, restored_captions, compilation, display_chapters
            )
            preliminary_caption_fingerprint = _sha_bytes(_canonical(display_meta.get("captions")))
            final_caption_fingerprint = _sha_bytes(_canonical(fresh_meta.get("captions")))
            if preliminary_caption_fingerprint != final_caption_fingerprint:
                raise AudioStageError("final HBG rerun changed the display caption timeline")
            staged_audio = _collect_audio_payloads(staging)
            staged_audio_hashes = {
                path: _sha_bytes(data) for path, data in staged_audio.items() if path.startswith("assets/audio/")
            }
            if dict(sorted(staged_audio_hashes.items())) != preliminary_audio_hashes:
                raise AudioStageError("final HBG rerun caused audio hash drift")
            final_storyboard, bindings = compile_final_storyboard(
                normalized_plan, fresh_meta, phase2_beats_raw
            )
            (staging / "STORYBOARD.json").write_bytes(_pretty(final_storyboard))
            if runner is None:
                run_hbg_density_audit(staging)
            final_meta = copy.deepcopy(fresh_meta)
            final_meta["storyboardMode"] = "audio-driven-final"
            final_meta["storyboardPlanSha256"] = sha256_file(plan_path)
            final_meta["captionBindingsPath"] = "04_audio/CAPTION_BINDINGS.json"
            audit = _timeline_audit(normalized_plan, final_storyboard, bindings, preliminary_audio_hashes)
            final_payloads: dict[str, bytes] = {
                "04_audio/STORYBOARD_BASE.audio-final.json": _pretty(final_hbg_base),
                "04_audio/CAPTION_BINDINGS.json": _pretty(bindings),
                "04_audio/AUDIO_TIMELINE_AUDIT.json": _pretty(audit),
                "audio_meta.json": _pretty(final_meta),
                "STORYBOARD.json": _pretty(final_storyboard),
            }
            # Build the Caption Visual Contract (the authoritative per-caption
            # source of truth the Director and Render stages consume) as a
            # Phase-4 artifact when the locked inputs exist. Fail-closed: an
            # unbindable caption rejects the whole audio stage rather than
            # shipping a frame with no semantic contract. Only added on a fresh
            # finalize so already-finalized manifests are not retro-modified.
            contract_rel = "04_audio/CAPTION_VISUAL_CONTRACT.json"
            if (
                (root / "02_story_script_故事脚本/SCRIPT_PACKAGE.json").is_file()
                and (root / "STORYBOARD_BASE.json").is_file()
                and (root / "04_audio/CAPTION_BINDINGS.json").is_file()
                and (root / "03_images_生成图片/BOOK_VISUAL_PROFILE.json").is_file()
            ):
                from book_video_factory.semantic_alignment.caption_contract import (
                    build_caption_visual_contract_from_project,
                )
                build_caption_visual_contract_from_project(root, release_id=input_data["release_id"])
                final_payloads[contract_rel] = (root / contract_rel).read_bytes()
            final_output_hashes = {path: _sha_bytes(data) for path, data in final_payloads.items()}
            stage_relative = f"manifests/stages/audio_final/audio-final-{input_digest[:16]}.json"
            stage = {
                "schema_version": "1.0",
                "manifest_id": f"audio-final-{input_digest[:16]}",
                "project_id": root.name,
                "stage": "audio_final",
                "release_id": input_data["release_id"],
                "producer": {"tool": "book-video-factory-audio-stage"},
                "status": "success",
                "inputs": {
                    "digest": input_digest,
                    "preliminary_manifest_sha256": sha256_file(preliminary_path),
                    "storyboard_plan_sha256": sha256_file(plan_path),
                },
                "outputs": [
                    {"path": path, "bytes": len(final_payloads[path]), "sha256": final_output_hashes[path]}
                    for path in sorted(final_payloads)
                ],
                "checks": audit["checks"],
            }
            final_payloads[stage_relative] = _pretty(stage)
            final_output_hashes[stage_relative] = _sha_bytes(final_payloads[stage_relative])
            manifest = {
                "schema_version": "audio-stage-manifest.v1",
                "release_id": input_data["release_id"],
                "input_digest": input_digest,
                "preliminary_manifest_path": "04_audio/AUDIO_PRELIMINARY_MANIFEST.json",
                "preliminary_manifest_sha256": sha256_file(preliminary_path),
                "storyboard_plan_path": plan_path.resolve().relative_to(root).as_posix()
                    if root in plan_path.resolve().parents else str(plan_path.resolve()),
                "storyboard_plan_sha256": sha256_file(plan_path),
                "preliminary_audio_hashes": preliminary_audio_hashes,
                "final_output_hashes": final_output_hashes,
                "caption_timeline_sha256": final_caption_fingerprint,
                "media_report": media_report,
                "tool_provenance": tool_provenance(external_edge_service_exercised=runner is None),
                "external_edge_service_exercised": runner is None,
                "stage_manifest_path": stage_relative,
                "stage_manifest_sha256": final_output_hashes[stage_relative],
                "next_stage_status": "ready_for_image_task_planning",
            }
            manifest_bytes = _pretty(manifest)
            if final_exists:
                existing = _load_object(final_manifest_path, "existing final audio manifest")
                if existing.get("input_digest") != input_digest:
                    raise AudioStageConflict("a different final audio timeline already exists")
                if existing.get("final_output_hashes") != final_output_hashes:
                    raise AudioStageConflict("existing final audio manifest differs from deterministic outputs")
                for relative, expected in final_output_hashes.items():
                    if not (root / relative).is_file() or sha256_file(root / relative) != expected:
                        raise AudioStageConflict(f"existing final audio output hash mismatch: {relative}")
                if sha256_file(final_manifest_path) != _sha_bytes(manifest_bytes):
                    raise AudioStageConflict("existing final audio manifest was modified")
                return AudioStageResult(
                    "unchanged", final_manifest_path, root / stage_relative, "ready_for_image_task_planning"
                )
            final_payloads["04_audio/AUDIO_STAGE_MANIFEST.json"] = manifest_bytes
            for relative in final_payloads:
                if relative in {"audio_meta.json", "STORYBOARD.json", "04_audio/CAPTION_VISUAL_CONTRACT.json"}:
                    continue
                if (root / relative).exists():
                    raise AudioStageConflict(f"refusing to overwrite pre-existing final Phase 4 output: {relative}")
            _publish(root, final_payloads)
            return AudioStageResult(
                "created", final_manifest_path, root / stage_relative, "ready_for_image_task_planning"
            )
    except AudioStageConflict:
        raise
    except (AudioStageContractError, PronunciationError, AudioHbgAdapterError, MediaValidationError,
            CaptionAlignmentError, StoryboardPlanError, OSError, ValueError, KeyError,
            json.JSONDecodeError, RuntimeError) as error:
        if isinstance(error, AudioStageError):
            raise
        raise AudioStageError(str(error)) from error


# ---------------------------------------------------------------------------
# Voice Performance Plan integration (Part 8, design §9.3).
#
# These are additive helpers: the main compile path is unchanged, but when a
# project opts into a Voice Performance Plan the planner SSML is read here and
# full TTS is gated behind an explicit audition approval. The pipeline fails
# closed -- an unapproved audition blocks the whole narration render.
# ---------------------------------------------------------------------------

def load_voice_performance_ssml(plan_path: Path) -> dict[str, str]:
    """Return ``{caption_id: ssml_override}`` for a validated VPP.

    Captions without an ``ssml_override`` are omitted, so callers can fall back
    to default Edge-TTS rendering for those.
    """

    from .contracts import validate_voice_performance_plan
    from .voice_performance import build_caption_ssml

    document = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    plan = validate_voice_performance_plan(document)
    ssml: dict[str, str] = {}
    for caption_id, entry in plan["captions"].items():
        override = entry.get("ssml_override")
        if override:
            ssml[caption_id] = override
        else:
            ssml[caption_id] = build_caption_ssml(
                caption_id,
                rate=entry["rate"],
                pitch=entry["pitch"],
                pause_ms_before=entry["pause_ms_before"],
                pause_ms_after=entry["pause_ms_after"],
                emphasis_words=entry["emphasis_words"],
            )
    return ssml


def require_voice_audition_approved(approval_path: Path) -> dict[str, Any]:
    """Fail closed unless the voice audition was explicitly approved."""

    from .contracts import validate_voice_audition_approval

    document = json.loads(Path(approval_path).read_text(encoding="utf-8"))
    return validate_voice_audition_approval(document)


# Relative paths for the opt-in Voice Performance Plan and its approval. A VPP
# opts the project into per-caption voice performance; without an explicit
# approval the whole narration render is blocked.
_VOICE_PERFORMANCE_PLAN_RELATIVE = "04_audio/VOICE_PERFORMANCE_PLAN.json"
_VOICE_AUDITION_APPROVAL_RELATIVE = "04_audio/VOICE_AUDITION_APPROVAL.json"


def _voice_performance_plan_sha256(root: Path) -> str:
    """SHA-256 of the project's ``VOICE_PERFORMANCE_PLAN.json``.

    The audition approval is pinned to this digest, so a VPP edited after
    approval (even a one-character change) invalidates the approval and blocks
    narration until a fresh audition is recorded against the new plan.
    """

    path = root / _VOICE_PERFORMANCE_PLAN_RELATIVE
    if not path.is_file():
        raise AudioStageError(f"Voice Performance Plan is missing: {path}")
    return sha256_file(path)


def _enforce_voice_audition_gate(root: Path) -> dict[str, Any] | None:
    """Enforce the voice-audition gate for an audio stage.

    Returns the approval record when a Voice Performance Plan is present and
    approved against the *exact current* VPP bytes. Returns ``None`` when the
    project did not opt into a VPP, in which case no audition is required.
    Raises ``AudioStageError`` when a VPP exists but no explicit audition
    approval is present, or the approval is pinned to a different VPP digest --
    narration render (preliminary and final) is blocked until the audition is
    re-approved against the current plan.
    """

    vpp_path = root / _VOICE_PERFORMANCE_PLAN_RELATIVE
    if not vpp_path.is_file():
        return None
    current_sha = sha256_file(vpp_path)
    approval_path = root / _VOICE_AUDITION_APPROVAL_RELATIVE
    if not approval_path.is_file():
        raise AudioStageError(
            "a Voice Performance Plan opts this project into per-caption voice "
            "performance, but no voice audition approval exists; narration render "
            "is blocked until the audition is explicitly approved"
        )
    approval = require_voice_audition_approved(approval_path)
    approved_sha = approval.get("voice_performance_plan_sha256")
    if approved_sha != current_sha:
        raise AudioStageError(
            "the voice audition approval is pinned to a different VOICE_PERFORMANCE_PLAN.json "
            f"(approved={approved_sha}, current={current_sha}); narration render is blocked "
            "until the audition is re-approved against the current plan"
        )
    return approval
