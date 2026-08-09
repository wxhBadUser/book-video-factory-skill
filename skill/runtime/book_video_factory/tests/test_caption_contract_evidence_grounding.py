"""Task 1: Caption Visual Contract v2 evidence-grounding tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError,
    CaptionVisualContract,
    enrich_captions_to_contracts,
)


def _section(*, narrative_function: str, text: str) -> dict[str, str]:
    return {"section_id": "S1", "narrative_function": narrative_function, "text": text}


def _beat(*, cue: str, required_entities: list[str], narrative_function: str | None = None) -> dict:
    beat = {
        "beatId": "B1",
        "sectionId": "S1",
        "cue": cue,
        "description": cue,
        "requiredEntities": required_entities,
        "forbiddenEntities": [],
    }
    if narrative_function is not None:
        beat["narrative_function"] = narrative_function
    return beat


def test_beat_character_not_named_by_caption_stays_out_of_must_show() -> None:
    """A beat character is context only when the caption names no character."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="theory", text="命运从不向谁解释。")],
        beats=[_beat(cue="命运从不向谁解释。", required_entities=["C002"])],
        captions=[{"caption_id": "c1", "text": "命运从不向谁解释。"}],
        entity_name_maps=[{"C002": "福贵"}],
    )

    contract = contracts[0]
    assert contract.section_id == "S1"
    assert contract.must_show == ()
    assert contract.to_dict()["may_show"] == [
        {
            "entity_id": "C002",
            "natural_language": "福贵",
            "reason": "beat_required_entity_context",
            "evidence": {"source_beat_id": "B1", "required_entity": "C002"},
        }
    ]


def test_resolvable_pronoun_records_upstream_caption_evidence() -> None:
    """A pronoun may require a character only with recorded prior-caption evidence."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="plot", text="福贵牵着老牛。他低头看着那头牛。")],
        beats=[
            _beat(cue="福贵牵着老牛。", required_entities=["C002", "C010"]),
            {
                **_beat(cue="他低头看着那头牛。", required_entities=["C002", "C010"]),
                "beatId": "B2",
            },
        ],
        captions=[
            {"caption_id": "c1", "text": "福贵牵着老牛。"},
            {"caption_id": "c2", "text": "他低头看着那头牛。"},
        ],
        entity_name_maps=[{"C002": "福贵", "C010": "老牛"}],
    )

    pronoun_entry = next(item for item in contracts[1].to_dict()["must_show"] if item["entity_id"] == "C002")
    assert pronoun_entry["natural_language"] == "福贵"
    assert pronoun_entry["reason"] == "pronoun_resolution"
    assert pronoun_entry["evidence"] == {
        "pronoun_resolution": "他",
        "upstream_caption_id": "c1",
        "upstream_caption_text": "福贵牵着老牛。",
        "text_evidence": "他",
    }


def test_beat_narrative_function_conflict_with_section_fails_closed() -> None:
    """The locked script section is authoritative; beat disagreement is an error."""
    with pytest.raises(CaptionContractError, match="narrative_function conflicts"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="theory", text="命运从不向谁解释。")],
            beats=[
                _beat(
                    cue="命运从不向谁解释。",
                    required_entities=[],
                    narrative_function="plot",
                )
            ],
            captions=[{"caption_id": "c1", "text": "命运从不向谁解释。"}],
        )


def test_section_only_caption_uses_locked_section_without_beat_entities() -> None:
    """An intro caption may bind directly to exactly one locked script section."""
    contracts = enrich_captions_to_contracts(
        script_sections=[_section(narrative_function="hook", text="名著太难读了。我们一起读完五十二本。")],
        beats=[],
        captions=[{"caption_id": "c1", "text": "我们一起读完五十二本。"}],
        entity_name_maps=[{"C002": "福贵"}],
    )

    contract = contracts[0]
    assert contract.section_id == "S1"
    assert contract.source_beat_ids == ()
    assert contract.narrative_function == "opening"
    assert contract.must_show == ()
    assert contract.to_dict()["may_show"] == []
    assert contract.to_dict()["scene_state"]["visible_character_ids"] == []
    assert CaptionVisualContract.from_mapping(contract.to_dict()).source_beat_ids == ()


def test_section_only_caption_without_locked_section_fails_closed() -> None:
    """Section-only mode is unavailable when the caption text proves no section membership."""
    with pytest.raises(CaptionContractError, match="locked script section"):
        enrich_captions_to_contracts(
            script_sections=[_section(narrative_function="hook", text="名著太难读了。")],
            beats=[],
            captions=[{"caption_id": "c1", "text": "这句话不在锁定脚本里。"}],
        )


def test_validate_only_cli_reads_metadata_without_writing_contract(tmp_path: Path) -> None:
    """Validation uses only locked metadata and leaves Phase 4 artifacts unchanged."""
    project = tmp_path / "project"
    (project / "02_story_script_故事脚本").mkdir(parents=True)
    (project / "03_images_生成图片").mkdir()
    (project / "04_audio").mkdir()
    (project / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({"performance_version": {"sections": [_section(narrative_function="plot", text="福贵走过田埂。")]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "STORYBOARD_BASE.json").write_text(
        json.dumps([_beat(cue="福贵走过田埂。", required_entities=["C002"] )], ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "04_audio" / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {"c1": {"caption_id": "c1", "text": "福贵走过田埂。"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [{"character_id": "C002", "name": "福贵"}], "object_anchors": [], "scene_anchors": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "book_video_factory/scripts/build_caption_visual_contract.py",
            "--project",
            str(project),
            "--release-id",
            "r1",
            "--validate-only",
        ],
        cwd=Path(__file__).parents[2],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"caption_count": 1, "release_id": "r1", "status": "valid"}
    assert not (project / "04_audio" / "CAPTION_VISUAL_CONTRACT.json").exists()
