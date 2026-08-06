from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


class FinalVideoQaError(RuntimeError):
    """Encoded media does not satisfy the release profile."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalVideoQaError(f"ffprobe evidence is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise FinalVideoQaError("ffprobe evidence must be an object")
    return value


def _fps(value: Any) -> float:
    if not isinstance(value, str) or "/" not in value:
        raise FinalVideoQaError("video frame rate is missing")
    numerator, denominator = value.split("/", 1)
    try:
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError) as error:
        raise FinalVideoQaError("video frame rate is invalid") from error
    return result


def _peak(path: Path) -> float | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"(?:Peak|True peak|Peak L|Peak R):\s*(-?\d+(?:\.\d+)?)\s*dB", text, re.I)
    return float(matches[-1]) if matches else None


def evaluate_final_video(
    video: Path,
    qa_dir: Path,
    *,
    expected_duration: float,
    expected_width: int = 1920,
    expected_height: int = 1080,
    expected_fps: float = 30.0,
    expected_frame_labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    video = video.expanduser().resolve(); qa = qa_dir.expanduser().resolve()
    if video.is_symlink() or not video.is_file() or video.stat().st_size == 0:
        raise FinalVideoQaError("final video is missing, symlinked, or empty")
    probe = _load(qa / "ffprobe.json")
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise FinalVideoQaError("ffprobe streams are missing")
    videos = [item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"]
    audios = [item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) < 1:
        raise FinalVideoQaError("final video must contain one video stream and at least one audio stream")
    visual = videos[0]; audio = audios[0]
    checks: list[dict[str, Any]] = []

    def check(identifier: str, condition: bool, observed: Any) -> None:
        checks.append({"id": identifier, "result": "pass" if condition else "fail", "observed": observed})
        if not condition:
            raise FinalVideoQaError(f"final video QA failed: {identifier} ({observed})")

    check("video_codec_h264", visual.get("codec_name") == "h264", visual.get("codec_name"))
    check("video_dimensions", (visual.get("width"), visual.get("height")) == (expected_width, expected_height), [visual.get("width"), visual.get("height")])
    check("pixel_format", visual.get("pix_fmt") == "yuv420p", visual.get("pix_fmt"))
    frame_rate = _fps(visual.get("r_frame_rate"))
    check("frame_rate", abs(frame_rate - expected_fps) <= 0.05, frame_rate)
    check("audio_codec", audio.get("codec_name") == "aac", audio.get("codec_name"))
    check("audio_sample_rate", str(audio.get("sample_rate")) == "48000", audio.get("sample_rate"))
    format_info = probe.get("format") if isinstance(probe.get("format"), Mapping) else {}
    try:
        duration = float(format_info.get("duration"))
    except (TypeError, ValueError) as error:
        raise FinalVideoQaError("final duration is missing") from error
    check("duration_matches_master", abs(duration - expected_duration) <= 0.75, duration)
    black_text = (qa / "blackdetect.txt").read_text(encoding="utf-8", errors="replace") if (qa / "blackdetect.txt").is_file() else ""
    silence_text = (qa / "silencedetect.txt").read_text(encoding="utf-8", errors="replace") if (qa / "silencedetect.txt").is_file() else ""
    check("no_unapproved_black_interval", "black_start" not in black_text, black_text.strip())
    check("no_long_digital_silence", "silence_start" not in silence_text, silence_text.strip())
    peak = _peak(qa / "ebur128.txt")
    if peak is not None:
        check("true_peak_headroom", peak <= -3.0, peak)
    contact = qa / "contact-sheet.jpg"
    check("encoded_contact_sheet", contact.is_file() and contact.stat().st_size > 0, str(contact))
    if expected_frame_labels is not None:
        expected_frames = [f"{index:02d}-{label}.png" for index, label in enumerate(expected_frame_labels, start=1)]
        observed_frames = sorted(path.name for path in qa.glob("*.png") if path.is_file())
        check(
            "encoded_frame_samples",
            set(observed_frames) == set(expected_frames),
            {"expected": expected_frames, "observed": observed_frames},
        )
    return {
        "schema_version": "final-video-qa-report.v1",
        "status": "pass",
        "video": str(video),
        "bytes": video.stat().st_size,
        "expected_duration": expected_duration,
        "observed_duration": duration,
        "checks": checks,
    }

