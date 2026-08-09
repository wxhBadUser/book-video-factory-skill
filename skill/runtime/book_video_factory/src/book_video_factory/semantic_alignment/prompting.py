"""Caption-first prompt assembly and prompt/caption/proposition binding.

Priority contract
-----------------
The image model reads the top of the prompt hardest. The old prompt led with
art direction, so the model produced beautiful frames that ignored the line on
screen. The block order is now fixed and numbered so it is visible both to the
model and to a human reviewer:

======  ==========================  ====================================
Slot    Block                       Why it is where it is
======  ==========================  ====================================
1       caption                     The line the viewer reads right now.
2       proposition                 The factual claim the frame asserts.
3       required_scene              The required situation, action and register.
4       required_visible            The referents that must be on screen.
5       anchors                     Character continuity references.
6       forbidden                   The referents that must not be.
7       camera                      Framing, lens, depth and subtitle-safe zone.
8       style                       Palette, texture, visual world.
9       symbolic_explanation        Only for Symbolic mode, and last.
======  ==========================  ====================================

Binding contract
----------------
A prompt is only valid for the exact caption text, the exact proposition and
the exact prompt body it was generated from. ``compute_prompt_binding`` records
six identifiers/hashes and ``verify_prompt_binding`` fails closed the moment any
of them drifts, so an edited caption can never be served by a stale image.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

from book_video_factory.semantic_alignment.caption_grouping import NARRATIVE_FUNCTIONS
from book_video_factory.semantic_alignment.models import VisualProposition

PROMPT_BLOCK_ORDER: tuple[str, ...] = (
    "caption",
    "proposition",
    "required_scene",
    "required_visible",
    "anchors",
    "forbidden",
    "camera",
    "style",
    "symbolic_explanation",
)

NARRATIVE_FUNCTION_GUIDANCE: dict[str, str] = {
    "opening": "establishing register; set place and mood without asserting plot events that have not happened yet",
    "plot": "narrative register; depict the concrete event the caption describes",
    "theory": "reflective register; do not stage a new plot event, illustrate the idea through setting or object",
    "author_background": "documentary register about the author, not the story; never depict story characters",
    "transition": "connective register; keep the frame quiet and non-committal",
    "closing": "valedictory register; withdraw from the action, favour distance and emptiness",
}

_BINDING_FIELDS: tuple[str, ...] = (
    "shot_id",
    "scene_id",
    "beat_ids",
    "caption_ids",
    "caption_text_sha256",
    "proposition_sha256",
    "prompt_sha256",
)
_GROUP_BINDING_FIELDS: tuple[str, ...] = (
    "group_id",
    "caption_contract_bindings",
    "caption_group_sha256",
)


class PromptSpecError(RuntimeError):
    """The prompt specification is internally inconsistent and must not be sent."""


class PromptBindingError(RuntimeError):
    """A prompt binding no longer matches its caption, proposition or prompt."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _camera_text(camera: Mapping[str, Any]) -> str:
    keys = ("shot_size", "lens", "camera_angle", "composition", "depth")
    missing = [key for key in keys if not str(camera.get(key, "")).strip()]
    if missing:
        raise PromptSpecError(f"camera block is missing: {', '.join(missing)}")
    return (
        f"{camera['shot_size']}, {camera['lens']} lens, {camera['camera_angle']}; "
        f"composition: {camera['composition']}; depth: {camera['depth']}"
    )


def _style_text(style: Mapping[str, Any]) -> str:
    keys = ("visual_world", "palette", "texture")
    missing = [key for key in keys if not str(style.get(key, "")).strip()]
    if missing:
        raise PromptSpecError(f"style block is missing: {', '.join(missing)}")
    return (
        f"visual world {style['visual_world']}; palette {style['palette']}; "
        f"surface texture {style['texture']}"
    )


