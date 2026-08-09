from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Sequence

from .media_probe import VttCue, normalize_text
from .pronunciation import PronunciationError, SpokenCompilation, map_spoken_interval_to_display
from ..semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    CaptionSectionRegister,
    assign_caption_registers,
)


class CaptionAlignmentError(RuntimeError):
    pass


def _normalized_with_spans(value: str) -> tuple[str, list[tuple[int, int]]]:
    normalized_chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for index, char in enumerate(value):
        norm = normalize_text(char)
        if not norm:
            continue
        for normalized_char in norm:
            normalized_chars.append(normalized_char)
            spans.append((index, index + 1))
    # Let every normalized character own trailing punctuation/whitespace until
    # the next semantic character, so the last cue preserves sentence marks.
    expanded: list[tuple[int, int]] = []
    for index, (start, _end) in enumerate(spans):
        next_start = spans[index + 1][0] if index + 1 < len(spans) else len(value)
        expanded.append((start, next_start))
    return "".join(normalized_chars), expanded


def restore_display_captions(
    cues: Sequence[VttCue],
    compilation: SpokenCompilation,
    *,
    min_chars: int,
    max_chars: int,
    min_duration: float,
    allow_short_cues: set[int] | None = None,
    section_register: Sequence[CaptionSectionRegister] | None = None,
) -> list[dict[str, Any]]:
    """Restore display captions and (optionally) tag them with real registers.

    When ``section_register`` is supplied, every restored caption is
    deterministically tagged with the narrative register (and any
    characters/location/time_of_day) of the script section it falls within.
    This is the B06/B07 fix: production captions carry their *real* section
    register instead of being left empty and silently collapsed to "plot" by
    ``CaptionUnit.from_mapping``. Callers that omit the register produce
    untagged captions, which the storyboard gate then rejects (fail closed).
    """
    if not cues:
        raise CaptionAlignmentError("raw VTT contains no cues")
    spoken_normalized, spoken_spans = _normalized_with_spans(compilation.spoken_text)
    cursor = 0
    mapped: list[dict[str, Any]] = []
    allowed_short = allow_short_cues or set()
    for index, cue in enumerate(cues):
        needle = normalize_text(cue.text)
        if not needle:
            raise CaptionAlignmentError(f"cue {index} is empty after normalization")
        if not spoken_normalized.startswith(needle, cursor):
            raise CaptionAlignmentError(f"cue {index} is omitted, duplicated, reordered, or unknown")
        norm_start = cursor
        norm_end = cursor + len(needle)
        spoken_start = spoken_spans[norm_start][0]
        spoken_end = spoken_spans[norm_end - 1][1]
        try:
            display_start, display_end = map_spoken_interval_to_display(compilation, spoken_start, spoken_end)
        except PronunciationError as error:
            raise CaptionAlignmentError(f"cue {index} cannot be restored: {error}") from error
        mapped.append({
            "start": float(cue.start),
            "end": float(cue.end),
            "display_start": display_start,
            "display_end": display_end,
            "allow_short": index in allowed_short,
            "raw_indexes": [index],
        })
        cursor = norm_end
    if cursor != len(spoken_normalized):
        raise CaptionAlignmentError("raw VTT omits part of the spoken body")

    # A pronunciation replacement can be split across Edge cues. Merge any
    # adjacent cues whose display intervals overlap so the helper pronunciation
    # is restored exactly once.
    merged: list[dict[str, Any]] = []
    for item in mapped:
        if merged and item["display_start"] < merged[-1]["display_end"]:
            merged[-1]["end"] = item["end"]
            merged[-1]["display_end"] = max(merged[-1]["display_end"], item["display_end"])
            merged[-1]["allow_short"] = bool(merged[-1].get("allow_short")) and bool(item.get("allow_short"))
            merged[-1]["raw_indexes"].extend(item["raw_indexes"])
        else:
            merged.append(dict(item))

    if section_register is not None:
        try:
            merged = assign_caption_registers(merged, section_register)
        except CaptionGroupingError as error:
            raise CaptionAlignmentError(f"caption register tagging failed: {error}") from error

    captions: list[dict[str, Any]] = []
    for index, item in enumerate(merged, start=1):
        text = compilation.display_text[item["display_start"]:item["display_end"]].strip()
        length = len(normalize_text(text))
        duration = float(item["end"] - item["start"])
        if not text or (length < min_chars and not item.get("allow_short")) or length > max_chars:
            raise CaptionAlignmentError(
                f"restored caption {index} length {length} violates {min_chars}-{max_chars}: {text}"
            )
        if duration + 1e-9 < min_duration:
            raise CaptionAlignmentError(
                f"restored caption {index} duration {duration:.3f}s is below {min_duration:.3f}s"
            )
        caption: dict[str, Any] = {
            "id": f"caption-{index:04d}",
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "duration": round(duration, 3),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "restoration_status": "display-restored",
            "allowShort": bool(item.get("allow_short")),
            "rawCueIndexes": list(item["raw_indexes"]),
        }
        # Only emit the register fields when they were actually tagged. Untagged
        # captions must omit them so CaptionUnit.from_mapping fails closed
        # (a missing register is an error, never a silent "plot").
        if "narrative_function" in item:
            caption["narrative_function"] = item["narrative_function"]
            caption["characters"] = list(item.get("characters", ()))
            caption["location"] = item.get("location", "")
            caption["time_of_day"] = item.get("time_of_day", "")
        captions.append(caption)
    restored = "".join(normalize_text(item["text"]) for item in captions)
    expected = normalize_text(compilation.display_text)
    if restored != expected:
        raise CaptionAlignmentError("restored captions do not exactly cover the frozen display body")
    return captions
