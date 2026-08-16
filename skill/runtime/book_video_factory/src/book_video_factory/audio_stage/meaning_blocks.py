"""P0-1: split a real narration word-cue stream into CaptionMeaningBlocks.

The display caption grid is NOT the TTS provider's sentence segmentation and
NOT a greedy max-char coalesce. Each block is a contiguous run of provider
words (per-character for MiniMax Chinese) that (a) forms a meaning unit,
(b) targets 8-18 chars (the 8-char floor is a soft goal; the 4s/18-char hard
caps always win — inter-word narration pauses force short blocks), (c)
targets 1.5-3.2s with a hard 4s cap. Splitting closes
at punctuation first, then at soft/breath boundaries, then is forced by
duration/length so no block exceeds the caps. Post-hoc over stored evidence:
no re-synthesis needed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class MeaningBlockError(ValueError):
    """Raised on any meaning-block construction failure."""


_TERMINAL = ("。", "！", "？", ".", "!", "?", "；", ";")
_SOFT = ("，", "、", "：", ",", ":", "：")

_MIN_CHARS = 8
_MAX_CHARS = 18
_TARGET_SECONDS = 2.8
_MAX_SECONDS = 4.0
_MIN_SECONDS = 1.2


@dataclass(frozen=True)
class WordCue:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class MeaningBlock:
    block_id: str
    text: str
    start: float
    end: float
    duration: float
    word_count: int
    split_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_len(text: str) -> int:
    return len("".join(ch for ch in text if not ch.isspace()))


def build_meaning_blocks(
    cues: Sequence[WordCue | Mapping[str, Any]],
    *,
    min_chars: int = _MIN_CHARS,
    max_chars: int = _MAX_CHARS,
    target_seconds: float = _TARGET_SECONDS,
    max_seconds: float = _MAX_SECONDS,
    min_seconds: float = _MIN_SECONDS,
    block_prefix: str = "BLK",
) -> list[MeaningBlock]:
    words: list[WordCue] = []
    for cue in cues:
        if isinstance(cue, WordCue):
            words.append(cue)
        else:
            text = str(cue.get("text", "")).strip()
            if not text:
                continue
            words.append(WordCue(text, float(cue["start"]), float(cue["end"])))
    if not words:
        raise MeaningBlockError("no word cues to split")
    for index in range(1, len(words)):
        if words[index].start < words[index - 1].end - 1e-6:
            raise MeaningBlockError(
                f"word cues out of order: {words[index - 1]!r} -> {words[index]!r}"
            )

    blocks: list[MeaningBlock] = []
    run: list[WordCue] = []

    def _close(reason: str) -> None:
        nonlocal run
        if not run:
            return
        text = "".join(word.text for word in run).strip()
        if not text:
            run = []
            return
        blocks.append(
            MeaningBlock(
                block_id=f"{block_prefix}_{len(blocks) + 1:04d}",
                text=text,
                start=run[0].start,
                end=run[-1].end,
                duration=round(run[-1].end - run[0].start, 3),
                word_count=len(run),
                split_reason=reason,
            )
        )
        run = []

    for word in words:
        # If adding this word would breach a HARD cap, close the run first.
        # Duration/length caps are hard invariants: any run that would breach
        # them closes even when it is <8 chars. Real MiniMax streams carry
        # inter-word narration gaps of seconds, so a <8-char run can already
        # span >4s — the 4s/18-char caps must always win over the 8-char soft
        # minimum (the guard `len(run) >= min_chars` is deliberately absent).
        if run:
            probe_len = _norm_len("".join(w.text for w in run) + word.text)
            probe_dur = word.end - run[0].start
            if probe_dur > max_seconds or probe_len > max_chars:
                _close("hard_cap")
        run.append(word)
        text = "".join(w.text for w in run)
        length = _norm_len(text)
        duration = run[-1].end - run[0].start
        if text.endswith(_TERMINAL) and length >= min_chars:
            _close("punctuation_terminal")
        elif text.endswith(_SOFT) and length >= max(min_chars, 10):
            _close("soft_boundary")
        elif length >= max_chars or duration >= target_seconds:
            _close("target_reached")
    _close("stream_end")

    if any(block.duration > max_seconds + 1e-9 for block in blocks):
        raise MeaningBlockError("internal invariant: a block exceeded the hard 4s cap")
    if any(_norm_len(block.text) > max_chars for block in blocks):
        raise MeaningBlockError("internal invariant: a block exceeded the 18-char cap")
    return blocks


def load_cues_from_evidence(evidence_path: Path, *, body_start: float = 0.0) -> list[dict[str, Any]]:
    """Load word cues from one or many evidence JSONs onto the master timeline.

    Real MiniMax chunk evidence carries top-level ``subtitle_timestamps``
    (per-character granularity) with chunk-relative ``start``/``end`` and its
    own top-level ``duration``. A directory is sorted by filename and each
    chunk is offset by the cumulative prior duration; ``body_start`` shifts the
    whole stream to the master timeline (after the opening lead).
    """
    if evidence_path.is_dir():
        files = sorted(evidence_path.glob("*.json"))
        if not files:
            raise MeaningBlockError(f"no evidence JSON files in {evidence_path}")
        cues: list[dict[str, Any]] = []
        cursor = body_start
        for file in files:
            doc = json.loads(file.read_text(encoding="utf-8"))
            for item in sorted(doc.get("subtitle_timestamps") or [], key=lambda t: t["start"]):
                cues.append(
                    {
                        "text": item["text"],
                        "start": cursor + float(item["start"]),
                        "end": cursor + float(item["end"]),
                    }
                )
            cursor += float(doc.get("duration", 0.0))
        return cues
    doc = json.loads(evidence_path.read_text(encoding="utf-8"))
    return [
        {
            "text": item["text"],
            "start": body_start + float(item["start"]),
            "end": body_start + float(item["end"]),
        }
        for item in sorted(doc.get("subtitle_timestamps") or [], key=lambda t: t["start"])
    ]


def blocks_to_caption_bindings(
    *,
    blocks: Sequence[MeaningBlock | Mapping[str, Any]],
    release_id: str,
    body_start: float = 0.0,
) -> dict[str, Any]:
    """Wrap meaning blocks into a caption-bindings.v1 document the real chain
    contract builder consumes. One caption == one meaning block; one shot ==
    one caption (the audio-stage storyboard builder re-aggregates them into
    real shots later — grouping only reads ``captions``). Values are
    schema-compliant placeholders that the chain overwrites when it re-reasons
    over the text (storyboard_plan write-back)."""
    normalized: list[MeaningBlock] = [
        b if isinstance(b, MeaningBlock) else MeaningBlock(
            block_id=str(b["block_id"]), text=str(b["text"]).strip(),
            start=float(b["start"]), end=float(b["end"]), duration=float(b["duration"]),
            word_count=int(b.get("word_count", 0)), split_reason=str(b.get("split_reason", "")),
        )
        for b in blocks
    ]
    captions: dict[str, dict[str, Any]] = {}
    shots: dict[str, dict[str, Any]] = {}
    for index, blk in enumerate(normalized, start=1):
        cid = blk.block_id
        sid = f"SHOT_{index:04d}"
        start = round(blk.start + body_start, 3)
        end = round(blk.end + body_start, 3)
        captions[cid] = {
            "caption_id": cid,
            "text": blk.text,
            "start": start,
            "end": end,
            "text_sha256": hashlib.sha256(blk.text.encode("utf-8")).hexdigest(),
            "shot_ids": [sid],
            "shared_required_entities": [],
            "semantic_rationale": "auto-split from narration word timestamps",
            "semantic_bridge_mode": "direct",
            "rationale_is_boilerplate": False,
            "restoration_status": "display-restored",
        }
        shots[sid] = {
            "shot_id": sid,
            "start": start,
            "end": end,
            "source_beat_ids": [cid],
            "caption_ids": [cid],
            "uncovered_time": 0.0,
            "intentional_hold": False,
            "hold_reason": "",
            "shared_required_entities": [],
            "semantic_bridge_mode": "direct",
            "source_terms_named": [],
            "image_terms_named": [],
            "rationale_is_boilerplate": False,
        }
    return {
        "schema_version": "caption-bindings.v1",
        "release_id": release_id,
        "captions": captions,
        "shots": shots,
        "coverage": {
            "caption_count": len(captions),
            "shot_count": len(shots),
            "unbound_captions": [],
        },
    }
