"""CaptionCue: the single caption unit derived from provider word timestamps.

Phase 1 refactoring: the former CaptionMeaningBlock was a full production
layer with its own schema, builder script, and tests. It is now demoted to an
internal algorithm. CaptionCue is the ONLY externally-exposed caption unit:

  Provider word cues (MiniMax subtitle_timestamps)
    → meaning_block splitter (internal algorithm)
    → CaptionCue (public contract)
    → SceneSpec

A CaptionCue carries the real provider timestamp, the display text, and the
locked script section it aligns to. No estimated durations, no character-count
projection — timing is 100% provider-derived.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CAPTION_CUE_SCHEMA_VERSION = "caption-cue.v1"

# Timing source markers. Only PROVIDER_TIMING is accepted in production;
# estimated timing is diagnostic-only and must never reach CaptionCue.
PROVIDER_TIMING = "provider"
ESTIMATED_TIMING = "estimated"
_VALID_TIMING_SOURCES = frozenset({PROVIDER_TIMING, ESTIMATED_TIMING})

# Soft targets for the meaning-block splitter. These are GOALS, not hard gates;
# the provider's real inter-word pauses always win. A caption that runs 4.2s
# because MiniMax inserted a dramatic pause is valid, not a contract failure.
TARGET_MIN_CHARS = 8
TARGET_MAX_CHARS = 18
TARGET_SECONDS = 2.8
HARD_MAX_SECONDS = 4.0
HARD_MAX_CHARS = 18


class CaptionCueError(ValueError):
    """A CaptionCue failed contract validation."""


@dataclass(frozen=True)
class CaptionCue:
    """One display caption bound to a real provider audio time window.

    Timing comes exclusively from the TTS provider (MiniMax word timestamps).
    Estimated durations from character counts are NEVER allowed in production.
    """

    caption_id: str
    text: str
    start: float
    end: float
    source_section_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.caption_id).strip():
            raise CaptionCueError("caption_id must be nonempty")
        if not str(self.text).strip():
            raise CaptionCueError(f"caption {self.caption_id}: text must be nonempty")
        if self.end <= self.start:
            raise CaptionCueError(
                f"caption {self.caption_id}: end ({self.end}) must be > start ({self.start})"
            )

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @property
    def char_count(self) -> int:
        return len("".join(ch for ch in self.text if not ch.isspace()))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "caption_id": self.caption_id,
            "text": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": self.duration,
        }
        if self.source_section_id:
            data["source_section_id"] = self.source_section_id
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CaptionCue":
        try:
            return cls(
                caption_id=str(data["caption_id"]).strip(),
                text=str(data["text"]).strip(),
                start=float(data["start"]),
                end=float(data["end"]),
                source_section_id=str(data.get("source_section_id", "") or "").strip(),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CaptionCueError(f"invalid CaptionCue mapping: {error}") from error


def build_caption_cue_document(
    *,
    release_id: str,
    cues: Sequence[CaptionCue],
    provider: str = "minimax",
    source_audio_sha256: str = "",
) -> dict[str, Any]:
    """Build the canonical CAPTION_CUES.json document.

    Cues are validated for temporal ordering and non-overlap. The document
    carries the provider name and audio hash so timing provenance is auditable.
    """
    if not cues:
        raise CaptionCueError("at least one CaptionCue is required")
    sorted_cues = sorted(cues, key=lambda c: c.start)
    previous_end: float | None = None
    seen_ids: set[str] = set()
    for cue in sorted_cues:
        if cue.caption_id in seen_ids:
            raise CaptionCueError(f"duplicate caption_id {cue.caption_id!r}")
        seen_ids.add(cue.caption_id)
        if previous_end is not None and cue.start < previous_end - 1e-6:
            raise CaptionCueError(
                f"caption {cue.caption_id} overlaps previous cue "
                f"(start={cue.start}, previous_end={previous_end})"
            )
        previous_end = cue.end
    return {
        "schema_version": CAPTION_CUE_SCHEMA_VERSION,
        "release_id": str(release_id),
        "provider": str(provider),
        "source_audio_sha256": source_audio_sha256,
        "caption_count": len(sorted_cues),
        "total_duration": round(sorted_cues[-1].end - sorted_cues[0].start, 3),
        "cues": [c.to_dict() for c in sorted_cues],
    }


def validate_caption_cue_document(document: Mapping[str, Any]) -> list[CaptionCue]:
    """Validate a CAPTION_CUES.json document and return parsed CaptionCue objects."""
    if document.get("schema_version") != CAPTION_CUE_SCHEMA_VERSION:
        raise CaptionCueError(
            f"unsupported schema_version {document.get('schema_version')!r}; "
            f"expected {CAPTION_CUE_SCHEMA_VERSION}"
        )
    raw_cues = document.get("cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise CaptionCueError("document must contain a nonempty 'cues' array")
    return [CaptionCue.from_mapping(c) for c in raw_cues]


def load_caption_cues(path: str | Path) -> list[CaptionCue]:
    """Load and validate a CAPTION_CUES.json file from disk."""
    p = Path(path)
    if not p.is_file():
        raise CaptionCueError(f"caption cues file not found: {p}")
    try:
        document = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptionCueError(f"cannot read caption cues file {p}: {error}") from error
    return validate_caption_cue_document(document)


def caption_cue_content_hash(cues: Iterable[CaptionCue]) -> str:
    """Stable hash of caption cue content for provenance binding."""
    payload = [c.to_dict() for c in cues]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _provider_cue_id(index: int, text: str, start: float) -> str:
    """Deterministic caption ID derived from provider cue content.

    The ID binds a caption to the exact provider word/character cues it was
    built from, so downstream SceneSpec timing is traceable to the provider.
    """
    digest = hashlib.sha256(
        f"{index}:{text}:{round(start, 3)}".encode("utf-8")
    ).hexdigest()[:12]
    return f"CAP_{index:04d}_{digest}"


def build_production_caption_cues(
    word_cues: Sequence[Mapping[str, Any]],
    *,
    timing_source: str,
    provider: str = "minimax",
    source_audio_sha256: str = "",
    section_ids: Sequence[str | None] | None = None,
) -> tuple[list[CaptionCue], dict[str, Any]]:
    """Build production CaptionCue list + document from provider word cues.

    This is the canonical production entry point: provider word/character
    timestamps (e.g. MiniMax ``subtitle_timestamps``) → internal meaning-block
    splitter → public CaptionCue contract.

    Fail-closed timing enforcement:
    * ``timing_source`` MUST be ``"provider"``. Estimated/CPM/character-count
      timing is rejected outright.
    * Every word cue MUST carry numeric ``start``/``end`` from the provider.
      Cues flagged ``estimated``/``timing_source="estimated"`` are rejected.
    * No silent fallback to character-count projection, CPM, average duration,
      or evenly-distributed timing is ever performed.

    Returns ``(cues, document)`` where *document* is the canonical
    CAPTION_CUES.json mapping (caller writes it to disk).
    """
    if timing_source != PROVIDER_TIMING:
        raise CaptionCueError(
            f"production CaptionCue requires timing_source={PROVIDER_TIMING!r}, "
            f"got {timing_source!r}; estimated timing is diagnostic-only"
        )
    if not word_cues:
        raise CaptionCueError("at least one provider word cue is required")

    # Validate every cue carries real provider timing before splitting.
    validated: list[dict[str, Any]] = []
    for index, cue in enumerate(word_cues):
        if not isinstance(cue, Mapping):
            raise CaptionCueError(f"word cue #{index} must be a mapping")
        cue_timing = str(cue.get("timing_source", PROVIDER_TIMING)).strip().lower()
        if cue_timing not in (PROVIDER_TIMING, ""):
            raise CaptionCueError(
                f"word cue #{index}: timing_source={cue_timing!r} is not provider "
                f"timing; estimated timing is forbidden in production"
            )
        if bool(cue.get("estimated", False)):
            raise CaptionCueError(
                f"word cue #{index}: estimated timing is forbidden in production"
            )
        text = str(cue.get("text", "")).strip()
        if not text:
            raise CaptionCueError(f"word cue #{index}: empty text")
        try:
            start = float(cue["start"])
            end = float(cue["end"])
        except (KeyError, TypeError, ValueError) as error:
            raise CaptionCueError(
                f"word cue #{index}: missing or non-numeric provider start/end: {error}"
            ) from error
        if end <= start:
            raise CaptionCueError(
                f"word cue #{index}: end ({end}) must be > start ({start})"
            )
        validated.append({"text": text, "start": start, "end": end})

    # Internal meaning-block split (algorithm only — not a production artifact).
    from book_video_factory.audio_stage.meaning_blocks import build_meaning_blocks

    blocks = build_meaning_blocks(validated, section_ids=section_ids)

    cues: list[CaptionCue] = []
    for index, block in enumerate(blocks, start=1):
        cid = _provider_cue_id(index, block.text, block.start)
        cues.append(CaptionCue(
            caption_id=cid,
            text=block.text,
            start=block.start,
            end=block.end,
            source_section_id=block.source_section_id or "",
        ))

    document = build_caption_cue_document(
        release_id="",  # caller fills release_id after construction if needed
        cues=cues,
        provider=provider,
        source_audio_sha256=source_audio_sha256,
    )
    return cues, document


def meaning_blocks_to_caption_cues(
    blocks: Sequence[Mapping[str, Any]],
    *,
    id_prefix: str = "CAP",
) -> list[CaptionCue]:
    """Convert internal meaning-block records to the public CaptionCue contract.

    This is the ONLY sanctioned boundary between the internal meaning-block
    algorithm and the production pipeline. MeaningBlock fields like word_count,
    split_reason are internal diagnostics and are dropped.
    """
    cues: list[CaptionCue] = []
    for index, block in enumerate(blocks, start=1):
        cid = str(block.get("block_id") or block.get("caption_id") or f"{id_prefix}_{index:04d}")
        cues.append(CaptionCue(
            caption_id=cid,
            text=str(block.get("text", "")).strip(),
            start=float(block["start"]),
            end=float(block["end"]),
            source_section_id=str(block.get("source_section_id", "") or "").strip(),
        ))
    return cues


__all__ = [
    "CAPTION_CUE_SCHEMA_VERSION",
    "PROVIDER_TIMING",
    "ESTIMATED_TIMING",
    "TARGET_MIN_CHARS",
    "TARGET_MAX_CHARS",
    "TARGET_SECONDS",
    "HARD_MAX_SECONDS",
    "HARD_MAX_CHARS",
    "CaptionCueError",
    "CaptionCue",
    "build_caption_cue_document",
    "validate_caption_cue_document",
    "load_caption_cues",
    "caption_cue_content_hash",
    "build_production_caption_cues",
    "meaning_blocks_to_caption_cues",
]
