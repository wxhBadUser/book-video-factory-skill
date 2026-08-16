"""契约绑定修复：标点不敏感 + source_section_id 权威消歧。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError, enrich_captions_to_contracts,
)


def _section(sid: str, text: str, narrative_function: str = "plot"):
    return {"section_id": sid, "text": text, "narrative_function": narrative_function}


def test_punct_insensitive_section_binding():
    # 真实旁白去标点、在词间切开 → caption 不是锁定文本的 verbatim 子串。
    # 绑定必须标点不敏感，否则 "因为那片海说" 永远匹配不上 "因为那片海，说白了"。
    sections = [_section("S09", "因为那片海，说白了，就是每个人心里那场注定会被夺走的搏斗。")]
    contracts = enrich_captions_to_contracts(
        script_sections=sections, beats=[],
        captions=[{"caption_id": "c1", "text": "因为那片海说"}],
    )
    assert contracts[0].section_id == "S09"


def test_source_section_id_is_authoritative_disambiguation():
    # "我出海出得太远了" 同时出现在 S08（cost）与 S10（ending_image）。
    # 文本匹配歧义时，splitter 对齐记录的 source_section_id 是权威 → 绑 S10。
    sections = [
        _section("S08", "老人轻声说，我出海，出得太远了。"),
        _section("S10", "然后又轻轻说了一遍，我出海，出得太远了。", narrative_function="ending_image"),
    ]
    contracts = enrich_captions_to_contracts(
        script_sections=sections, beats=[],
        captions=[{"caption_id": "c1", "text": "海出得太远了", "source_section_id": "S10"}],
    )
    assert contracts[0].section_id == "S10"


def test_source_section_conflict_fails_closed():
    # source_section_id 声称 S09，但文本在 S09（去标点）中不存在 → fail-closed。
    sections = [
        _section("S09", "因为那片海，说白了。"),
        _section("S10", "老人醒来，天已经亮了。"),
    ]
    with pytest.raises(CaptionContractError, match="does not occur in its aligned section"):
        enrich_captions_to_contracts(
            script_sections=sections, beats=[],
            captions=[{"caption_id": "c1", "text": "完全不存在的旁白", "source_section_id": "S09"}],
        )


def test_unknown_source_section_fails_closed():
    sections = [_section("S09", "因为那片海，说白了。")]
    with pytest.raises(CaptionContractError, match="is not a locked script section"):
        enrich_captions_to_contracts(
            script_sections=sections, beats=[],
            captions=[{"caption_id": "c1", "text": "因为那片海说", "source_section_id": "S99"}],
        )


def test_legacy_caption_without_source_section_still_binds():
    # 无 source_section_id 的旧 caption：保持原文本子串路径（标点不敏感）＋ 唯一 section。
    sections = [_section("S09", "因为那片海，说白了，就是每个人心里那场注定会被夺走的搏斗。")]
    contracts = enrich_captions_to_contracts(
        script_sections=sections, beats=[],
        captions=[{"caption_id": "c1", "text": "因为那片海说白了就是每个人"}],
    )
    assert contracts[0].section_id == "S09"


def test_source_section_beat_filter():
    # 带 source_section_id 时，beat 匹配只接受属于该 section 的 beat。
    sections = [
        _section("S10", "老人醒来，天已经亮了，阳光从门缝里斜进来。", narrative_function="ending_image"),
    ]
    beats = [{"beatId": "B010", "sectionId": "S10", "cue": "老人醒来，天已经亮了，阳光从门缝里斜进来",
              "requiredEntities": ["C001"], "narrative_function": "ending_image"}]
    contracts = enrich_captions_to_contracts(
        script_sections=sections, beats=beats,
        captions=[{"caption_id": "c1", "text": "老人醒来天已经亮了阳", "source_section_id": "S10"}],
    )
    assert contracts[0].section_id == "S10"
    assert contracts[0].source_beat_ids == ("B010",)
