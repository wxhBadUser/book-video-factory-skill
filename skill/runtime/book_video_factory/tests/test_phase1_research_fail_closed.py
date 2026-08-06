from __future__ import annotations

import tempfile
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.book_research import BookResearch, build_research
from book_video_factory.fact_ledger import FactLedger
from book_video_factory.research_validation import (
    ResearchValidationError,
    validate_formal_research,
)
from book_video_factory.source_ingestion import SourceLevel, ingest_source


def _research(*, events: int = 20) -> BookResearch:
    chapters = [
        {
            "chapter_id": f"CH{i:02d}",
            "chapter_marker": f"第{i}章",
            "summary": f"第{i}章的可靠摘要",
            "characters": ["老人"],
            "events": [],
            "source_ref": f"第{i}章",
        }
        for i in range(1, 21)
    ]
    candidates = [
        {
            "event_id": f"EV{i:03d}",
            "chapter_ref": f"CH{i:02d}",
            "summary": f"老人完成第{i}个真实事件",
            "changes_fate": True,
            "reveals_character": True,
            "proves_thesis": i % 2 == 0,
            "strong_visual": True,
            "key_dialogue": False,
            "launches_question": True,
            "supports_payoff": i > 15,
            "source_ids": [f"CH{i:02d}", f"F{i:03d}"],
        }
        for i in range(1, events + 1)
    ]
    ledger = FactLedger(
        entries=[
            {
                "id": f"F{i:03d}",
                "type": "F",
                "claim": f"第{i}个事实",
                "source_ref": f"第{i}章",
                "verified": True,
            }
            for i in range(1, 21)
        ]
    )
    return BookResearch(
        book_title="老人与海",
        author="欧内斯特·海明威",
        source_level=SourceLevel.A,
        research_status="ready",
        chapter_notes=chapters,
        character_map=[{"character_id": "C001", "name": "老人"}],
        event_candidates=candidates,
        fact_ledger=ledger,
        coverage="full_text_available",
    )


def test_missing_source_path_is_rejected() -> None:
    with pytest.raises(FileNotFoundError):
        ingest_source(Path("/definitely/missing/book-source"))


def test_pdf_without_extracted_text_is_not_level_a() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "book.pdf"
        source.write_bytes(b"%PDF-1.4 opaque bytes are not extracted prose")
        manifest = ingest_source(source, book_title="某书", author="某作者")
    assert manifest.level is SourceLevel.C
    assert manifest.is_full_text is False
    assert manifest.files[0].kind == "opaque_document"


def test_research_does_not_fabricate_candidates_to_reach_twenty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "full_text.txt"
        source.write_text(
            "第一章 " + "老人出海。" * 1000 + "\n第二章 " + "老人归来。" * 1000,
            encoding="utf-8",
        )
        manifest = ingest_source(source, book_title="老人与海", author="海明威")
        research = build_research(manifest)
    assert len(research.event_candidates) == 2
    assert not any("derived" in str(event).lower() for event in research.event_candidates)


def test_formal_research_rejects_fewer_than_twenty_real_events() -> None:
    with pytest.raises(ResearchValidationError, match="20.*40"):
        validate_formal_research(_research(events=19))


def test_formal_research_rejects_event_without_source_ids() -> None:
    research = _research()
    research.event_candidates[0]["source_ids"] = []
    with pytest.raises(ResearchValidationError, match="source_ids"):
        validate_formal_research(research)


def test_formal_research_rejects_unknown_chapter_and_fact_references() -> None:
    research = _research()
    research.event_candidates[0]["chapter_ref"] = "CH999"
    research.event_candidates[0]["source_ids"] = ["F999"]
    with pytest.raises(ResearchValidationError, match="unknown"):
        validate_formal_research(research)


def test_formal_research_rejects_placeholder_tokens() -> None:
    research = _research()
    research.event_candidates[0]["summary"] = "derived candidate 1"
    with pytest.raises(ResearchValidationError, match="placeholder"):
        validate_formal_research(research)


def test_valid_research_returns_reference_graph() -> None:
    report = validate_formal_research(_research())
    assert report["status"] == "pass"
    assert report["chapter_count"] == 20
    assert report["event_count"] == 20
    assert report["fact_count"] == 20
    assert report["reference_graph"]["EV001"] == ["CH01", "F001"]
