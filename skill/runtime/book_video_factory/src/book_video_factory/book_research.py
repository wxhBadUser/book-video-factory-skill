"""原著深读：章节笔记、人物关系、候选事件、事实台账组装。

不凭模型记忆声称深读完成。来源不足（Level C）时 fail closed。
build_research 可从来源文本生成脚手架，也可接收预构建材料（Phase 6 真实内容由
生成智能体注入，本模块只负责组装与校验）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .fact_ledger import FactLedger, validate_fact_ledger
from .source_ingestion import SourceLevel, SourceManifest

CHAPTER_PATTERN = re.compile(r"第[一二三四五六七八九十百千零0-9]+[章节回]")


class ResearchInsufficientError(RuntimeError):
    """来源不足，禁止正式写作。"""


@dataclass
class BookResearch:
    book_title: str
    author: str
    source_level: SourceLevel
    research_status: str
    chapter_notes: list[dict[str, Any]] = field(default_factory=list)
    character_map: list[dict[str, Any]] = field(default_factory=list)
    event_candidates: list[dict[str, Any]] = field(default_factory=list)
    fact_ledger: FactLedger = field(default_factory=FactLedger)
    research_summary: str = ""
    coverage: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "book-research.v1",
            "book_title": self.book_title,
            "author": self.author,
            "source_level": self.source_level.value,
            "research_status": self.research_status,
            "coverage": self.coverage,
            "chapter_notes": self.chapter_notes,
            "character_map": self.character_map,
            "event_candidates": self.event_candidates,
            "fact_ledger": self.fact_ledger.to_dict(),
            "research_summary": self.research_summary,
        }


def _read_full_text(manifest: SourceManifest) -> str:
    for f in manifest.files:
        if f.kind in {"full_text", "partial_text"} and f.path.endswith((".txt", ".md")):
            p = _resolve_source_file(manifest, f.path)
            try:
                return p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return ""


def _resolve_source_file(manifest: SourceManifest, rel: str) -> Any:
    from pathlib import Path

    base = Path(manifest.source_dir)
    if base.is_file():
        if rel != base.name:
            raise ValueError(f"source manifest path mismatch: {rel} != {base.name}")
        return base
    return base / rel


def _scaffold_chapter_notes(text: str) -> list[dict[str, Any]]:
    matches = list(CHAPTER_PATTERN.finditer(text))
    notes: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()[:2000]
        notes.append({
            "chapter_id": f"CH{i+1:02d}",
            "chapter_marker": m.group(0),
            "summary": body[:200],
            "characters": [],
            "events": [],
            "source_ref": m.group(0),
        })
    return notes


def _scaffold_event_candidates(chapter_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create at most one traceable candidate per chapter.

    This is only a research scaffold. It never pads the list to satisfy the formal
    20-40 event contract; formal validation must fail until real events are authored.
    """
    candidates: list[dict[str, Any]] = []
    for chapter in chapter_notes[:40]:
        chapter_id = chapter.get("chapter_id", "")
        summary = str(chapter.get("summary", "")).strip()
        if not chapter_id or not summary:
            continue
        candidates.append({
            "event_id": f"EV{len(candidates)+1:03d}",
            "chapter_ref": chapter_id,
            "summary": summary[:120],
            "changes_fate": False,
            "reveals_character": False,
            "proves_thesis": False,
            "strong_visual": False,
            "key_dialogue": False,
            "launches_question": False,
            "supports_payoff": False,
            "source_ids": [chapter_id],
        })
    return candidates


def build_research(
    manifest: SourceManifest,
    *,
    book_title: str = "",
    author: str = "",
    chapter_notes: list[dict[str, Any]] | None = None,
    character_map: list[dict[str, Any]] | None = None,
    event_candidates: list[dict[str, Any]] | None = None,
    fact_ledger_entries: list[dict[str, Any]] | None = None,
    research_summary: str = "",
) -> BookResearch:
    """组装原著研究。Level C fail closed。预构建材料优先于脚手架。"""
    if manifest.level == SourceLevel.C:
        raise ResearchInsufficientError(
            f"insufficient_source: level C (summary/memory only) blocks formal writing; "
            f"book={book_title or manifest.book_title}"
        )

    title = book_title or manifest.book_title
    auth = author or manifest.author

    # 预构建材料优先；否则从来源文本生成脚手架。
    if chapter_notes is None:
        text = _read_full_text(manifest)
        chapter_notes = _scaffold_chapter_notes(text) if text else []
    if event_candidates is None:
        event_candidates = _scaffold_event_candidates(chapter_notes)
    if character_map is None:
        character_map = []
    ledger = FactLedger(entries=list(fact_ledger_entries) if fact_ledger_entries else [])
    validate_fact_ledger(ledger)

    summary = research_summary or (
        f"Source level {manifest.level.value}: {manifest.coverage}. "
        f"{len(chapter_notes)} chapter notes, {len(event_candidates)} event candidates, "
        f"{len(ledger.entries)} fact-ledger entries."
    )

    return BookResearch(
        book_title=title,
        author=auth,
        source_level=manifest.level,
        research_status=manifest.research_status,
        chapter_notes=chapter_notes,
        character_map=character_map,
        event_candidates=event_candidates,
        fact_ledger=ledger,
        research_summary=summary,
        coverage=manifest.coverage,
    )
