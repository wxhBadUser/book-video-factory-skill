"""Phase 2 失败测试：统一 schema 运行时校验加载器。

TDD：引用尚未实现的 `book_video_factory.schema_validation`，运行应失败。
实现后须通过。

目的：终结"schema 文件存在但无代码加载"的状态。所有保留 schema 必须被
运行时代码消费：加载器在 narrator-essay 流程启动时校验合同实例。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.schema_validation import (  # noqa: E402
    SchemaLoadError,
    SupportedSchemas,
    load_schema,
    validate_instance,
)

SCHEMAS = ROOT.parent / "docs" / "schemas" / "narrator_essay"


class SchemaLoaderTests(unittest.TestCase):
    def test_supported_schemas_lists_all_narrator_contracts(self) -> None:
        ids = SupportedSchemas.narrator_essay_ids()
        expected = {
            "creative-decision.v1",
            "fate-anchors.v1",
            "event-cards.v1",
            "script-beats.v1",
            "visual-bible.v1",
            "voice-direction.narrator.v1",
            "content-quality-report.v1",
        }
        self.assertTrue(expected.issubset(set(ids)), f"missing: {expected - set(ids)}")

    def test_load_schema_returns_parsed_json(self) -> None:
        schema = load_schema(SCHEMAS / "creative-decision.v1.example.json")
        self.assertEqual(schema["schema_version"], "creative-decision.v1")

    def test_load_schema_rejects_missing_file(self) -> None:
        with self.assertRaises(SchemaLoadError):
            load_schema(SCHEMAS / "does-not-exist.json")

    def test_validate_instance_accepts_valid_example(self) -> None:
        instance = json.loads(
            (SCHEMAS / "event-cards.v1.example.json").read_text(encoding="utf-8")
        )
        # 不抛即通过
        validate_instance("event-cards.v1", instance)

    def test_validate_instance_rejects_wrong_schema_version(self) -> None:
        instance = json.loads(
            (SCHEMAS / "event-cards.v1.example.json").read_text(encoding="utf-8")
        )
        instance["schema_version"] = "wrong.v1"
        with self.assertRaises(SchemaLoadError):
            validate_instance("event-cards.v1", instance)

    def test_all_example_schemas_round_trip(self) -> None:
        """docs/schemas/narrator_essay 下每个 example.json 都能被加载且自洽。"""
        for path in sorted(SCHEMAS.glob("*.example.json")):
            instance = json.loads(path.read_text(encoding="utf-8"))
            sv = instance["schema_version"]
            validate_instance(sv, instance)


if __name__ == "__main__":
    unittest.main()
