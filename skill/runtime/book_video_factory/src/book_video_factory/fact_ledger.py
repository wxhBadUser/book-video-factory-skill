"""F/Q/A/I/E 事实台账。

F = 原著明确事实
Q = 已核验短引语
A = 口播合并、压缩或顺序调整
I = 主播解释
E = 外部文学/历史/心理资料

重大情节、人物关系、金额、死亡、婚姻、遗产、时间顺序和关键引语必须可追溯。
F/Q 类必须有 source_ref。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class FactType(str, enum.Enum):
    F = "F"
    Q = "Q"
    A = "A"
    I = "I"
    E = "E"


VALID_TYPES = {t.value for t in FactType}
# F/Q 必须可追溯到原著位置。
TRACEABLE_TYPES = {"F", "Q"}


@dataclass
class FactLedger:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "fact-ledger.v1", "entries": self.entries}

    def by_type(self, t: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e.get("type") == t]


def validate_fact_ledger(ledger: FactLedger) -> None:
    """校验台账：类型合法、F/Q 可追溯、每条有 id+claim。"""
    seen_ids: set[str] = set()
    for i, e in enumerate(ledger.entries):
        if not isinstance(e, dict):
            raise ValueError(f"entry[{i}] must be an object")
        eid = e.get("id")
        if not eid:
            raise ValueError(f"entry[{i}].id required")
        if eid in seen_ids:
            raise ValueError(f"duplicate fact id: {eid}")
        seen_ids.add(eid)
        t = e.get("type")
        if t not in VALID_TYPES:
            raise ValueError(f"entry[{i}].type '{t}' not in F/Q/A/I/E")
        if not e.get("claim"):
            raise ValueError(f"entry[{i}].claim required")
        if t in TRACEABLE_TYPES and not e.get("source_ref"):
            raise ValueError(
                f"entry[{i}] type {t} must have source_ref (重大事实/引语必须可追溯)"
            )
