from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from .manifests import safe_project_output, sha256_file


class VisualContractError(ValueError):
    pass


SHOT_ID_PATTERN = re.compile(r"[A-Z][A-Z0-9_-]{1,63}")

SCENE_MODE_GUIDANCE = {
    "portrait": (
        "Portrait staging: the named character's face and identifying silhouette "
        "are the unmistakable focal subject."
    ),
    "dialogue": (
        "Dialogue staging: every speaking and listening subject is visible; show "
        "eye-line, body orientation and spatial tension rather than an unrelated insert."
    ),
    "scene": (
        "Scene staging: the named location and story action dominate the frame; "
        "do not substitute a generic portrait or decorative still life."
    ),
    "object": (
        "Object staging: the named object is large, recognizable and narratively "
        "specific rather than generic decoration."
    ),
    "interior": (
        "Interior staging: the named room, person and action are all legible, with "
        "furniture and props serving the story."
    ),
    "landscape": (
        "Landscape staging: the named environment is the primary subject and any "
        "character remains present at the scale required by the narration."
    ),
}

PAINTING_TEMPERATURE_ROLES = {
    "warm-dominant",
    "balanced",
    "cool-counterpoint",
}


def validate_painting_light_cadence(shots: list[dict[str, Any]]) -> None:
    warm_run: list[str] = []
    for shot in shots:
        if not isinstance(shot, dict):
            raise VisualContractError("painting shot must be an object")
        intent = _required_mapping(
            shot,
            "lighting_intent",
            ("key", "fill", "shadow_readability", "temperature_role"),
        )
        role = str(intent["temperature_role"]).strip()
        if role not in PAINTING_TEMPERATURE_ROLES:
            raise VisualContractError(
                "lighting_intent.temperature_role must be warm-dominant, "
                "balanced, or cool-counterpoint"
            )
        if role == "warm-dominant":
            warm_run.append(str(shot.get("shot_id", "?")))
            if len(warm_run) >= 4:
                raise VisualContractError(
                    "literary oil batch has four consecutive warm-dominant frames: "
                    + ", ".join(warm_run[-4:])
                )
        else:
            warm_run = []


