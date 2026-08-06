from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import AudioStageContractError, validate_pronunciation_lexicon


class PronunciationError(RuntimeError):
    """Display/spoken text cannot be compiled or restored safely."""


_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:…—\-\n\r]")


@dataclass(frozen=True)
class SpokenSegment:
    display_start: int
    display_end: int
    spoken_start: int
    spoken_end: int
    entry_id: str | None


@dataclass(frozen=True)
class SpokenCompilation:
    display_text: str
    spoken_text: str
    segments: tuple[SpokenSegment, ...]
    display_sha256: str
    spoken_sha256: str
    scope: str


def _punctuation_signature(value: str) -> str:
    return "".join(_PUNCT_RE.findall(value))


def _active_entries(lexicon: Mapping[str, Any], scope: str) -> list[dict[str, Any]]:
    try:
        normalized = validate_pronunciation_lexicon(__import__("pathlib").Path("."), lexicon)
    except AudioStageContractError as error:
        raise PronunciationError(str(error)) from error
    entries = [
        dict(item)
        for item in normalized["entries"]
        if item["scope"] == scope or (scope.startswith("chapter:") and item["scope"] == scope)
    ]
    entries.sort(key=lambda item: (-len(item["display"]), item["entry_id"]))
    return entries


def compile_spoken_script(
    display_text: str,
    lexicon: Mapping[str, Any],
    *,
    scope: str = "body",
) -> SpokenCompilation:
    if not isinstance(display_text, str) or not display_text:
        raise PronunciationError("display text must be nonempty")
    entries = _active_entries(lexicon, scope)
    for item in entries:
        if item["display"] == item["spoken"]:
            raise PronunciationError(f"pronunciation entry is a no-op: {item['entry_id']}")
        if _punctuation_signature(item["display"]) != _punctuation_signature(item["spoken"]):
            raise PronunciationError(f"pronunciation entry changes punctuation structure: {item['entry_id']}")

    # Find replacements left-to-right. At every position, the longest matching
    # entry wins. This permits a short name to exist independently while
    # preventing it from rewriting the interior of a longer name.
    replacements: list[tuple[int, int, dict[str, Any]]] = []
    counts = {item["entry_id"]: 0 for item in entries}
    cursor = 0
    while cursor < len(display_text):
        chosen = next((item for item in entries if display_text.startswith(item["display"], cursor)), None)
        if chosen is None:
            cursor += 1
            continue
        end = cursor + len(chosen["display"])
        replacements.append((cursor, end, chosen))
        counts[chosen["entry_id"]] += 1
        cursor = end

    for item in entries:
        count = counts[item["entry_id"]]
        policy = item["occurrence_policy"]
        if policy["mode"] == "exact" and count != policy["count"]:
            raise PronunciationError(
                f"pronunciation entry {item['entry_id']} expected {policy['count']} occurrences, found {count}"
            )
        if policy["mode"] == "all" and item["display"] in display_text and count == 0:
            raise PronunciationError(
                f"pronunciation entry {item['entry_id']} is fully shadowed by a longer replacement"
            )
        if policy["mode"] == "exact" and policy["count"] > 0 and count == 0:
            raise PronunciationError(f"required pronunciation entry is missing: {item['entry_id']}")

    output: list[str] = []
    segments: list[SpokenSegment] = []
    display_cursor = 0
    spoken_cursor = 0
    for start, end, item in replacements:
        if start > display_cursor:
            raw = display_text[display_cursor:start]
            output.append(raw)
            segments.append(
                SpokenSegment(display_cursor, start, spoken_cursor, spoken_cursor + len(raw), None)
            )
            spoken_cursor += len(raw)
        spoken = item["spoken"]
        output.append(spoken)
        segments.append(SpokenSegment(start, end, spoken_cursor, spoken_cursor + len(spoken), item["entry_id"]))
        spoken_cursor += len(spoken)
        display_cursor = end
    if display_cursor < len(display_text):
        raw = display_text[display_cursor:]
        output.append(raw)
        segments.append(
            SpokenSegment(display_cursor, len(display_text), spoken_cursor, spoken_cursor + len(raw), None)
        )
    spoken_text = "".join(output)
    if not segments:
        segments = [SpokenSegment(0, len(display_text), 0, len(display_text), None)]
    return SpokenCompilation(
        display_text=display_text,
        spoken_text=spoken_text,
        segments=tuple(segments),
        display_sha256=hashlib.sha256(display_text.encode("utf-8")).hexdigest(),
        spoken_sha256=hashlib.sha256(spoken_text.encode("utf-8")).hexdigest(),
        scope=scope,
    )


def map_spoken_interval_to_display(
    compilation: SpokenCompilation,
    start: int,
    end: int,
) -> tuple[int, int]:
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise PronunciationError("spoken interval must use integer character offsets")
    if start < 0 or end < start or end > len(compilation.spoken_text):
        raise PronunciationError("spoken interval is outside the compiled text")
    if start == end:
        return (0, 0) if start == 0 else (len(compilation.display_text), len(compilation.display_text))
    touched = [segment for segment in compilation.segments if segment.spoken_end > start and segment.spoken_start < end]
    if not touched:
        raise PronunciationError("spoken interval does not overlap a mapping segment")
    display_starts: list[int] = []
    display_ends: list[int] = []
    for segment in touched:
        overlap_start = max(start, segment.spoken_start)
        overlap_end = min(end, segment.spoken_end)
        if segment.entry_id is not None:
            display_starts.append(segment.display_start)
            display_ends.append(segment.display_end)
            continue
        display_starts.append(segment.display_start + (overlap_start - segment.spoken_start))
        display_ends.append(segment.display_start + (overlap_end - segment.spoken_start))
    result = min(display_starts), max(display_ends)
    if result[0] < 0 or result[1] > len(compilation.display_text) or result[0] > result[1]:
        raise PronunciationError("display interval mapping is not monotonic")
    return result


def restore_spoken_span(compilation: SpokenCompilation, start: int, end: int) -> str:
    display_start, display_end = map_spoken_interval_to_display(compilation, start, end)
    return compilation.display_text[display_start:display_end]
