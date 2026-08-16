"""P0-1: meaning-block splitter invariants."""
from __future__ import annotations

import sys
from pathlib import Path

# 每个测试文件自带 src 注入（包未安装、无 conftest；约定见既有测试）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from book_video_factory.audio_stage.meaning_blocks import (
    MeaningBlockError, WordCue, build_meaning_blocks, blocks_to_caption_bindings,
)

# 28 个"字"，每字 0.4s，模拟 ~11s 口语流，句号在第 12 字后（约 4.8s）
CUES = [
    WordCue(t, round(i * 0.4, 3), round(i * 0.4 + 0.35, 3))
    for i, t in enumerate("老人圣地亚哥连续八十四天没有钓到一条鱼。他决定独自出海。")
]


def test_every_block_within_char_and_duration_caps():
    blocks = build_meaning_blocks(CUES)
    assert len(blocks) >= 3
    for index, block in enumerate(blocks):
        # 硬约束（§22）：上界全局生效（≤18 字、≤4s），优先级最高。
        # 8 字下限是软目标：真实旁白在词间有停顿，时长驱动的切分
        # （hard_cap/target_reached）会产生 <8 字块——4s 硬上限永远优先于
        # 8 字下限。仅边界切分（punctuation_terminal/soft_boundary）由构造保证 ≥8。
        assert _norm_len(block.text) <= 18, f"block out of char range: {block.text!r}"
        assert block.duration <= 4.0 + 1e-9, f"block exceeds hard 4s cap: {block.duration}"
        if index < len(blocks) - 1 and block.split_reason in ("punctuation_terminal", "soft_boundary"):
            assert 8 <= _norm_len(block.text), f"boundary-closed block under 8 chars: {block.text!r}"


def test_stream_coverage_is_exact():
    blocks = build_meaning_blocks(CUES)
    joined = "".join(b.text for b in blocks)
    assert _strip(joined) == _strip("老人圣地亚哥连续八十四天没有钓到一条鱼。他决定独自出海。")


def test_punctuation_preferred_boundary():
    blocks = build_meaning_blocks(CUES)
    # 句号位置应在块边界（允许作为块内末尾，但不得把句号前的长句切开又拼回去）
    boundary_texts = {b.text for b in blocks}
    assert any(t.endswith("。") for t in boundary_texts), "a block should close on 。"
    assert not any(t == "他决定独自出海" for t in boundary_texts)  # sanity


def test_empty_stream_rejected():
    with pytest.raises(MeaningBlockError):
        build_meaning_blocks([])


def test_blocks_to_bindings_schema_shape():
    blocks = build_meaning_blocks(CUES)
    doc = blocks_to_caption_bindings(blocks=blocks, release_id="omats-v25")
    assert doc["schema_version"] == "caption-bindings.v1"
    assert doc["coverage"]["caption_count"] == len(blocks)
    assert doc["coverage"]["unbound_captions"] == []
    first = next(iter(doc["captions"].values()))
    for key in ("caption_id", "text", "start", "end", "text_sha256", "restoration_status"):
        assert key in first


def _norm_len(text: str) -> int:
    return len("".join(ch for ch in text if not ch.isspace()))


def _strip(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in "。！？，、；：")


import json
from pathlib import Path

def test_blocks_document_validates_against_schema():
    import jsonschema
    blocks = build_meaning_blocks(CUES)
    doc = {
        "schema_version": "caption-meaning-block.v1",
        "release_id": "omats-v25",
        "block_count": len(blocks),
        "source_evidence_sha256": "0" * 64,
        "stats": {
            "median_duration": 2.8,
            "p95_duration": 3.9,
            "max_duration": max(b.duration for b in blocks),
            "max_chars_any_block": max(_norm_len(b.text) for b in blocks),
        },
        "blocks": [b.to_dict() for b in blocks],
    }
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "caption_meaning_block.v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(doc, schema)


def test_caption_bindings_document_validates_against_schema():
    import jsonschema
    blocks = build_meaning_blocks(CUES)
    doc = blocks_to_caption_bindings(blocks=blocks, release_id="omats-v25")
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "caption_bindings.v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(doc, schema)  # 1 shot == 1 block；unbound_captions 为空数组


def test_aggregates_multi_chunk_evidence():
    import json, tempfile
    from book_video_factory.audio_stage.meaning_blocks import build_meaning_blocks, load_cues_from_evidence
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        # 真实证据形态：顶层 subtitle_timestamps + 每 chunk 自带 duration，
        # 文件名排序聚合，第二块从累计 5.0s 偏移
        a_text = "老人出海远航天际"  # 8 字
        b_text = "他独自迎向风浪"    # 8 字
        a_ts = [
            {"index": i, "text": ch, "start": round(0.1 + i * 0.4, 3), "end": round(0.4 + i * 0.4, 3)}
            for i, ch in enumerate(a_text)
        ]
        b_ts = [
            {"index": i, "text": ch, "start": round(0.1 + i * 0.4, 3), "end": round(0.4 + i * 0.4, 3)}
            for i, ch in enumerate(b_text)
        ]
        (p / "omats-01.json").write_text(
            json.dumps({"chunk_id": "omats-01", "duration": 5.0, "subtitle_timestamps": a_ts}),
            encoding="utf-8")
        (p / "omats-02.json").write_text(
            json.dumps({"chunk_id": "omats-02", "duration": 3.5, "subtitle_timestamps": b_ts}),
            encoding="utf-8")
        cues = load_cues_from_evidence(p)
        assert cues[0]["start"] == 0.1
        assert cues[8]["start"] == 5.1  # 第二块已偏移到累计 5.0s 之后
        blocks = build_meaning_blocks(cues)
        assert blocks[0].start == 0.1
        assert all(b.duration <= 4.0 for b in blocks)
        assert blocks[-1].end > 5.0  # 跨 chunk 聚合成链