def select_shot_characters(
    shot: dict[str, Any],
    characters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    references = shot.get("references", [])
    if not isinstance(references, list):
        raise VisualContractError("shot.references must be an array")
    referenced = {
        re.sub(r"[^A-Z0-9]", "", str(reference.get("asset_id", "")).upper())
        for reference in references
        if isinstance(reference, dict) and reference.get("role") == "character"
    }
    selected: list[dict[str, Any]] = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        identifier = re.sub(
            r"[^A-Z0-9]",
            "",
            str(character.get("character_id", "")).upper(),
        )
        if identifier and any(identifier in asset_id for asset_id in referenced):
            selected.append(character)
    return selected


def _required_mapping(
    payload: dict[str, Any], key: str, required: tuple[str, ...]
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise VisualContractError(f"{key} must be an object")
    missing = [field for field in required if not str(value.get(field, "")).strip()]
    if missing:
        raise VisualContractError(f"{key} is missing: {', '.join(missing)}")
    return value


def _required_text(payload: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    missing = [key for key in keys if not str(payload.get(key, "")).strip()]
    if missing:
        raise VisualContractError(f"{label} is missing: {', '.join(missing)}")


def build_imagegen_prompt(
    art_direction: dict[str, Any],
    shot: dict[str, Any],
    characters: list[dict[str, Any]],
) -> str:
    _required_text(
        art_direction,
        ("visual_world", "palette", "texture"),
        "art direction",
    )
    lighting = _required_mapping(
        art_direction, "lighting", ("key", "fill", "shadow")
    )
    _required_text(
        shot,
        (
            "shot_id",
            "asset_tier",
            "subject",
            "action",
            "shot_size",
            "lens",
            "camera_angle",
            "composition",
            "depth",
            "emotion",
        ),
        "shot",
    )
    shot_id = str(shot["shot_id"])
    if SHOT_ID_PATTERN.fullmatch(shot_id) is None:
        raise VisualContractError("shot_id must be a safe uppercase identifier")
    anchors = [
        str(character["continuity_anchor"]).strip()
        for character in characters
        if isinstance(character, dict)
        and str(character.get("continuity_anchor", "")).strip()
    ]
    continuity = "; ".join(anchors) if anchors else "no recurring character"
    output_orientation = str(shot.get("output_orientation", "landscape"))
    if output_orientation not in {"landscape", "portrait"}:
        raise VisualContractError("output_orientation must be landscape or portrait")
    aspect_ratio = "16:9" if output_orientation == "landscape" else "9:16"
    lines = [
            f"Create one production-ready {aspect_ratio} {output_orientation} cinematic frame for shot {shot_id}.",
            f"Visual world: {art_direction['visual_world']}.",
            f"Subject and action: {shot['subject']}; {shot['action']}.",
            (
                f"Camera: {shot['shot_size']}, {shot['lens']} lens, "
                f"{shot['camera_angle']}; composition: {shot['composition']}; "
                f"depth: {shot['depth']}."
            ),
            f"Performance: {shot['emotion']}.",
            (
                f"Lighting: key {lighting['key']}; fill {lighting['fill']}; "
                f"shadow {lighting['shadow']}."
            ),
            f"Lighting profile: {shot.get('lighting_id', 'unspecified')}.",
            f"Palette: {art_direction['palette']}.",
            f"Surface and image texture: {art_direction['texture']}.",
            f"Character continuity anchors: {continuity}.",
            (
                "Deliver a clean frame: no text, no captions, no logo, no watermark, "
                "no UI, no border, no official book cover, no illegible pseudo-writing; not a photograph."
            ),
            (
                "Preserve plausible anatomy, hands when visible, animal structure, object geometry, "
                "era-appropriate clothing and architecture. Avoid heavy impasto and generic AI oil-painting filters."
            ),
    ]
    painting_identity = str(art_direction.get("painting_identity", "")).strip()
    if painting_identity:
        narration = str(shot.get("narration_text", "")).strip()
        entities = shot.get("semantic_entities")
        scene_mode = str(shot.get("scene_mode", "")).strip()
        if not narration:
            raise VisualContractError("literary oil shot requires narration_text")
        if not isinstance(entities, list) or not all(
            isinstance(entity, str) and entity.strip() for entity in entities
        ):
            raise VisualContractError(
                "literary oil shot requires semantic_entities to be a string list"
            )
        if scene_mode not in SCENE_MODE_GUIDANCE:
            raise VisualContractError(
                "literary oil shot requires a supported scene_mode"
            )
        if not entities and scene_mode != "landscape":
            raise VisualContractError(
                "literary oil shot requires nonempty semantic_entities outside a landscape shot"
            )
        paint = _required_mapping(
            art_direction,
            "paint_handling",
            ("focal", "accent", "background"),
        )
        shot_light = _required_mapping(
            shot,
            "lighting_intent",
            ("key", "fill", "shadow_readability", "temperature_role"),
        )
        temperature_role = str(shot_light["temperature_role"]).strip()
        if temperature_role not in PAINTING_TEMPERATURE_ROLES:
            raise VisualContractError(
                "lighting_intent.temperature_role must be warm-dominant, "
                "balanced, or cool-counterpoint"
            )
        entity_text = ", ".join(entity.strip() for entity in entities)
        reference_roles = art_direction.get("reference_roles", {})
        if reference_roles is not None and not isinstance(reference_roles, dict):
            raise VisualContractError("reference_roles must be an object")
        forbidden_entities = shot.get("forbidden_entities", [])
        if forbidden_entities is not None and (
            not isinstance(forbidden_entities, list)
            or not all(isinstance(entity, str) and entity.strip() for entity in forbidden_entities)
        ):
            raise VisualContractError("forbidden_entities must be an array of nonempty strings")
        forbidden_text = ", ".join(entity.strip() for entity in forbidden_entities) or "none beyond the project-wide prohibitions"
        painting_lines = [
            f"Visual rendering: {painting_identity}; restrained material rendering.",
            f"Narration this frame must illustrate: {narration}",
            f"Visible semantic entities: {entity_text}.",
            f"Forbidden visible entities or traits: {forbidden_text}.",
            SCENE_MODE_GUIDANCE[scene_mode],
            (
                "Every listed semantic entity must be visibly recognizable and "
                "perform the stated narrative role; do not replace it with symbolic "
                "scenery unless the shot explicitly requests an object or landscape."
            ),
            (
                f"Paint handling: focal areas use {paint['focal']}; accents use "
                f"{paint['accent']}; background uses {paint['background']}."
            ),
            (
                f"Explicit continuity state: wardrobe_state={shot.get('wardrobe_state', 'not_applicable')}; "
                f"hat_state={shot.get('hat_state', 'not_applicable')}."
            ),
            (
                f"Shot-specific light: key {shot_light['key']}; fill "
                f"{shot_light['fill']}; shadow readability "
                f"{shot_light['shadow_readability']}; temperature role "
                f"{temperature_role}."
            ),
        ]
        edit_target = str(shot.get("edit_target", "")).strip()
        if edit_target:
            painting_lines.append(
                f"Edit target: {edit_target}. Preserve its named subjects, action, "
                f"camera, spatial relationships and {aspect_ratio} composition unless a "
                "shot-specific instruction explicitly changes one of them."
            )
        reference_instructions = shot.get("reference_instructions")
        if reference_instructions is not None:
            if not isinstance(reference_instructions, list) or not all(
                isinstance(item, str) and item.strip()
                for item in reference_instructions
            ):
                raise VisualContractError(
                    "reference_instructions must be an array of nonempty strings"
                )
            painting_lines.extend(item.strip() for item in reference_instructions)
        for key, value in reference_roles.items():
            if not isinstance(value, str) or not value.strip():
                raise VisualContractError("reference role values must be nonempty")
            match = re.fullmatch(r"image_(\d+)", str(key))
            if match is None:
                raise VisualContractError(
                    "reference role keys must use image_<number>"
                )
            painting_lines.append(
                f"Image {match.group(1)} controls {value.strip()}."
            )
        if edit_target:
            painting_lines.append(
                "Use every non-target reference only for its assigned role. Preserve "
                "the edit target's story composition; do not copy people, exact pose, "
                "text, subtitle or layout from style references."
            )
        else:
            painting_lines.append(
                "Use references only for their assigned role. Create a new composition "
                "and do not copy their people, exact pose, text, subtitle or layout."
            )
        lines[1:1] = painting_lines
    return "\n".join(lines)


def ingest_imagegen_asset(
    project: Path,
    *,
    shot_id: str,
    source: Path,
    prompt: str,
    tool_call_id: str,
) -> dict[str, Any]:
    if SHOT_ID_PATTERN.fullmatch(shot_id) is None:
        raise VisualContractError("shot_id must be a safe uppercase identifier")
    if not source.is_file():
        raise FileNotFoundError(source)
    if not prompt.strip() or not tool_call_id.strip():
        raise VisualContractError("prompt and tool_call_id are required")
    suffix = source.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise VisualContractError(f"unsupported image format: {suffix}")
    root = project.resolve()
    target = safe_project_output(
        root,
        root / "03_images_生成图片" / "generated" / f"{shot_id}{suffix}",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    shutil.copy2(source, target)
    return {
        "asset_id": shot_id,
        "shot_id": shot_id,
        "path": target.relative_to(root / "03_images_生成图片").as_posix(),
        "sha256": sha256_file(target),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "provider": "codex-imagegen",
        "tool_call_id": tool_call_id,
        "qa": "pending",
    }
