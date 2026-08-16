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
# forced_single_word 的兜底上限：与 schema 的 12s/60 字一致。单个词超过此上限
# 意味着证据畸形到无法进入字幕网格 —— fail-closed（产物无法通过自身 schema 校验）。
_FORCED_MAX_SECONDS = 12.0
_FORCED_MAX_CHARS = 60


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
    # 锁定的脚本 section_id（对齐所得）。契约构建器用它做权威绑定 + 消歧；
    # 未对齐的块（无 --script-package）为 None，走旧文本子串路径。
    source_section_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("source_section_id") is None:
            data.pop("source_section_id", None)  # schema 里是 string；None 不进 JSON
        return data


def _norm_len(text: str) -> int:
    return len("".join(ch for ch in text if not ch.isspace()))


def build_meaning_blocks(
    cues: Sequence[WordCue | Mapping[str, Any]],
    *,
    section_ids: Sequence[str | None] | None = None,
    min_chars: int = _MIN_CHARS,
    max_chars: int = _MAX_CHARS,
    target_seconds: float = _TARGET_SECONDS,
    max_seconds: float = _MAX_SECONDS,
    block_prefix: str = "BLK",
) -> list[MeaningBlock]:
    """``section_ids``（与 ``cues`` 等长，每 cue 一个锁定 section_id，可为 None）
    由 ``align_cues_to_script`` 产出；splitter 记录每个块的 ``source_section_id``
    （= 块首 cue 的 section）。未传时块不带 section（None），兼容纯证据路径。"""
    if section_ids is not None and len(section_ids) != len(cues):
        raise MeaningBlockError(
            f"section_ids length {len(section_ids)} != cue count {len(cues)}"
        )
    words: list[WordCue] = []
    word_sections: list[str | None] = []
    for index, cue in enumerate(cues):
        if isinstance(cue, WordCue):
            text = cue.text.strip()
            if not text:
                continue
            words.append(WordCue(text, cue.start, cue.end))
        else:
            text = str(cue.get("text", "")).strip()
            if not text:
                continue
            words.append(WordCue(text, float(cue["start"]), float(cue["end"])))
        word_sections.append(None if section_ids is None else section_ids[index])
    if not words:
        raise MeaningBlockError("no word cues to split")
    for index in range(1, len(words)):
        if words[index].start < words[index - 1].end - 1e-6:
            raise MeaningBlockError(
                f"word cues out of order: {words[index - 1]!r} -> {words[index]!r}"
            )

    blocks: list[MeaningBlock] = []
    run: list[WordCue] = []
    run_section: str | None = None

    def _close(reason: str) -> None:
        nonlocal run, run_section
        if not run:
            return
        text = "".join(word.text for word in run).strip()
        if not text:
            run, run_section = [], None
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
                source_section_id=run_section,
            )
        )
        run, run_section = [], None

    for index, word in enumerate(words):
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
        if len(run) == 1:
            run_section = word_sections[index]
        text = "".join(w.text for w in run)
        length = _norm_len(text)
        duration = run[-1].end - run[0].start
        # 单个词自身就超过硬上限：不可再切分，强制单独成块
        # （schema 允许 forced_single_word；该块豁免 4s/18-字 上限）。
        if len(run) == 1 and (duration > max_seconds or length > max_chars):
            # 超过 schema 兜底上限则 fail-closed（产物无法通过自身 schema 校验）
            if duration > _FORCED_MAX_SECONDS or length > _FORCED_MAX_CHARS:
                raise MeaningBlockError(
                    f"single word {run[0].text!r} exceeds forced-block ceiling "
                    f"({_FORCED_MAX_SECONDS}s / {_FORCED_MAX_CHARS} chars)"
                )
            _close("forced_single_word")
        elif text.endswith(_TERMINAL) and length >= min_chars:
            _close("punctuation_terminal")
        elif text.endswith(_SOFT) and length >= max(min_chars, 10):
            _close("soft_boundary")
        elif length >= max_chars or duration >= target_seconds:
            _close("target_reached")
    _close("stream_end")

    # 不变量只约束正常块；forced_single_word 是数据本身的产物，豁免。
    if any(b.duration > max_seconds + 1e-9 and b.split_reason != "forced_single_word" for b in blocks):
        raise MeaningBlockError(f"internal invariant: a block exceeded the hard {max_seconds}s cap")
    if any(_norm_len(b.text) > max_chars and b.split_reason != "forced_single_word" for b in blocks):
        raise MeaningBlockError(f"internal invariant: a block exceeded the {max_chars}-char cap")
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
            if "duration" not in doc:
                raise MeaningBlockError(
                    f"chunk {file.name} missing top-level duration; cannot offset master timeline"
                )
            for item in sorted(doc.get("subtitle_timestamps") or [], key=lambda t: t["start"]):
                cues.append(
                    {
                        "text": item["text"],
                        "start": cursor + float(item["start"]),
                        "end": cursor + float(item["end"]),
                    }
                )
            cursor += float(doc["duration"])
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


