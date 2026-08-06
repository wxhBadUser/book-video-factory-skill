#!/usr/bin/env python3
"""构建原著研究：从来源目录生成 01_research/* 产物。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import _bootstrap  # noqa: F401

from book_video_factory.source_ingestion import build_source_sanitization_report, ingest_source
from book_video_factory.book_research import build_research, ResearchInsufficientError


def main() -> int:
    parser = argparse.ArgumentParser(description="构建原著研究")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--project", required=True, help="项目目录（01_research 父级）")
    parser.add_argument("--book-title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--materials", default="", help="可选预构建材料 JSON（chapter_notes/character_map/event_candidates/fact_ledger_entries）")
    args = parser.parse_args()

    manifest = ingest_source(Path(args.source_dir), book_title=args.book_title, author=args.author)
    materials = {}
    if args.materials:
        materials = json.loads(Path(args.materials).read_text(encoding="utf-8"))

    try:
        research = build_research(
            manifest,
            book_title=args.book_title,
            author=args.author,
            chapter_notes=materials.get("chapter_notes"),
            character_map=materials.get("character_map"),
            event_candidates=materials.get("event_candidates"),
            fact_ledger_entries=materials.get("fact_ledger_entries"),
            research_summary=materials.get("research_summary", ""),
        )
    except ResearchInsufficientError as exc:
        print(json.dumps({"research_status": "insufficient_source", "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return 2

    research_dir = Path(args.project) / "01_research_资料搜集"
    research_dir.mkdir(parents=True, exist_ok=True)
    manifest_payload = manifest.to_dict()
    (research_dir / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (research_dir / "SOURCE_SANITIZATION_REPORT.json").write_text(
        json.dumps(
            build_source_sanitization_report(manifest_payload, Path(args.project)),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    d = research.to_dict()
    for key in ("chapter_notes", "character_map", "event_candidates"):
        (research_dir / f"{key}.json").write_text(
            json.dumps(d[key], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (research_dir / "fact_ledger.json").write_text(
        json.dumps(d["fact_ledger"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (research_dir / "research_summary.md").write_text(
        f"# {research.book_title} 研究摘要\n\n{research.research_summary}\n", encoding="utf-8")
    # 新增长产物：quote_ledger / chronology / relationship_map
    for key in ("quote_ledger", "chronology", "relationship_map"):
        val = materials.get(key)
        if val is not None:
            (research_dir / f"{key}.json").write_text(
                json.dumps(val, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "research_status": research.research_status,
        "source_level": research.source_level.value,
        "chapter_notes": len(research.chapter_notes),
        "event_candidates": len(research.event_candidates),
        "fact_ledger_entries": len(research.fact_ledger.entries),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
