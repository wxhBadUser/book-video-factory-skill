"""P0-9: detect silence/placeholder narration before render."""
from __future__ import annotations

import array
import wave
from pathlib import Path


class PlaceholderAudioError(ValueError):
    pass


def assert_body_is_real_speech(workspace: Path, body_path: Path, *, min_rms: float = 400.0) -> None:
    """Fail-closed: body audio must have non-negligible RMS (not anullsrc / silence).

    MiniMax narration RMS is ~thousands; ffmpeg anullsrc is 0. A pure-silence
    body is a placeholder and must never reach the encoder. Opening lead/flash/
    reveal/BGM placeholders are NOT gated here (BGM silence is legitimate).
    """
    path = Path(body_path)
    if not path.is_absolute():
        path = workspace / path
    if not path.is_file() or path.stat().st_size < 44:
        raise PlaceholderAudioError("narration body audio is missing or empty")
    if not path.name.lower().endswith(".wav"):
        raise PlaceholderAudioError("static render requires a wav narration body for the placeholder check")
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.readframes(min(wav.getnframes(), 16000 * 10))  # cap analysis at 10s
    except (EOFError, wave.Error) as error:
        raise PlaceholderAudioError(f"narration body audio unreadable: {error}") from error
    if len(frames) < 1600:
        raise PlaceholderAudioError("narration body audio is too short")
    vals = array.array("h", frames[: len(frames) // 2 * 2])
    if not vals:
        raise PlaceholderAudioError("narration body audio has no samples")
    rms = (sum(v * v for v in vals) / len(vals)) ** 0.5
    if rms < min_rms:
        raise PlaceholderAudioError(f"narration body RMS {rms:.1f} below {min_rms}: looks like silence/placeholder")