# 锁定的脚本用、但旁白流会丢掉的标点。契约构建器 caption_contract._BIND_PUNCT
# 必须与此一致（audio_stage 依赖 semantic_alignment，反向不成立，故两处各自定义）。
_SCRIPT_PUNCT = frozenset("，。、！？；：,.;!?…—–·\"'“”‘’「」『』《》（）〈〉【】")


def align_cues_to_script(
    cues: Sequence[Mapping[str, Any]],
    script_sections: Sequence[Mapping[str, Any]],
) -> list[str | None]:
    """把每个 cue 对齐到锁定的脚本 section，返回与 ``cues`` 等长的 section_id 列表。

    真实 MiniMax 旁白 = 锁定脚本去标点后、按连续片段念出（可能跳过整段）。构建
    ``M`` = 全部 section 按顺序去标点串联；把整段旁白文本作为 ``M`` 的一个连续
    子串找到（``M.find``），再把每个 cue 的字符映射回其 M 位置 → 得到 section。

    Fail-closed：旁白不是锁定脚本（去标点后）的连续子串 = 无法产出可绑定字幕，
    拒绝而非产出错位块。空文本 cue 返回 None（不占用 M 位置）。
    """
    stream: list[str] = []
    sec_of: list[str] = []
    for section in script_sections:
        sid = str(section.get("section_id") or section.get("id") or "")
        if not sid:
            raise MeaningBlockError(f"script section without section_id: {section!r}")
        for ch in str(section.get("text", "")):
            if ch in _SCRIPT_PUNCT or ch.isspace():
                continue
            stream.append(ch)
            sec_of.append(sid)
    stream_text = "".join(stream)
    narration = "".join(str(cue["text"]) for cue in cues)
    pos = stream_text.find(narration)
    if pos < 0:
        raise MeaningBlockError(
            "narration is not a contiguous substring of the punct-stripped locked script; "
            "cannot align cues to sections"
        )
    result: list[str | None] = []
    mpos = pos
    for cue in cues:
        text = str(cue["text"])
        if not text:
            result.append(None)
            continue
        result.append(sec_of[mpos])
        mpos += len(text)
    return result


def blocks_to_caption_bindings(
    *,
    blocks: Sequence[MeaningBlock | Mapping[str, Any]],
    release_id: str,
) -> dict[str, Any]:
    """Wrap meaning blocks into a caption-bindings.v1 document the real chain
    contract builder consumes. One caption == one meaning block; one shot ==
    one caption (the audio-stage storyboard builder re-aggregates them into
    real shots later — grouping only reads ``captions``). Values are
    schema-compliant placeholders that the chain overwrites when it re-reasons
    over the text (storyboard_plan write-back).

    IMPORTANT: ``blocks`` MUST already carry master-timeline times. The caller
    applies any body offset in ``load_cues_from_evidence`` BEFORE splitting;
    this function never re-offsets (double-applying body_start was a bug)."""
    normalized: list[MeaningBlock] = [
        b if isinstance(b, MeaningBlock) else MeaningBlock(
            block_id=str(b["block_id"]), text=str(b["text"]).strip(),
            start=float(b["start"]), end=float(b["end"]), duration=float(b["duration"]),
            word_count=int(b.get("word_count", 0)), split_reason=str(b.get("split_reason", "")),
            source_section_id=str(b.get("source_section_id") or "") or None,
        )
        for b in blocks
    ]
    captions: dict[str, dict[str, Any]] = {}
    shots: dict[str, dict[str, Any]] = {}
    for index, blk in enumerate(normalized, start=1):
        cid = blk.block_id
        sid = f"SHOT_{index:04d}"
        start = round(blk.start, 3)
        end = round(blk.end, 3)
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
        if blk.source_section_id:
            captions[cid]["source_section_id"] = blk.source_section_id
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
