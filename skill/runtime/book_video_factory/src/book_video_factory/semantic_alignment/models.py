"""Frozen data models for the semantic alignment contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

PROPOSITION_MODES = ("Literal", "Symbolic", "Abstract")
BRIDGE_MODES = ("direct", "symbolic")


@dataclass(frozen=True)
class EntityVisibility:
    """One entity the image must (or may) show."""

    entity_id: str
    must_be_visible: bool
    natural_language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "must_be_visible": self.must_be_visible,
            "natural_language": self.natural_language,
        }


@dataclass(frozen=True)
class VisualProposition:
    """What one image is being asked to do for one caption group.

    ``mode`` follows the Literal / Symbolic / Abstract taxonomy:

    Literal
        The caption names a drawable person, object, place or action; the image
        must show those referents.
    Symbolic
        The caption is abstract but a concrete surrogate is available; the image
        must evoke the surrogate and the rationale must name both sides.
    Abstract
        Neither a literal nor a symbolic referent is reasonable; the image is
        atmosphere only and must not claim to depict narrative referents.
    """

    mode: str
    subject: str
    action: str
    environment: str
    mood: str
    lighting: str
    palette: str
    rationale_text: str
    entity_visibility: tuple[EntityVisibility, ...] = ()
    surrogate_objects: tuple[str, ...] = ()
    source_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "subject": self.subject,
            "action": self.action,
            "environment": self.environment,
            "mood": self.mood,
            "lighting": self.lighting,
            "palette": self.palette,
            "rationale_text": self.rationale_text,
            "entity_visibility": [item.to_dict() for item in self.entity_visibility],
            "surrogate_objects": list(self.surrogate_objects),
            "source_terms": list(self.source_terms),
        }

    def content_sha256(self) -> str:
        """Stable hash of the proposition, used to detect stale prompts."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "VisualProposition":
        visibility = tuple(
            EntityVisibility(
                entity_id=str(item.get("entity_id", "")),
                must_be_visible=bool(item.get("must_be_visible", False)),
                natural_language=str(item.get("natural_language", "")),
            )
            for item in raw.get("entity_visibility", [])
            if isinstance(item, Mapping)
        )
        return cls(
            mode=str(raw.get("mode", "")),
            subject=str(raw.get("subject", "")),
            action=str(raw.get("action", "")),
            environment=str(raw.get("environment", "")),
            mood=str(raw.get("mood", "")),
            lighting=str(raw.get("lighting", "")),
            palette=str(raw.get("palette", "")),
            rationale_text=str(raw.get("rationale_text", "")),
            entity_visibility=visibility,
            surrogate_objects=tuple(str(item) for item in raw.get("surrogate_objects", [])),
            source_terms=tuple(str(item) for item in raw.get("source_terms", [])),
        )


@dataclass(frozen=True)
class SemanticBridge:
    """Why a given shot is allowed to illustrate a given narration span.

    ``direct``
        The shot's ``required_entities`` intersect the source beats'
        ``requiredEntities``. The overlap itself is the justification.
    ``symbolic``
        There is no overlap, so the written rationale is *load bearing*: it must
        name the source-side referent and the image-side surrogate explicitly.
    """

    shot_id: str
    mode: str
    shared_entities: tuple[str, ...] = ()
    subject: str = ""
    source_terms_named: tuple[str, ...] = ()
    image_terms_named: tuple[str, ...] = ()
    rationale: str = ""
    rationale_is_boilerplate: bool = False
    event_alignment: str = ""
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "semantic_bridge_mode": self.mode,
            "shared_required_entities": list(self.shared_entities),
            "source_terms_named": list(self.source_terms_named),
            "image_terms_named": list(self.image_terms_named),
            "rationale_is_boilerplate": self.rationale_is_boilerplate,
        }
