"""Single source of truth for narrative function taxonomy.

Phase 1 refactoring: narrative functions were redefined in at least four
places (narrator_essay_contracts, caption_grouping, performance, classifier),
with inconsistent membership. This module is the ONLY authoritative registry.
All production code MUST import from here; any module that defines its own
list is a contract violation.

Principle: LLM interprets literature; Python validates contracts. The
narrative function is a semantic label assigned by the Semantic Director
Agent, then validated against this closed set.
"""

from __future__ import annotations

from typing import Iterable

# Canonical narrative functions used across the entire production pipeline.
# These are coarse-grained scene-level labels; fine-grained beat-level
# distinctions (hook, desire, cost, etc.) belong in the script/research
# layer, not in the visual-semantic layer.
NARRATIVE_FUNCTIONS: tuple[str, ...] = (
    "opening",
    "plot",
    "theory",
    "author_background",
    "transition",
    "closing",
)

NARRATIVE_FUNCTION_SET: frozenset[str] = frozenset(NARRATIVE_FUNCTIONS)

# Subset of functions that allow abstract / non-literal visual rendering.
# plot requires literal scene depiction; opening, theory, author_background,
# transition, and closing may use symbolic or atmospheric frames.
ABSTRACT_ALLOWED_FUNCTIONS: frozenset[str] = frozenset({
    "opening",
    "theory",
    "author_background",
    "transition",
    "closing",
})


def is_valid_narrative_function(value: object) -> bool:
    """Return True if value is a string in the canonical registry."""
    return isinstance(value, str) and value in NARRATIVE_FUNCTION_SET


def validate_narrative_function(value: object, *, label: str = "narrative_function") -> str:
    """Validate and return a narrative function string.

    Raises ValueError with a clear message if the value is not in the registry.
    """
    if not is_valid_narrative_function(value):
        raise ValueError(
            f"{label}={value!r} is not a valid narrative function; "
            f"expected one of {NARRATIVE_FUNCTIONS}"
        )
    return str(value)


def filter_valid_functions(values: Iterable[object]) -> list[str]:
    """Return only the values that are valid narrative functions, in order."""
    return [str(v) for v in values if is_valid_narrative_function(v)]


__all__ = [
    "NARRATIVE_FUNCTIONS",
    "NARRATIVE_FUNCTION_SET",
    "ABSTRACT_ALLOWED_FUNCTIONS",
    "is_valid_narrative_function",
    "validate_narrative_function",
    "filter_valid_functions",
]
