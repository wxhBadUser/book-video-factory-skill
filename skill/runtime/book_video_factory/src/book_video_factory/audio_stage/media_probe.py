from __future__ import annotations

import json
import math
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


class MediaValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioProbe:
    path: Path
    codec: str
    sample_rate: int
    channels: int
    duration: float
    size: int


@dataclass(frozen=True)
class VttCue:
    start: float
    end: float
    text: str


_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})$")
_NORMALIZE_RE = re.compile(r"[\s“”‘’\"'《》，。！？；：、,.!?;:…—-]")


def normalize_text(value: str) -> str:
    return _NORMALIZE_RE.sub("", unicodedata.normalize("NFKC", str(value))).lower()


def probe_audio(path: Path) -> AudioProbe:
    target = path.expanduser().resolve()
    if not target.is_file() or target.stat().st_size <= 0:
        raise MediaValidationError(f"audio file is missing or empty: {target}")
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels:format=duration,size",
            "-of", "json", str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise MediaValidationError(f"ffprobe failed for {target}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        fmt = payload["format"]
        codec = str(stream["codec_name"])
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        duration = float(fmt["duration"])
        size = int(fmt.get("size", target.stat().st_size))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaValidationError(f"ffprobe output is invalid for {target}") from error
    if not codec or sample_rate <= 0 or channels <= 0 or not math.isfinite(duration) or duration <= 0 or size <= 0:
        raise MediaValidationError(f"audio metadata is invalid for {target}")
    return AudioProbe(target, codec, sample_rate, channels, duration, size)


def _timestamp(value: str) -> float:
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        raise MediaValidationError(f"invalid VTT timestamp: {value}")
    raw_hours, raw_minutes, raw_seconds, raw_millis = match.groups()
    hours = int(raw_hours or 0)
    minutes, seconds, millis = map(int, (raw_minutes, raw_seconds, raw_millis))
    if minutes >= 60 or seconds >= 60:
        raise MediaValidationError(f"invalid VTT timestamp: {value}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_vtt(path: Path) -> list[VttCue]:
    target = path.expanduser().resolve()
    if not target.is_file() or target.stat().st_size == 0:
        raise MediaValidationError(f"VTT is missing or empty: {target}")
    try:
        raw = target.read_text(encoding="utf-8").replace("\r", "")
    except (OSError, UnicodeDecodeError) as error:
        raise MediaValidationError(f"VTT is unreadable: {error}") from error
    if not raw.lstrip().startswith("WEBVTT"):
        raise MediaValidationError("VTT header is missing")
    cues: list[VttCue] = []
    blocks = re.split(r"\n{2,}", raw.strip())
    for block in blocks[1:]:
        lines = [line for line in block.split("\n") if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing_index is None:
            raise MediaValidationError(f"VTT block has no timing line: {block[:80]}")
        parts = lines[timing_index].split(" --> ")
        if len(parts) != 2:
            raise MediaValidationError("VTT timing line is malformed")
        start = _timestamp(parts[0])
        end = _timestamp(parts[1].split()[0])
        text = " ".join(lines[timing_index + 1:]).strip()
        if not text:
            raise MediaValidationError("VTT cue text is empty")
        cues.append(VttCue(start, end, text))
    if not cues:
        raise MediaValidationError("VTT contains no cues")
    return cues


def validate_vtt(cues: Sequence[VttCue], *, duration: float, expected_text: str) -> dict[str, Any]:
    if not math.isfinite(duration) or duration <= 0:
        raise MediaValidationError("narration duration must be positive and finite")
    previous_end = 0.0
    for index, cue in enumerate(cues):
        if cue.start < 0 or cue.end <= cue.start:
            raise MediaValidationError(f"VTT cue {index} has invalid timing")
        if cue.start + 0.050001 < previous_end:
            raise MediaValidationError(f"VTT cue {index} overlaps or moves backward")
        if cue.end > duration + 0.05:
            raise MediaValidationError(f"VTT cue {index} exceeds narration duration")
        if not normalize_text(cue.text):
            raise MediaValidationError(f"VTT cue {index} is empty after normalization")
        previous_end = cue.end
    normalized = "".join(normalize_text(cue.text) for cue in cues)
    expected = normalize_text(expected_text)
    if normalized != expected:
        raise MediaValidationError("VTT text does not exactly cover the expected spoken body")
    return {
        "cue_count": len(cues),
        "normalized_text": normalized,
        "first_start": cues[0].start,
        "last_end": cues[-1].end,
        "duration": duration,
    }
