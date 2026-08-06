"""Phase 3 失败测试：原著来源与深读。

TDD：引用尚未实现的 source_ingestion/book_research/fact_ledger，运行应失败。
实现后须通过。

来源等级：
- Level A（全文）→ research_status=ready，允许正式写作；
- Level B（部分原文+权威资料）→ research_status=limited，标记覆盖与缺失；
- Level C（简介或模型记忆）→ research_status=insufficient_source，fail closed 阻止写作。
fact_ledger 每条 F/Q/A/I/E 可追溯。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.source_ingestion import (  # noqa: E402
    SourceLevel,
    SourceManifest,
    ingest_source,
)
from book_video_factory.book_research import (  # noqa: E402
    BookResearch,
    ResearchInsufficientError,
    build_research,
)
from book_video_factory.fact_ledger import (  # noqa: E402
    FactLedger,
    FactType,
    validate_fact_ledger,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _level_a_source(tmp: Path) -> Path:
    """Level A：完整原文（模拟全文，>8000 字符含章节标记）。"""
    src = tmp / "source"
    chapter = "第一章 呼啸山庄来了一个弃儿。" + "他被称为希斯克利夫。" * 200 + "\n"
    text = "".join(f"第{['一','二','三','四','五'][i%5]}章 章节{i+1}。" + "内容内容内容内容。" * 60 + "\n" for i in range(20))
    text = (chapter + text) if len(chapter) + len(text) < 8000 else text
    _write(src / "full_text.txt", text)
    _write(src / "meta.json", '{"title":"呼啸山庄","author":"艾米莉·勃朗特"}')
    return src


def _level_c_source(tmp: Path) -> Path:
    """Level C：只有简介。"""
    src = tmp / "source"
    _write(src / "summary.txt", "这是一部关于约克郡庄园的爱情悲剧。")
    return src


class SourceIngestionTests(unittest.TestCase):
    def test_level_a_full_text_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_a_source(Path(tmp))
            manifest = ingest_source(src)
            self.assertEqual(manifest.level, SourceLevel.A)
            self.assertEqual(manifest.research_status, "ready")
            self.assertTrue(manifest.is_full_text)

    def test_level_c_summary_only_blocks_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_c_source(Path(tmp))
            manifest = ingest_source(src)
            self.assertEqual(manifest.level, SourceLevel.C)
            self.assertEqual(manifest.research_status, "insufficient_source")

    def test_manifest_records_files_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_a_source(Path(tmp))
            manifest = ingest_source(src)
            self.assertTrue(manifest.files)
            for f in manifest.files:
                self.assertTrue(f.sha256)
                self.assertTrue(f.path)


class BookResearchTests(unittest.TestCase):
    def test_level_a_builds_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_a_source(Path(tmp))
            manifest = ingest_source(src)
            research = build_research(manifest, book_title="呼啸山庄", author="艾米莉·勃朗特")
            self.assertIsInstance(research, BookResearch)
            self.assertIsInstance(research.chapter_notes, list)
            self.assertIsInstance(research.character_map, list)
            self.assertIsInstance(research.event_candidates, list)
            self.assertIsInstance(research.fact_ledger, FactLedger)

    def test_level_c_raises_insufficient_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_c_source(Path(tmp))
            manifest = ingest_source(src)
            with self.assertRaises(ResearchInsufficientError):
                build_research(manifest, book_title="某书", author="某作者")

    def test_event_candidates_count_in_20_to_40(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = _level_a_source(Path(tmp))
            manifest = ingest_source(src)
            research = build_research(manifest, book_title="呼啸山庄", author="艾米莉·勃朗特")
            self.assertTrue(20 <= len(research.event_candidates) <= 40,
                            f"event_candidates={len(research.event_candidates)} 不在 20-40")


class FactLedgerTests(unittest.TestCase):
    def test_fact_types_present(self) -> None:
        ledger = FactLedger(entries=[
            {"id": "F001", "type": "F", "claim": "希斯克利夫被恩肖家收养", "source_ref": "ch4", "verified": True},
            {"id": "Q001", "type": "Q", "claim": "不管我们的灵魂是什么做的，他和我的是一样的", "source_ref": "ch9", "verified": True},
            {"id": "A001", "type": "A", "claim": "合并两章的复仇推进", "source_ref": "ch17-19", "verified": True},
            {"id": "I001", "type": "I", "claim": "主播解释拒绝选择", "source_ref": None, "verified": True},
            {"id": "E001", "type": "E", "claim": "代际创伤的外部心理学视角", "source_ref": "external", "verified": True},
        ])
        validate_fact_ledger(ledger)
        types = {e["type"] for e in ledger.entries}
        self.assertTrue({"F", "Q", "A", "I", "E"}.issubset(types))

    def test_invalid_type_rejected(self) -> None:
        ledger = FactLedger(entries=[
            {"id": "X001", "type": "X", "claim": "坏类型", "source_ref": None, "verified": True},
        ])
        with self.assertRaisesRegex(ValueError, "type|F.*Q.*A.*I.*E"):
            validate_fact_ledger(ledger)

    def test_major_facts_must_be_traceable(self) -> None:
        # F 类（原著事实）必须有 source_ref
        ledger = FactLedger(entries=[
            {"id": "F001", "type": "F", "claim": "凯瑟琳死亡", "source_ref": None, "verified": True},
        ])
        with self.assertRaisesRegex(ValueError, "source_ref|追溯|trace"):
            validate_fact_ledger(ledger)


if __name__ == "__main__":
    unittest.main()
