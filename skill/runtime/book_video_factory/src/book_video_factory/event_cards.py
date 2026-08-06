"""事件卡：每个命运锚点生成一张七要素事件卡。

scene_location / sensory_object / character_action / key_dialogue /
narrator_reaction / hammer_line / next_question / source_ids / emotion / visual_function。
真实内容由生成智能体注入；本模块负责组装与校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fact_ledger import FactLedger
from .narrator_essay_contracts import validate_event_cards


@dataclass
class EventCards:
    cards: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "event-cards.v1", "cards": self.cards}


def build_event_cards(
    fate_anchors,
    fact_ledger: FactLedger,
    *,
    cards: list[dict[str, Any]] | None = None,
) -> EventCards:
    """从锚点生成事件卡。预构建 cards 优先；否则脚手架（真实内容 Phase 6 注入）。"""
    if cards is None:
        raise ValueError("formal event cards require agent-authored cards")
    ec = EventCards(cards=list(cards))
    validate_event_cards(ec.to_dict())
    return ec
