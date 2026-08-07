"""Semantic alignment contracts between narration captions and generated images.

This package exists because the pipeline previously accepted *assertions* of
semantic alignment ("字幕与画面共享当前场景") in place of *evidence* of it. Every
public helper here is pure, deterministic and side-effect free so the gates it
backs can be replayed from the manifests alone.

Modules:

``models``
    Frozen dataclasses for the visual proposition and semantic bridge records.
``validation``
    Fail-closed validators for rationale substance and entity grounding.
``caption_grouping``
    Rules deciding when several captions may share one image.
``scoring``
    Deterministic alignment scoring used by review reports.
"""

from __future__ import annotations

from .models import (
    PROPOSITION_MODES,
    SemanticBridge,
    VisualProposition,
    EntityVisibility,
)
from .validation import (
    SemanticContractError,
    evaluate_semantic_bridge,
    is_boilerplate_rationale,
    normalize_rationale,
    validate_visual_proposition,
)

__all__ = [
    "PROPOSITION_MODES",
    "EntityVisibility",
    "SemanticBridge",
    "SemanticContractError",
    "VisualProposition",
    "evaluate_semantic_bridge",
    "is_boilerplate_rationale",
    "normalize_rationale",
    "validate_visual_proposition",
]
