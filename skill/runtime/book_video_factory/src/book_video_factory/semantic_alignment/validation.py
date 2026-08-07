"""Fail-closed validators for narration/image semantic alignment.

The rule this module enforces is deliberately narrow and mechanical:

* When a shot's ``required_entities`` intersect the entities its source beats
  declare, the overlap *is* the evidence. The written rationale is then only
  documentation and boilerplate is recorded, not fatal.
* When there is no overlap, the written rationale becomes the only evidence.
  A load-bearing rationale must be substantive and must name **both** the
  source-side referent (what the narration says) and the image-side surrogate
  (what will actually be drawn). Anything else is an assertion, not a bridge.

This is what closes root cause B: before this module, any nonempty string in
``semantic_rationale`` disabled entity validation entirely.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .models import BRIDGE_MODES, PROPOSITION_MODES, SemanticBridge, VisualProposition


class SemanticContractError(RuntimeError):
    """A shot claims semantic alignment it has not demonstrated."""


# Minimum number of information-bearing characters a load-bearing rationale
# must carry once punctuation and whitespace are removed.
MIN_LOAD_BEARING_CHARS = 16

# Minimum length of a term that may count as "named" inside a rationale.
MIN_TERM_LENGTH = 2

_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^字幕与(?:画面|镜头|图像)",
        r"^(?:画面|镜头|图像)与字幕",
        r"^(?:画面|镜头|图像)(?:忠实)?(?:呼应|对应|配合|匹配|贴合|反映|承接)(?:当前)?(?:旁白|字幕|台词|叙述|场景)",
        r"^与(?:旁白|字幕|台词|叙述)(?:语义|情绪|氛围|内容)?(?:一致|相符|匹配|贴合|统一)",
        r"^(?:语义|情绪|氛围|基调)(?:一致|统一|贴合|相符)",
        r"^(?:同上|同前|如上|见上)",
        r"illustrate\s+the\s+exact\s+current\s+narration\s+beat",
        r"^(?:same|as)\s+(?:above|previous|before)",
        r"^matches?\s+the\s+(?:caption|narration|voice[- ]?over)",
        r"^visually\s+consistent\s+with\s+the\s+(?:caption|narration)",
    )
)

_PUNCTUATION_CATEGORIES = {"Pc", "Pd", "Pe", "Pf", "Pi", "Po", "Ps", "Zs", "Cc"}


def normalize_rationale(text: str) -> str:
    """Strip punctuation and whitespace so length reflects information, not commas."""
    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        char
        for char in normalized
        if unicodedata.category(char) not in _PUNCTUATION_CATEGORIES
    )


def is_boilerplate_rationale(text: str) -> bool:
    """True when the rationale is a template that asserts alignment without showing it."""
    if not isinstance(text, str):
        return True
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _BOILERPLATE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def _named_terms(rationale: str, terms: Iterable[str]) -> tuple[str, ...]:
    """Terms from ``terms`` that literally appear inside ``rationale``."""
    haystack = unicodedata.normalize("NFKC", rationale)
    found: list[str] = []
    for term in terms:
        if not isinstance(term, str):
            continue
        candidate = unicodedata.normalize("NFKC", term.strip())
        if len(candidate) < MIN_TERM_LENGTH:
            continue
        if candidate in haystack and candidate not in found:
            found.append(candidate)
    return tuple(sorted(found))


def evaluate_semantic_bridge(
    *,
    shot_id: str,
    required_entities: Sequence[str],
    source_entities: Iterable[str],
    rationale: str,
    caption_texts: Sequence[str] = (),
) -> SemanticBridge:
    """Decide whether ``shot_id`` may illustrate its narration span.

    Raises ``SemanticContractError`` when the shot has neither an entity overlap
    nor a rationale that grounds both sides of the bridge.
    """
    source_set = {str(item).strip() for item in source_entities if str(item).strip()}
    image_set = [str(item).strip() for item in required_entities if str(item).strip()]
    shared = tuple(sorted(set(image_set) & source_set))
    boilerplate = is_boilerplate_rationale(rationale)

    if shared:
        return SemanticBridge(
            shot_id=shot_id,
            mode="direct",
            shared_entities=shared,
            source_terms_named=_named_terms(rationale, sorted(source_set)),
            image_terms_named=_named_terms(rationale, image_set),
            rationale=rationale,
            rationale_is_boilerplate=boilerplate,
        )

    # No overlap: the rationale is now the only evidence and must carry weight.
    if not isinstance(rationale, str) or not rationale.strip():
        raise SemanticContractError(
            f"shot {shot_id} shares no entity with its source beats and has no semantic rationale"
        )
    if boilerplate:
        raise SemanticContractError(
            f"shot {shot_id} shares no entity with its source beats and its semantic rationale "
            f"is template boilerplate that asserts alignment instead of demonstrating it: {rationale!r}"
        )
    informative = normalize_rationale(rationale)
    if len(informative) < MIN_LOAD_BEARING_CHARS:
        raise SemanticContractError(
            f"shot {shot_id} shares no entity with its source beats and its semantic rationale "
            f"carries only {len(informative)} information characters "
            f"(minimum {MIN_LOAD_BEARING_CHARS})"
        )

    source_vocabulary = sorted(source_set | {str(text).strip() for text in caption_texts if str(text).strip()})
    source_named = _named_terms(rationale, source_vocabulary)
    image_named = _named_terms(rationale, image_set)
    if not source_named:
        raise SemanticContractError(
            f"shot {shot_id} has no entity overlap, so its semantic rationale must name the "
            f"source-side referent; expected one of {sorted(source_set)}"
        )
    if not image_named:
        raise SemanticContractError(
            f"shot {shot_id} has no entity overlap, so its semantic rationale must name the "
            f"image-side surrogate it will actually draw; expected one of {sorted(image_set)}"
        )
    return SemanticBridge(
        shot_id=shot_id,
        mode="symbolic",
        shared_entities=(),
        source_terms_named=source_named,
        image_terms_named=image_named,
        rationale=rationale,
        rationale_is_boilerplate=False,
    )


def validate_visual_proposition(
    proposition: VisualProposition | Mapping[str, Any],
    *,
    shot_id: str,
    caption_texts: Sequence[str] = (),
    source_entities: Iterable[str] = (),
) -> VisualProposition:
    """Validate a per-shot visual proposition against its declared mode."""
    resolved = (
        proposition
        if isinstance(proposition, VisualProposition)
        else VisualProposition.from_mapping(proposition)
    )
    if resolved.mode not in PROPOSITION_MODES:
        raise SemanticContractError(
            f"shot {shot_id} proposition mode {resolved.mode!r} is not one of {list(PROPOSITION_MODES)}"
        )
    if not resolved.rationale_text.strip():
        raise SemanticContractError(f"shot {shot_id} proposition rationale_text is empty")
    if is_boilerplate_rationale(resolved.rationale_text):
        raise SemanticContractError(
            f"shot {shot_id} proposition rationale_text is template boilerplate: "
            f"{resolved.rationale_text!r}"
        )
    joined_caption = "".join(str(text) for text in caption_texts)
    if resolved.rationale_text.strip() and joined_caption.strip():
        if resolved.rationale_text.strip() == joined_caption.strip():
            raise SemanticContractError(
                f"shot {shot_id} proposition rationale_text merely repeats the caption cue"
            )

    if resolved.mode == "Literal":
        if not resolved.subject.strip():
            raise SemanticContractError(f"shot {shot_id} Literal proposition requires a subject")
        if not resolved.action.strip():
            raise SemanticContractError(f"shot {shot_id} Literal proposition requires an action")
        visible = [item for item in resolved.entity_visibility if item.must_be_visible]
        if not visible:
            raise SemanticContractError(
                f"shot {shot_id} Literal proposition requires at least one entity marked must_be_visible"
            )
        for item in visible:
            if not item.natural_language.strip():
                raise SemanticContractError(
                    f"shot {shot_id} Literal proposition entity {item.entity_id!r} has no "
                    f"natural-language description for the image model"
                )
        source_set = {str(item).strip() for item in source_entities if str(item).strip()}
        if source_set:
            declared = {item.entity_id.strip() for item in visible}
            declared |= {item.natural_language.strip() for item in visible}
            grounded = any(
                any(term and term in candidate for candidate in declared)
                for term in source_set
            ) or bool(declared & source_set)
            if not grounded:
                raise SemanticContractError(
                    f"shot {shot_id} Literal proposition declares visible entities {sorted(declared)} "
                    f"that do not include any source-side referent {sorted(source_set)}"
                )
    elif resolved.mode == "Symbolic":
        if not resolved.surrogate_objects:
            raise SemanticContractError(
                f"shot {shot_id} Symbolic proposition requires at least one surrogate object"
            )
        if not resolved.source_terms:
            raise SemanticContractError(
                f"shot {shot_id} Symbolic proposition requires the source-side terms it stands in for"
            )
        named_source = _named_terms(resolved.rationale_text, resolved.source_terms)
        named_surrogate = _named_terms(resolved.rationale_text, resolved.surrogate_objects)
        if not named_source or not named_surrogate:
            raise SemanticContractError(
                f"shot {shot_id} Symbolic proposition rationale must name both the source term and "
                f"the surrogate it replaces"
            )
    else:  # Abstract
        if any(item.must_be_visible for item in resolved.entity_visibility):
            raise SemanticContractError(
                f"shot {shot_id} Abstract proposition cannot require any entity to be visible"
            )
        if not resolved.mood.strip() and not resolved.palette.strip():
            raise SemanticContractError(
                f"shot {shot_id} Abstract proposition requires a mood or palette to be renderable"
            )
    return resolved


__all__ = [
    "BRIDGE_MODES",
    "MIN_LOAD_BEARING_CHARS",
    "SemanticContractError",
    "evaluate_semantic_bridge",
    "is_boilerplate_rationale",
    "normalize_rationale",
    "validate_visual_proposition",
]
