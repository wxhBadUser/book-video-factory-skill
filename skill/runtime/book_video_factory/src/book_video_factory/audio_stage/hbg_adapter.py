from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from book_video_factory.hbg_bridge.runner import run_hbg_node


class AudioHbgAdapterError(RuntimeError):
    pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_audio_staging_project(
    project: Path,
    staging: Path,
    *,
    spoken_script: str,
    phase4_input: Mapping[str, Any],
    storyboard_base: Sequence[Mapping[str, Any]],
    reuse_audio_from: Path | None = None,
) -> None:
    root = project.expanduser().resolve()
    target = staging.expanduser().resolve()
    if target == root or root in target.parents:
        raise AudioHbgAdapterError("audio staging directory cannot be the source project or inside it")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for relative in ("PROJECT_SPEC.json", "HBG_STYLE.json", "CHARACTERS.md", "SCRIPT_SOURCE.md"):
        source = root / relative
        if not source.is_file():
            raise AudioHbgAdapterError(f"required HBG evidence is missing: {relative}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (target / "SCRIPT.md").write_text(spoken_script, encoding="utf-8")
    _write_json(target / "STORYBOARD_BASE.json", list(storyboard_base))

    spec = json.loads((target / "PROJECT_SPEC.json").read_text(encoding="utf-8"))
    narration = dict(spec.get("narration") or {})
    narration.update({
        "provider": "edge-tts",
        "voice": phase4_input["voice"],
        "bodyRate": phase4_input["body_rate"],
        "leadRate": phase4_input["lead_rate"],
        "revealRate": phase4_input["reveal_rate"],
        "pitch": phase4_input["pitch"],
        "leadText": phase4_input["lead_text"],
        "revealText": phase4_input["reveal_text"],
        "captionMinChars": phase4_input["caption_min_chars"],
        "captionMaxChars": phase4_input["caption_max_chars"],
        "captionMinDuration": phase4_input["caption_min_duration"],
        "leadStart": phase4_input["lead_start"],
        "flashGapAfterLead": phase4_input["flash_gap_after_lead"],
        "flashDuration": phase4_input["flash_duration"],
        "revealHold": phase4_input["reveal_hold"],
        "bodyGap": phase4_input["body_gap"],
    })
    spec["narration"] = narration
    _write_json(target / "PROJECT_SPEC.json", spec)

    # HBG only records the flash path during narration construction. Copy the
    # file when it exists so later HBG audits can run against a self-contained
    # staging project.
    flash = (spec.get("opening") or {}).get("flashMedia")
    if isinstance(flash, str) and flash:
        source_flash = root / flash
        if source_flash.is_file():
            destination = target / flash
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_flash, destination)

    if reuse_audio_from is not None:
        cache = reuse_audio_from.expanduser().resolve()
        source_audio = cache / "assets/audio"
        if source_audio.is_dir():
            shutil.copytree(source_audio, target / "assets/audio", dirs_exist_ok=True)


def run_hbg_narration(staging: Path):
    audio = staging.expanduser().resolve() / "assets/audio"
    existing_webvtt = {
        subtitle: subtitle.read_bytes()
        for subtitle in sorted(audio.rglob("*.vtt"))
        if subtitle.read_text(encoding="utf-8-sig").lstrip().startswith("WEBVTT")
    } if audio.is_dir() else {}
    normalize_hbg_subtitles_to_webvtt(staging)
    result = run_hbg_node("build_narration.mjs", staging, capability="phase4")
    normalize_hbg_subtitles_to_webvtt(staging)
    for subtitle, original in existing_webvtt.items():
        subtitle.write_bytes(original)
    return result


def normalize_hbg_subtitles_to_webvtt(staging: Path) -> None:
    """Normalize current Edge CLI SRT payloads without replacing HBG timing."""
    def preserve_hbg_timestamp_shape(path: Path) -> None:
        original = path.read_text(encoding="utf-8-sig")
        lines: list[str] = []
        for line in original.replace("\r", "").split("\n"):
            if " --> " in line:
                start, end = line.split(" --> ", 1)
                if start.count(":") == 1:
                    start = "00:" + start
                end_parts = end.split(" ", 1)
                if end_parts[0].count(":") == 1:
                    end_parts[0] = "00:" + end_parts[0]
                end = " ".join(end_parts)
                line = f"{start} --> {end}"
            lines.append(line)
        normalized = "\n".join(lines)
        if original.endswith(("\n", "\r")) and not normalized.endswith("\n"):
            normalized += "\n"
        if normalized != original:
            path.write_text(normalized, encoding="utf-8")

    audio = staging.expanduser().resolve() / "assets/audio"
    if not audio.is_dir():
        return
    for subtitle in sorted(audio.rglob("*.vtt")):
        if subtitle.read_text(encoding="utf-8-sig").lstrip().startswith("WEBVTT"):
            preserve_hbg_timestamp_shape(subtitle)
            continue
        converted = subtitle.with_name(f".{subtitle.name}.webvtt.tmp")
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "srt", "-i", str(subtitle), "-f", "webvtt", str(converted),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0 or not converted.is_file():
            converted.unlink(missing_ok=True)
            raise AudioHbgAdapterError(
                f"FFmpeg could not normalize HBG subtitle output: {subtitle.name}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        converted.replace(subtitle)
        preserve_hbg_timestamp_shape(subtitle)


def run_hbg_caption_audit(staging: Path):
    return run_hbg_node(
        "audit_caption_semantics.mjs",
        staging,
        (str(staging.resolve()),),
        capability="phase4",
    )


def run_hbg_density_audit(staging: Path):
    storyboard = staging / "STORYBOARD.json"
    return run_hbg_node(
        "audit_storyboard_density.mjs",
        staging,
        (str(storyboard.resolve()), "12", "16"),
        capability="phase4",
    )