def build_aligned_prompt_blocks(
    *,
    caption_text: str,
    proposition: VisualProposition,
    narrative_function: str,
    camera: Mapping[str, Any],
    style: Mapping[str, Any],
    forbidden_entities: Sequence[str] | Iterable[str] = (),
    anchors: Sequence[str] | Iterable[str] = (),
    must_show: Sequence[str] | Iterable[str] = (),
    must_not_show: Sequence[str] | Iterable[str] = (),
) -> tuple[str, ...]:
    """Return the numbered prompt blocks in strict priority order.

    ``must_show`` / ``must_not_show`` come from the authoritative Caption Visual
    Contract: the referents the frame MUST depict (or must NOT depict as the
    primary subject) for the exact caption on screen. They are asserted in
    addition to the proposition-derived visible entities so a boilerplate or
    mismatched proposition cannot silently drop a named character or event.
    """

    caption = caption_text.strip()
    if not caption:
        raise PromptSpecError("caption text is empty; the prompt has nothing to illustrate")
    if narrative_function not in NARRATIVE_FUNCTIONS:
        raise PromptSpecError(
            f"unknown narrative_function {narrative_function!r}; expected one of {NARRATIVE_FUNCTIONS}"
        )
    if proposition.mode not in {"Literal", "Symbolic", "Abstract"}:
        raise PromptSpecError(f"unknown proposition mode {proposition.mode!r}")

    visible = [item for item in proposition.entity_visibility if item.must_be_visible]
    if proposition.mode == "Literal" and not visible:
        raise PromptSpecError(
            "Literal proposition must declare at least one entity that has to be visible"
        )
    if proposition.mode == "Abstract" and visible:
        raise PromptSpecError(
            "Abstract proposition may not require any narrative referent to be visible"
        )
    if proposition.mode == "Symbolic" and not proposition.surrogate_objects:
        raise PromptSpecError("Symbolic proposition must name at least one surrogate object")

    blocks: list[str] = []

    blocks.append(
        f"[1/9 CAPTION] The narration on screen for this frame is: 「{caption}」 "
        "The image exists to illustrate this exact line and nothing else."
    )

    blocks.append(
        f"[2/9 PROPOSITION] Factual claim the frame asserts: {proposition.subject} "
        f"{proposition.action}. Setting: {proposition.environment}. Mood: {proposition.mood}."
    )

    blocks.append(
        f"[3/9 REQUIRED SCENE] Required situation/action: {proposition.action or proposition.subject}. "
        f"Environment: {proposition.environment or 'approved atmospheric setting only'}. "
        f"Narrative function: {narrative_function} — {NARRATIVE_FUNCTION_GUIDANCE[narrative_function]}."
    )

    if proposition.mode == "Abstract":
        required_visible = (
            "This frame claims no narrative referent. Show atmosphere only; do not introduce "
            "identifiable story characters or plot objects."
        )
    else:
        described = "; ".join(
            f"{item.entity_id} ({item.natural_language})" if item.natural_language else item.entity_id
            for item in visible
        ) or "; ".join(proposition.surrogate_objects)
        required_visible = f"Must be clearly recognisable in frame: {described}."
    contract_must_show = [str(item).strip() for item in must_show if str(item).strip()]
    if contract_must_show:
        required_visible += (
            " Per the Caption Visual Contract, this frame MUST show: "
            + ", ".join(contract_must_show)
            + ". If a named character or object above is missing, the frame fails the contract."
        )
    blocks.append(f"[4/9 REQUIRED VISIBLE] {required_visible}")

    anchor_list = [str(item).strip() for item in anchors if str(item).strip()]
    blocks.append(
        "[5/9 IDENTITY REFERENCES] Character continuity identity references and current variants: "
        + ("; ".join(anchor_list) if anchor_list else "no recurring character in this frame")
        + "."
    )

    forbidden = [str(item).strip() for item in forbidden_entities if str(item).strip()]
    contract_must_not = [str(item).strip() for item in must_not_show if str(item).strip()]
    if contract_must_not:
        forbidden = forbidden + contract_must_not
    blocks.append(
        "[6/9 FORBIDDEN] Must not appear or become primary: "
        + (", ".join(forbidden) if forbidden else "no shot-specific prohibitions")
        + ". Never include text, captions, logo, watermark, UI or border."
    )

    blocks.append(
        f"[7/9 CAMERA] {_camera_text(camera)}. Keep the declared subtitle-safe zone visually quiet."
    )

    blocks.append(
        f"[8/9 STYLE] {_style_text(style)}; lighting profile {proposition.lighting}; "
        f"palette key {proposition.palette}."
    )

    if proposition.mode == "Symbolic":
        surrogates = ", ".join(proposition.surrogate_objects)
        sources = ", ".join(proposition.source_terms) or caption
        blocks.append(
            f"[9/9 SYMBOLIC] This frame is a symbolic surrogate: {surrogates} stands in for "
            f"{sources}. Render the surrogate literally and let the meaning stay implicit; "
            "do not draw the abstract idea as a caption, diagram or allegorical figure."
        )

    return tuple(blocks)


