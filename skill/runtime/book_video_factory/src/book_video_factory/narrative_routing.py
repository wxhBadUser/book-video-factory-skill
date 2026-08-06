"""三路线竞争：每本书生成 3 条完整路线，按 10 项评分，选 1。

不得生成一条后再伪造另外两条。路线缺少至少 8 个有效事件证据不得成为最终选择。
click_question 与 deep_thesis 必须分离。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .book_research import BookResearch
from .narrator_essay_contracts import validate_creative_decision

MIN_EVIDENCE_FOR_SELECTION = 8

SCORE_KEYS = (
    "audience_resonance", "textual_evidence", "emotional_range",
    "midpoint_power", "reinterpretation_power", "modern_mapping",
    "visual_potential", "originality", "character_complexity",
    "ending_specificity",
)


class RoutingError(ValueError):
    pass


@dataclass
class Route:
    route_id: str
    click_question: str
    deep_thesis: str
    narrative_engine: str
    fate_evidence_ids: list[str]
    midpoint_question: str
    reinterpretation_evidence: list[str]
    narrator_persona: str
    main_concept: str
    emotion_curve: list[dict[str, Any]]
    visual_potential: int
    ending_image: str
    recommended_duration: int
    scores: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "click_question": self.click_question,
            "deep_thesis": self.deep_thesis,
            "narrative_engine": self.narrative_engine,
            "fate_evidence_ids": self.fate_evidence_ids,
            "midpoint_question": self.midpoint_question,
            "reinterpretation_evidence_ids": self.reinterpretation_evidence,
            "narrator_persona": self.narrator_persona,
            "main_concept": self.main_concept,
            "target_emotional_curve": self.emotion_curve,
            "visual_potential": self.visual_potential,
            "ending_image": self.ending_image,
            "recommended_duration_minutes": self.recommended_duration,
            "scores": self.scores,
        }


@dataclass
class CreativeDecision:
    book_id: str
    candidate_routes: list[Route]
    selected_route_id: str
    rejected_route_reasons: list[str] = field(default_factory=list)
    recommended_duration_minutes: int = 16

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "creative-decision.v1",
            "book_id": self.book_id,
            "candidate_routes": [r.to_dict() for r in self.candidate_routes],
            "selected_route_id": self.selected_route_id,
            "rejected_route_reasons": self.rejected_route_reasons,
            "recommended_duration_minutes": self.recommended_duration_minutes,
        }


def generate_routes(
    research: BookResearch, *, routes: list[Route] | None = None
) -> list[Route]:
    """Return exactly three agent-authored routes; never synthesize fallback routes."""
    if routes is not None:
        if len(routes) != 3:
            raise RoutingError(f"must generate exactly 3 routes, got {len(routes)}")
        for r in routes:
            if r.click_question == r.deep_thesis:
                raise RoutingError(
                    f"route {r.route_id}: click_question and deep_thesis must differ"
                )
        return list(routes)
    raise RoutingError("formal routing requires exactly three agent-authored routes")



def score_routes(routes: list[Route]) -> list[Route]:
    """Validate the ten agent-authored scores for every route."""
    expected = set(SCORE_KEYS)
    for route in routes:
        if set(route.scores) != expected:
            raise RoutingError(
                f"route {route.route_id} requires ten agent-authored scores"
            )
        for key, value in route.scores.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1 <= value <= 5:
                raise RoutingError(
                    f"route {route.route_id}.scores.{key} must be 1..5"
                )
    return routes


def select_route(scored: list[Route]) -> Route:
    """选择得分最高且 fate_evidence_ids>=8 的路线。"""
    eligible = [r for r in scored if len(r.fate_evidence_ids) >= MIN_EVIDENCE_FOR_SELECTION]
    if not eligible:
        raise RoutingError(
            f"no route has >= {MIN_EVIDENCE_FOR_SELECTION} evidence; cannot select"
        )
    return max(eligible, key=lambda r: sum(r.scores.values()))


def build_creative_decision(
    research: BookResearch,
    scored: list[Route],
    selected: Route,
) -> CreativeDecision:
    """组装 creative-decision.v1 合同并校验。"""
    rejected = [
        f"{r.route_id}: lower score or insufficient evidence"
        for r in scored if r.route_id != selected.route_id
    ]
    cd = CreativeDecision(
        book_id=f"{research.book_title}",
        candidate_routes=scored,
        selected_route_id=selected.route_id,
        rejected_route_reasons=rejected,
        recommended_duration_minutes=selected.recommended_duration,
    )
    validate_creative_decision(cd.to_dict())
    return cd
