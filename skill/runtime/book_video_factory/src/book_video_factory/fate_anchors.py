"""命运锚点：20-40 候选事件 → 8-12 锚点。

每个锚点至少承担两项功能（changes_fate/reveals_character/proves_thesis/strong_visual/
key_dialogue/launches_question/supports_payoff）。未选事件必须标记
expand/compress/merge/delete/analysis_only，不得无理由丢失重要结局。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .narrator_essay_contracts import validate_fate_anchors

ANCHOR_FUNCS = (
    "changes_fate", "reveals_character", "proves_thesis",
    "strong_visual", "key_dialogue", "launches_question", "supports_payoff",
)
VALID_TREATMENTS = ("expand", "compress", "merge", "delete", "analysis_only")


@dataclass
class FateAnchors:
    book_id: str
    candidate_event_count: int
    anchors: list[dict[str, Any]] = field(default_factory=list)
    omission_map: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "fate-anchors.v1",
            "book_id": self.book_id,
            "candidate_event_count": self.candidate_event_count,
            "anchors": self.anchors,
            "omission_map": self.omission_map,
        }


def _function_score(cand: dict[str, Any]) -> int:
    return sum(1 for f in ANCHOR_FUNCS if cand.get(f))


def select_anchors(
    event_candidates: list[dict[str, Any]],
    *,
    book_id: str = "",
    min_anchors: int = 8,
    max_anchors: int = 12,
    anchors: list[dict[str, Any]] | None = None,
    omission_map: list[dict[str, Any]] | None = None,
) -> FateAnchors:
    """筛选 8-12 命运锚点。预构建 anchors 优先；否则按功能分排序选 top-N。"""
    n = len(event_candidates)
    if anchors is None:
        raise ValueError("formal anchor selection requires agent-authored anchors")
    if omission_map is None:
        raise ValueError("formal anchor selection requires an agent-authored omission_map")
    fa = FateAnchors(
        book_id=book_id, candidate_event_count=n,
        anchors=list(anchors), omission_map=list(omission_map),
    )
    validate_fate_anchors(fa.to_dict())
    return fa
