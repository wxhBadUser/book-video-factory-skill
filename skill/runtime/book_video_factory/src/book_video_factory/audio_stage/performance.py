"""Narration Performance Director (M4A).

A "voice director" that sits in front of the TTS provider. It turns the
approved narration into *performance segments* (1-3 sentences, roughly 6-18s
of speech) -- never per-caption -- and assigns each segment a delivery mode,
emotion, intensity, speed, volume, pitch, pauses and (sparing) sound tags so
the same provider voice finally carries the emotional beat of the book instead
of one flat global rate.

Key contract:
* A performance segment is NOT a caption. Caption boundaries are display
  units; performance segments are expressive units. One segment usually spans
  several captions.
* Intensity variants A/B/C apply a single multiplier to the baseline intensity
  of every segment so the pilot compares identical text/voice/hold with only
  the performance weight changing.
* Pronunciation is applied to the *spoken* text only; display text is never
  rewritten (A11).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from book_video_factory.narrative_functions import NARRATIVE_FUNCTIONS as _CANONICAL_NARRATIVE_FUNCTIONS

# Chinese narration estimate: characters per second at natural speed. Speech
# durations are estimates until the provider returns real timestamps.
_CHARS_PER_SECOND = 4.5
_MIN_SEGMENT_SECONDS = 6.0
_MAX_SEGMENT_SECONDS = 18.0
# Sentence cap yields to emotional coherence for short captions: a 4-short-caption
# grief beat (~9s) is one expressive segment, while long production sentences are
# still cut by the duration cap. The hard constraints are 6-18s of speech.
_MAX_SENTENCES_PER_SEGMENT = 4

NARRATIVE_FUNCTIONS = set(_CANONICAL_NARRATIVE_FUNCTIONS)
DELIVERY_MODES = {"storytelling", "warmth", "tension", "grief", "impact", "reflection"}
EMOTIONS = {"neutral", "calm", "sad", "warm", "tense", "impactful", "reflective"}
ALLOWED_SOUND_TAGS = {"sighs", "breath", "inhale", "exhale", "crying"}

# Performance variants (M4A A/B/C calibration). Same text / same voice / same
# holds; only the intensity weight changes.
VARIANTS = {
    "A": {"label": "RESTRAINED", "intensity_factor": 0.7},
    "B": {"label": "EXPRESSIVE_BOOK_EXPLAINER", "intensity_factor": 1.0},
    "C": {"label": "DRAMATIC", "intensity_factor": 1.3},
}

# Baseline per delivery mode (design section 3.3; starting points, not fixed).
_BASELINE = {
    "storytelling": {"intensity": 0.30, "speed": 1.00, "volume": 1.00, "pitch": 0.0},
    "warmth":       {"intensity": 0.38, "speed": 0.95, "volume": 1.00, "pitch": 0.5},
    "tension":      {"intensity": 0.55, "speed": 1.05, "volume": 1.00, "pitch": 0.5},
    "grief":        {"intensity": 0.50, "speed": 0.92, "volume": 0.95, "pitch": -1.0},
    "impact":       {"intensity": 0.60, "speed": 0.95, "volume": 1.05, "pitch": 0.0},
    "reflection":   {"intensity": 0.30, "speed": 0.90, "volume": 1.00, "pitch": 0.0},
}

_GRIEF_WORDS = ("死", "高烧", "听不见", "不会说话", "哭", "没了", "咽气", "坟")
_WARMTH_WORDS = ("回来了", "还能过", "好好的", "抱着", "家")
_TENSION_WORDS = ("枪毙", "押", "五花大绑", "恶霸", "赌", "枪", "冷气")
_IMPACT_WORDS = ("枪响", "五声", "替你去死")
_REFLECTION_WORDS = ("要不是", "可能", "想想", "要是", "回想")

_STRONG_BOUNDARY = {"impact", "reflection"}
_EMOTION_TO_MODE = {
    "sad": "grief",
    "warm": "warmth",
    "tense": "tension",
    "impactful": "impact",
    "reflective": "reflection",
}


class NarrationPerformanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarrationSourceUnit:
    """One sentence-level narration unit (display + spoken + metadata)."""

    unit_id: str
    text: str                       # display text (never rewritten)
    spoken_text: str                # compiled spoken text (pronunciation applied)
    chapter_id: str = "ch1"
    narrative_function: str = "plot"
    emotion_hint: str | None = None
    is_dialogue_start: bool = False
    is_dialogue_end: bool = False
    is_impact: bool = False
    is_reflection: bool = False
    is_paragraph_start: bool = False


@dataclass(frozen=True)
class PerformanceSegment:
    segment_id: str
    source_unit_ids: tuple[str, ...]
    source_text: str
    spoken_text: str
    narrative_function: str
    delivery_mode: str
    emotion: str | None
    intensity: float
    speed: float
    volume: float
    pitch: float
    pause_before_ms: int
    pause_after_ms: int
    sound_tags: tuple[str, ...] = field(default_factory=tuple)
    performance_note: str = ""
    estimated_seconds: float = 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sentences(text: str) -> list[str]:
    """Split Chinese narration text into sentence units at terminal punct."""

    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [part.strip() for part in parts if part.strip()]


def _estimate_seconds(text: str, speed: float) -> float:
    return len(text) / max(0.1, _CHARS_PER_SECOND * speed)


def _mode_for_unit(unit: NarrationSourceUnit) -> str:
    if unit.emotion_hint in _EMOTION_TO_MODE:
        return _EMOTION_TO_MODE[unit.emotion_hint]
    if unit.is_impact or any(word in unit.text for word in _IMPACT_WORDS):
        return "impact"
    if unit.is_reflection or any(word in unit.text for word in _REFLECTION_WORDS):
        return "reflection"
    if any(word in unit.text for word in _GRIEF_WORDS):
        return "grief"
    if any(word in unit.text for word in _TENSION_WORDS):
        return "tension"
    if any(word in unit.text for word in _WARMTH_WORDS):
        return "warmth"
    return "storytelling"


def _emotion_for_mode(mode: str) -> str | None:
    return {
        "storytelling": "neutral",
        "warmth": "warm",
        "tension": "tense",
        "grief": "sad",
        "impact": "impactful",
        "reflection": "reflective",
    }[mode]


def _sound_tags_for_mode(mode: str) -> tuple[str, ...]:
    if mode == "grief":
        return ("breath",)
    if mode == "impact":
        return ()
    if mode == "reflection":
        return ("sighs",)
    return ()


def _segment_note(units: Sequence[NarrationSourceUnit], mode: str) -> str:
    dialogue = any(u.is_dialogue_start or u.is_dialogue_end for u in units)
    note = mode
    if dialogue:
        note += "; dialogue beat"
    if any(u.is_paragraph_start for u in units):
        note += "; paragraph open"
    return note


def _dominant_mode(units: Sequence[NarrationSourceUnit]) -> str:
    """The delivery mode that dominates a beat; ties fall to the last unit."""

    counts: dict[str, int] = {}
    for unit in units:
        mode = _mode_for_unit(unit)
        counts[mode] = counts.get(mode, 0) + 1
    best = max(counts.items(), key=lambda pair: (pair[1], pair[0] == _mode_for_unit(units[-1])))
    return best[0]


def _split_beats(
    units: Sequence[NarrationSourceUnit],
) -> list[list[NarrationSourceUnit]]:
    """Split sentence units into emotion beats.

    A new beat starts on a strong boundary (impact event, dialogue start) or on
    a delivery-emotion change. A reflection passage is opened by its emotion
    change, but consecutive reflective sentences stay in one beat (never one
    beat per sentence). Once a spoken line has started it stays in one beat
    until it ends, so an emotional peak inside a line (for example a cry)
    never fragments the dialogue.
    """

    beats: list[list[NarrationSourceUnit]] = []
    current: list[NarrationSourceUnit] = []
    for unit in units:
        if current:
            dominant = _dominant_mode(current)
            strong_start = unit.is_impact or unit.is_dialogue_start
            in_dialogue = (
                any(u.is_dialogue_start for u in current)
                and not any(u.is_dialogue_end for u in current)
            )
            emotion_change = _mode_for_unit(unit) != dominant
            if not in_dialogue and (strong_start or emotion_change):
                beats.append(current)
                current = []
        current.append(unit)
    if current:
        beats.append(current)
    return beats


def _chunk_beat(
    beat: Sequence[NarrationSourceUnit],
    *,
    max_sentences: int,
    max_seconds: float,
) -> list[list[NarrationSourceUnit]]:
    """Cut one emotion beat into segments by sentence/duration caps.

    The chunker avoids leaving a 1-unit tail: when cutting at the sentence cap
    would strand a single unit, it cuts one sentence earlier so short beats
    stay paired (e.g. 5 units -> 3+2 instead of 4+1).
    """

    chunks: list[list[NarrationSourceUnit]] = []
    start = 0
    units = list(beat)
    while start < len(units):
        end = min(start + max_sentences, len(units))
        if end - start == max_sentences and len(units) - end == 1:
            end -= 1
        duration = _estimate_seconds(
            "".join(u.spoken_text for u in units[start:end]),
            _BASELINE[_dominant_mode(units[start:end])]["speed"],
        )
        if duration >= max_seconds and end - start >= 2:
            end = start + 1
        chunks.append(units[start:end])
        start = end
    return chunks


def split_performance_segments(
    units: Sequence[NarrationSourceUnit],
    *,
    min_seconds: float = _MIN_SEGMENT_SECONDS,
    max_seconds: float = _MAX_SEGMENT_SECONDS,
    max_sentences: int = _MAX_SENTENCES_PER_SEGMENT,
) -> list[PerformanceSegment]:
    """Group sentence units into performance segments (never per-caption).

    Two stages: emotion beats (strong boundaries + emotion changes, dialogue
    kept whole), then beat chunking by sentence/duration caps. The result is
    deterministic and never equals the caption split.
    """

    if not units:
        raise NarrationPerformanceError("performance plan requires at least one source unit")
    for unit in units:
        if not unit.text.strip() or not unit.spoken_text.strip():
            raise NarrationPerformanceError(f"source unit {unit.unit_id!r} has empty text")
    segments: list[PerformanceSegment] = []
    for beat in _split_beats(list(units)):
        for chunk in _chunk_beat(beat, max_sentences=max_sentences, max_seconds=max_seconds):
            segments.append(_build_segment(chunk))
    return segments


def _build_segment(units: Sequence[NarrationSourceUnit]) -> PerformanceSegment:
    mode = _dominant_mode(units)
    baseline = _BASELINE[mode]
    source_text = "".join(u.text for u in units)
    spoken_text = "".join(u.spoken_text for u in units)
    speed = baseline["speed"]
    duration = _estimate_seconds(spoken_text, speed)
    return PerformanceSegment(
        segment_id=f"perf-{units[0].unit_id}",
        source_unit_ids=tuple(u.unit_id for u in units),
        source_text=source_text,
        spoken_text=spoken_text,
        narrative_function=units[-1].narrative_function,
        delivery_mode=mode,
        emotion=_emotion_for_mode(mode),
        intensity=baseline["intensity"],
        speed=speed,
        volume=baseline["volume"],
        pitch=baseline["pitch"],
        pause_before_ms=600 if units[0].is_paragraph_start else 0,
        pause_after_ms=800 if mode in {"impact", "reflection"} else 600 if mode == "grief" else 0,
        sound_tags=_sound_tags_for_mode(mode),
        performance_note=_segment_note(units, mode),
        estimated_seconds=duration,
    )


def apply_intensity_variant(
    segment: PerformanceSegment,
    *,
    factor: float,
) -> PerformanceSegment:
    return PerformanceSegment(
        segment_id=segment.segment_id,
        source_unit_ids=segment.source_unit_ids,
        source_text=segment.source_text,
        spoken_text=segment.spoken_text,
        narrative_function=segment.narrative_function,
        delivery_mode=segment.delivery_mode,
        emotion=segment.emotion,
        intensity=_clamp(segment.intensity * factor, 0.0, 1.0),
        speed=segment.speed,
        volume=segment.volume,
        pitch=segment.pitch,
        pause_before_ms=segment.pause_before_ms,
        pause_after_ms=segment.pause_after_ms,
        sound_tags=segment.sound_tags,
        performance_note=segment.performance_note,
        estimated_seconds=segment.estimated_seconds,
    )


def _segment_payload(segment: PerformanceSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "source_unit_ids": list(segment.source_unit_ids),
        "source_text": segment.source_text,
        "spoken_text": segment.spoken_text,
        "narrative_function": segment.narrative_function,
        "delivery_mode": segment.delivery_mode,
        "emotion": segment.emotion,
        "intensity": round(segment.intensity, 3),
        "speed": segment.speed,
        "volume": segment.volume,
        "pitch": segment.pitch,
        "pause_before_ms": segment.pause_before_ms,
        "pause_after_ms": segment.pause_after_ms,
        "sound_tags": list(segment.sound_tags),
        "performance_note": segment.performance_note,
        "estimated_seconds": round(segment.estimated_seconds, 3),
    }


def build_narration_performance_plan(
    units: Sequence[NarrationSourceUnit],
    *,
    release_id: str,
    variant: str,
    voice_profile: Mapping[str, Any] | None = None,
    audio_meta_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a ``narration-performance-plan.v1`` document for one variant."""

    if variant not in VARIANTS:
        raise NarrationPerformanceError(f"unknown variant {variant!r}; use A/B/C")
    factor = VARIANTS[variant]["intensity_factor"]
    segments = [
        apply_intensity_variant(seg, factor=factor)
        for seg in split_performance_segments(list(units))
    ]
    return {
        "schema_version": "narration-performance-plan.v1",
        "release_id": release_id,
        "variant": variant,
        "variant_label": VARIANTS[variant]["label"],
        "intensity_factor": factor,
        "audio_meta_sha256": audio_meta_sha256,
        "voice_profile": dict(voice_profile or {}),
        "segments": [_segment_payload(seg) for seg in segments],
    }


def plan_digest(plan: Mapping[str, Any]) -> str:
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
