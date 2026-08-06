#!/usr/bin/env python3
"""Validate formal book research with the Phase 1 fail-closed contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import _bootstrap  # noqa: F401

from book_video_factory.book_research import BookResearch
from book_video_factory.fact_ledger import FactLedger
from book_video_factory.research_validation import ResearchValidationError, validate_formal_research
from book_video_factory.source_ingestion import SourceLevel


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="校验正式原著研究")
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    directory = args.project.resolve() / "01_research_资料搜集"
    research_path = directory / "BOOK_RESEARCH.json"
    if not research_path.is_file():
        print(json.dumps({"valid": False, "errors": ["BOOK_RESEARCH.json missing"]}, ensure_ascii=False))
        return 2
    try:
        payload = _read(research_path)
        ledger_payload = payload.get("fact_ledger") or _read(directory / "FACT_LEDGER.json")
        research = BookResearch(
            book_title=str(payload["book_title"]), author=str(payload["author"]),
            source_level=SourceLevel(str(payload["source_level"])),
            research_status=str(payload["research_status"]),
            chapter_notes=list(payload["chapter_notes"]),
            character_map=list(payload["character_map"]),
            event_candidates=list(payload["event_candidates"]),
            fact_ledger=FactLedger(entries=list(ledger_payload["entries"])),
            research_summary=str(payload.get("research_summary", "")),
            coverage=str(payload.get("coverage", "")),
        )
        report = validate_formal_research(research)
    except (OSError, KeyError, TypeError, ValueError, ResearchValidationError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)]}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"valid": True, "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
