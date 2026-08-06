"""统一 schema 运行时校验加载器。

终结"schema 文件存在但无代码加载"的状态。所有 narrator-essay 合同实例进入
运行时前必须经此加载器校验 schema_version 与基本结构；深度业务规则由
`narrator_essay_contracts` 的领域校验器负责。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# narrator-essay 正式合同注册表（schema_id → 顶层必含字段）。
NARRATOR_ESSAY_SCHEMAS: dict[str, tuple[str, ...]] = {
    "creative-decision.v1": ("schema_version", "book_id", "candidate_routes", "selected_route_id"),
    "fate-anchors.v1": ("schema_version", "book_id", "candidate_event_count", "anchors"),
    "event-cards.v1": ("schema_version", "cards"),
    "script-beats.v1": ("schema_version", "beats"),
    "visual-bible.v1": ("schema_version", "book_id", "core_metaphor"),
    "voice-direction.narrator.v1": ("schema_version", "provider", "brand_voice_id", "chunks"),
    "content-quality-report.v1": ("schema_version", "overall_score", "checks"),
    "script.narrator-essay.v1": ("schema_version", "book_id", "release_id"),
    "book-research.v1": ("schema_version", "book_id", "source_manifest"),
}


class SchemaLoadError(ValueError):
    """schema 加载或基本结构校验失败。"""


class SupportedSchemas:
    """已注册的 narrator-essay 合同清单。"""

    @staticmethod
    def narrator_essay_ids() -> list[str]:
        return list(NARRATOR_ESSAY_SCHEMAS)


def load_schema(path: Path) -> dict[str, Any]:
    """读取并解析 JSON schema/实例文件。"""
    p = Path(path)
    if not p.is_file():
        raise SchemaLoadError(f"schema file missing: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SchemaLoadError(f"invalid JSON in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaLoadError(f"schema root must be an object: {p}")
    return data


def validate_instance(schema_id: str, instance: Any) -> None:
    """校验实例的 schema_version 与基本结构匹配注册表。

    深度业务规则（评分区间、比例、外键等）由 narrator_essay_contracts 负责。
    """
    if schema_id not in NARRATOR_ESSAY_SCHEMAS:
        raise SchemaLoadError(f"unsupported schema_id: {schema_id}")
    if not isinstance(instance, dict):
        raise SchemaLoadError(f"instance for {schema_id} must be an object")
    actual = instance.get("schema_version")
    if actual != schema_id:
        raise SchemaLoadError(
            f"schema_version mismatch: expected {schema_id!r}, got {actual!r}"
        )
    required = NARRATOR_ESSAY_SCHEMAS[schema_id]
    missing = [k for k in required if k not in instance]
    if missing:
        raise SchemaLoadError(
            f"{schema_id} missing required fields: {missing}"
        )
