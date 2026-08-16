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