def compute_prompt_binding(
    *,
    caption_ids: Sequence[str] | Iterable[str],
    caption_text: str,
    proposition: VisualProposition,
    prompt: str,
    scene_id: str,
    beat_ids: Sequence[str] | Iterable[str],
    shot_id: str,
    caption_visual_contract_sha256: str | None = None,
    group_id: str | None = None,
    caption_contract_bindings: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]] = (),
    caption_group_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind a prompt to the caption, proposition, scene and beats it came from.

    ``caption_visual_contract_sha256`` is the hash of the authoritative Caption
    Visual Contract the caption was illustrated against. It is bound whenever a
    contract is in force (every remediated release) so an edited caption can
    never be served by a stale image that was built against a different
    contract. It is optional only for legacy projects that predate the
    contract; remediated releases always supply it.
    """

    ids = [str(item).strip() for item in caption_ids if str(item).strip()]
    beats = [str(item).strip() for item in beat_ids if str(item).strip()]
    if not ids:
        raise PromptSpecError("prompt binding requires at least one caption id")
    if not beats:
        raise PromptSpecError("prompt binding requires at least one source beat id")
    if not str(scene_id).strip():
        raise PromptSpecError("prompt binding requires a scene id")
    if not str(shot_id).strip():
        raise PromptSpecError("prompt binding requires a shot id")
    if not caption_text.strip():
        raise PromptSpecError("prompt binding requires nonempty caption text")
    if not prompt.strip():
        raise PromptSpecError("prompt binding requires a nonempty prompt")
    binding: dict[str, Any] = {
        "shot_id": str(shot_id),
        "scene_id": str(scene_id),
        "beat_ids": beats,
        "caption_ids": ids,
        "caption_text_sha256": _sha256_text(caption_text),
        "proposition_sha256": proposition.content_sha256(),
        "prompt_sha256": _sha256_text(prompt),
    }
    if caption_visual_contract_sha256 is not None:
        if not isinstance(caption_visual_contract_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", caption_visual_contract_sha256):
            raise PromptSpecError("caption_visual_contract_sha256 must be a 64-char hex digest when supplied")
        binding["caption_visual_contract_sha256"] = caption_visual_contract_sha256
    contract_bindings = [dict(item) for item in caption_contract_bindings]
    group_values = (group_id, caption_group_sha256, bool(contract_bindings))
    if any(value is not None and value is not False for value in group_values) and not all(group_values):
        raise PromptSpecError("group prompt binding requires group_id, contract bindings and group SHA")
    if group_id is not None:
        if not isinstance(group_id, str) or not group_id.strip():
            raise PromptSpecError("group_id must be nonempty")
        if not isinstance(caption_group_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", caption_group_sha256):
            raise PromptSpecError("caption_group_sha256 must be a 64-char hex digest")
        bound_ids = [str(item.get("caption_id", "")).strip() for item in contract_bindings]
        bound_hashes = [str(item.get("caption_visual_contract_sha256", "")) for item in contract_bindings]
        if bound_ids != ids or len(set(bound_ids)) != len(bound_ids):
            raise PromptSpecError("caption contract bindings must exactly match ordered caption ids")
        if any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in bound_hashes):
            raise PromptSpecError("caption contract bindings require 64-char content hashes")
        binding.update({
            "group_id": group_id,
            "caption_contract_bindings": contract_bindings,
            "caption_group_sha256": caption_group_sha256,
        })
    return binding


def verify_prompt_binding(
    binding: Mapping[str, Any],
    *,
    caption_text: str,
    proposition: VisualProposition,
    prompt: str,
    caption_visual_contract_sha256: str | None = None,
    group_id: str | None = None,
    caption_contract_bindings: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    caption_group_sha256: str | None = None,
) -> None:
    """Fail closed when the caption, proposition, prompt, or contract drifted.

    When ``caption_visual_contract_sha256`` is supplied it must match the value
    bound into the prompt binding -- an edited contract (or a frame built
    against a different contract) fails closed. When the binding carries the
    field but no expected value is supplied, the field is left unverified (this
    only happens for legacy bindings predating the contract; remediated
    releases always pass the expected value).
    """

    missing = [field for field in _BINDING_FIELDS if field not in binding]
    if missing:
        raise PromptBindingError(f"prompt binding is missing fields: {', '.join(missing)}")
    expected_caption = _sha256_text(caption_text)
    if binding["caption_text_sha256"] != expected_caption:
        raise PromptBindingError(
            f"caption text changed since the prompt was built "
            f"(binding {binding['caption_text_sha256'][:12]}, actual {expected_caption[:12]})"
        )
    expected_proposition = proposition.content_sha256()
    if binding["proposition_sha256"] != expected_proposition:
        raise PromptBindingError(
            f"visual proposition changed since the prompt was built "
            f"(binding {binding['proposition_sha256'][:12]}, actual {expected_proposition[:12]})"
        )
    expected_prompt = _sha256_text(prompt)
    if binding["prompt_sha256"] != expected_prompt:
        raise PromptBindingError(
            f"prompt body changed since it was bound "
            f"(binding {binding['prompt_sha256'][:12]}, actual {expected_prompt[:12]})"
        )
    if caption_visual_contract_sha256 is not None:
        bound = binding.get("caption_visual_contract_sha256")
        if bound != caption_visual_contract_sha256:
            raise PromptBindingError(
                f"caption visual contract changed since the prompt was built "
                f"(binding {str(bound)[:12]}, actual {caption_visual_contract_sha256[:12]})"
            )
    has_group_fields = [field in binding for field in _GROUP_BINDING_FIELDS]
    if any(has_group_fields) and not all(has_group_fields):
        raise PromptBindingError("prompt binding has a partial caption group binding")
    if group_id is not None or caption_contract_bindings is not None or caption_group_sha256 is not None:
        if not all(has_group_fields):
            raise PromptBindingError("prompt binding is missing caption group fields")
        if binding["group_id"] != group_id or binding["caption_group_sha256"] != caption_group_sha256:
            raise PromptBindingError("caption group changed since the prompt was built")
        expected_bindings = [dict(item) for item in caption_contract_bindings or ()]
        if binding["caption_contract_bindings"] != expected_bindings:
            raise PromptBindingError("caption contract bindings changed since the prompt was built")
        bound_ids = [str(item.get("caption_id", "")).strip() for item in expected_bindings]
        if bound_ids != binding["caption_ids"]:
            raise PromptBindingError("caption contract binding IDs do not match ordered caption IDs")


__all__ = [
    "NARRATIVE_FUNCTION_GUIDANCE",
    "PROMPT_BLOCK_ORDER",
    "PromptBindingError",
    "PromptSpecError",
    "build_aligned_prompt_blocks",
    "compute_prompt_binding",
    "verify_prompt_binding",
]
