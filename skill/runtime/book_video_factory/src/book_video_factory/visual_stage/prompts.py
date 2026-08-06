from __future__ import annotations

import hashlib
from typing import Any

from book_video_factory.reference_visuals.catalog import ReferenceCatalog
from book_video_factory.visual_assets import build_imagegen_prompt
from book_video_factory.orientation import canvas_for_orientation


_VIEW_LABELS = {
    "front": "front-facing identity portrait",
    "three_quarter": "three-quarter identity portrait",
    "full_body": "full-body identity view with natural posture",
    "expression_range": "single coherent expression-range character study",
    "wardrobe": "full-body wardrobe continuity study",
    "surface_profile": "marine surface and anatomy profile with no human expression",
    "action_profile": "natural animal action profile with readable anatomy",
    "scale_reference": "full-body scale reference in believable water space",
    "group_wide": "wide shark-group spacing study in open water",
    "approach_profile": "approaching shark-group profile with believable depth",
    "depth_profile": "underwater depth and silhouette group study",
    "crowd_wide": "wide harbor crowd anchor with no single-person identity",
    "crowd_mid": "mid-distance working crowd anchor with varied people",
    "work_action": "working harbor crowd action study",
    "headwear_variation": "group headwear variation study, not a recurring identity",
    "environment_scale": "crowd-to-harbor environment scale study",
}
_SCENE_MODE = {
    "identity_portrait": "portrait",
    "full_body": "portrait",
    "relationship": "dialogue",
    "interior": "interior",
    "exterior": "landscape",
    "object": "object",
    "daylight": "scene",
    "low_light": "scene",
    "action": "scene",
    "emotional_closeup": "portrait",
    "environment": "landscape",
    "hero_composition": "scene",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _profiles(value: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(item[key]): item for item in value}


def _art_direction(
    payload: dict[str, Any],
    catalog: ReferenceCatalog,
    palette_id: str,
    lighting_id: str,
) -> dict[str, Any]:
    look = payload["book_look"]
    palette = _profiles(payload["palette_profiles"], "palette_id")[palette_id]
    light = _profiles(payload["lighting_profiles"], "lighting_id")[lighting_id]
    materials = "; ".join(
        material
        for profile in payload["material_profiles"]
        for material in profile["materials"]
    )
    reference_roles = {
        f"image_{index}": (
            "style only — transfer broad visual language, tonal hierarchy and material handling from "
            f"{catalog.by_id()[reference_id].work_title}; do not copy people, identity, exact pose, text, subtitle, framing or layout"
        )
        for index, reference_id in enumerate(payload["style_reference_ids"], start=1)
    }
    return {
        "visual_world": (
            f"{look['visual_world']}; period {look['period']}; geography {look['geography']}; "
            f"render balance {look['render_balance']}; emotional temperature {look['emotional_temperature']}"
        ),
        "palette": ", ".join(palette["colors"]),
        "texture": materials,
        "lighting": {"key": light["key"], "fill": light["fill"], "shadow": light["shadow"]},
        "painting_identity": "original literary cinematic realism with cinema-still precision for faces, hands, animals and key objects",
        "paint_handling": {
            "focal": "cinema-still precision for faces, hands, animal anatomy and story-critical objects",
            "accent": "controlled brush texture primarily on sea, wood, rope, cloth and background weather",
            "background": "restrained material texture and period-credible atmospheric depth; no heavy impasto",
        },
        "reference_roles": reference_roles,
    }


def _task_record(
    *,
    task_id: str,
    task_kind: str,
    source_anchor_id: str | None,
    category: str,
    view: str | None,
    art_direction: dict[str, Any],
    shot: dict[str, Any],
    style_reference_ids: list[str],
    anchor_refs: list[str],
    dependencies: list[str],
    identity_dependencies: list[str],
    output_dir: str,
    palette_id: str,
    lighting_id: str,
    continuity_anchors: list[str] | None = None,
    wardrobe_state: str,
    hat_state: str,
    orientation: str,
) -> dict[str, Any]:
    characters = [
        {"continuity_anchor": value}
        for value in (continuity_anchors or [])
    ]
    canvas = canvas_for_orientation(orientation)
    prompt = (
        build_imagegen_prompt(
            art_direction, {**shot, "output_orientation": orientation}, characters
        )
        + f"\nOutput canvas: native {orientation} {canvas['width']}x{canvas['height']}; "
        "compose for this aspect ratio without rotation or embedded text."
    )
    return {
        "schema_version": "visual-task.v1",
        "task_id": task_id,
        "task_kind": task_kind,
        "source_anchor_id": source_anchor_id,
        "category": category,
        "view": view,
        "generation_lane": "host-imagegen",
        "generation_mode": "single",
        "canvas": canvas,
        "style_reference_ids": list(style_reference_ids),
        "identity_reference_ids": [],
        "anchor_refs": list(anchor_refs),
        "depends_on_task_ids": list(dependencies),
        "identity_dependency_task_ids": list(identity_dependencies),
        "required_entities": list(shot["semantic_entities"]),
        "forbidden_entities": list(shot["forbidden_entities"]),
        "risk_flags": list(shot.get("risk_flags", [])),
        "palette_id": palette_id,
        "lighting_id": lighting_id,
        "prompt": prompt,
        "prompt_sha256": _sha(prompt),
        "output_target": f"assets/generated/{output_dir}/{task_id}.png",
        "status": "planned",
        "wardrobe_state": wardrobe_state,
        "hat_state": hat_state,
    }


def compile_visual_task_prompts(
    payload: dict[str, Any],
    catalog: ReferenceCatalog,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    default_palette = payload["palette_profiles"][0]["palette_id"]
    default_light = payload["lighting_profiles"][0]["lighting_id"]
    global_forbidden = list(payload["forbidden_traits"])
    front_by_anchor: dict[str, str] = {}
    character_by_anchor = {
        anchor["anchor_id"]: anchor
        for anchor in payload["character_anchors"]
    }

    for anchor in payload["character_anchors"]:
        anchor_type = anchor["anchor_type"]
        first_view = anchor["required_views"][0]
        first_task_id = f"ANCHOR_{anchor['character_id']}_{first_view.upper()}"
        front_by_anchor[anchor["anchor_id"]] = first_task_id
        human = anchor_type == "character_identity"
        category = {
            "character_identity": "character_identity",
            "animal_anchor": "animal_identity",
            "animal_group_anchor": "animal_group_identity",
            "crowd_anchor": "crowd_identity",
        }[anchor_type]
        task_kind = "character_anchor" if human else anchor_type
        for view in anchor["required_views"]:
            task_id = f"ANCHOR_{anchor['character_id']}_{view.upper()}"
            dependencies = [] if view == first_view else [first_task_id]
            forbidden = [*global_forbidden, *anchor["forbidden_changes"]]
            if human:
                shot_size = "close portrait" if view in {"front", "three_quarter", "expression_range"} else "full body"
                lens = "85mm" if view in {"front", "three_quarter", "expression_range"} else "50mm"
                action = "hold a natural identity-consistent pose without glamour posing"
                emotion = "natural, restrained, identity-first performance"
                scene_mode = "portrait"
                semantic_entities = [anchor["name"], *anchor["invariants"], *anchor["wardrobe"]]
            else:
                shot_size = "wide" if view in {"group_wide", "crowd_wide", "environment_scale", "scale_reference"} else "medium wide"
                lens = "35mm" if anchor_type in {"animal_group_anchor", "crowd_anchor"} else "50mm"
                action = "show the specified non-human or group structure without human portrait conventions"
                emotion = "observational, physically credible, non-anthropomorphic"
                scene_mode = "landscape" if anchor_type == "crowd_anchor" else "scene"
                semantic_entities = [anchor["name"], *anchor["invariants"]]
            shot = {
                "shot_id": task_id,
                "asset_tier": "identity_anchor",
                "subject": f"{anchor['prompt_subject']}; {_VIEW_LABELS[view]}",
                "action": action,
                "shot_size": shot_size,
                "lens": lens,
                "camera_angle": "eye level",
                "composition": "clean literary cinematic study with readable geometry and subtitle-safe lower frame",
                "depth": "identity-critical anatomy, group spacing and story objects remain readable",
                "emotion": emotion,
                "narration_text": f"Lock the recurring visual design of {anchor['name']} in the {view} view",
                "semantic_entities": semantic_entities,
                "forbidden_entities": forbidden,
                "scene_mode": scene_mode,
                "lighting_intent": {
                    "key": payload["lighting_profiles"][0]["key"],
                    "fill": payload["lighting_profiles"][0]["fill"],
                    "shadow_readability": payload["lighting_profiles"][0]["shadow"],
                    "temperature_role": "balanced",
                },
                "lighting_id": default_light,
                "risk_flags": ["hero_shot"] if human and view == "front" else [],
                "wardrobe_state": anchor["wardrobe_state"],
                "hat_state": anchor["hat_state"],
            }
            tasks.append(_task_record(
                task_id=task_id,
                task_kind=task_kind,
                source_anchor_id=anchor["anchor_id"],
                category=category,
                view=view,
                art_direction=_art_direction(payload, catalog, default_palette, default_light),
                shot=shot,
                style_reference_ids=payload["style_reference_ids"],
                anchor_refs=[anchor["anchor_id"]],
                dependencies=dependencies,
                identity_dependencies=dependencies if human else [],
                output_dir="anchors",
                palette_id=default_palette,
                lighting_id=default_light,
                wardrobe_state=anchor["wardrobe_state"],
                hat_state=anchor["hat_state"],
                orientation=payload["orientation"],
            ))

    for anchor_kind, anchors, scene_mode in (
        ("scene_anchor", payload["scene_anchors"], "landscape"),
        ("object_anchor", payload["object_anchors"], "object"),
    ):
        for anchor in anchors:
            task_id = f"ANCHOR_{anchor['anchor_id']}"
            shot = {
                "shot_id": task_id,
                "asset_tier": anchor_kind,
                "subject": anchor["prompt_subject"],
                "action": "present the recurring production design clearly and without decorative substitution",
                "shot_size": "wide" if scene_mode == "landscape" else "object close-up",
                "lens": "28mm" if scene_mode == "landscape" else "70mm",
                "camera_angle": "eye level",
                "composition": "clear production-design reference with readable geometry and subtitle-safe lower frame",
                "depth": "all identity-critical geometry is readable",
                "emotion": "grounded and story-specific",
                "narration_text": f"Lock recurring visual design for {anchor['name']}",
                "semantic_entities": [anchor["name"], *anchor["invariants"]],
                "forbidden_entities": global_forbidden,
                "scene_mode": scene_mode,
                "lighting_intent": {
                    "key": payload["lighting_profiles"][0]["key"],
                    "fill": payload["lighting_profiles"][0]["fill"],
                    "shadow_readability": payload["lighting_profiles"][0]["shadow"],
                    "temperature_role": "balanced",
                },
                "risk_flags": [],
                "wardrobe_state": "not_applicable_anchor_object_or_scene",
                "hat_state": "not_applicable_anchor_object_or_scene",
            }
            tasks.append(_task_record(
                task_id=task_id,
                task_kind=anchor_kind,
                source_anchor_id=anchor["anchor_id"],
                category=anchor_kind,
                view=None,
                art_direction=_art_direction(payload, catalog, default_palette, default_light),
                shot=shot,
                style_reference_ids=payload["style_reference_ids"],
                anchor_refs=[anchor["anchor_id"]],
                dependencies=[],
                identity_dependencies=[],
                output_dir="anchors",
                palette_id=default_palette,
                lighting_id=default_light,
                wardrobe_state="not_applicable_anchor_object_or_scene",
                hat_state="not_applicable_anchor_object_or_scene",
                orientation=payload["orientation"],
            ))

    for source in payload["lookdev_tasks"]:
        task_id = f"LOOKDEV_{source['task_id']}"
        referenced_characters = [
            character_by_anchor[ref]
            for ref in source["anchor_refs"]
            if ref in character_by_anchor
        ]
        dependencies = [front_by_anchor[ref] for ref in source["anchor_refs"] if ref in front_by_anchor]
        identity_dependencies = [
            front_by_anchor[ref]
            for ref in source["anchor_refs"]
            if ref in front_by_anchor
            and next((anchor for anchor in referenced_characters if anchor["anchor_id"] == ref), {}).get("anchor_type") == "character_identity"
        ]
        continuity_anchors: list[str] = []
        for anchor in referenced_characters:
            if anchor["character_id"] == "C005":
                continuity_anchors.append(
                    f"C005 crowd-only reference; {anchor['prompt_subject']}; never use its hat or face as CHAR_C001 identity evidence"
                )
            else:
                continuity_anchors.append(
                    f"{anchor['prompt_subject']}; identity invariants: {'; '.join(anchor['invariants'])}; "
                    f"wardrobe_state: {source['wardrobe_state']}; hat_state: {source['hat_state']}; "
                    f"never change: {'; '.join(anchor['forbidden_changes'])}"
                )
        identity_forbidden = [
            item
            for anchor in referenced_characters
            for item in anchor["forbidden_changes"]
        ]
        shot = {
            "shot_id": task_id,
            "asset_tier": "lookdev",
            "subject": source["subject"],
            "action": source["action"],
            "shot_size": source["shot_size"],
            "lens": source["lens"],
            "camera_angle": source["camera_angle"],
            "composition": source["composition"],
            "depth": source["depth"],
            "emotion": source["emotion"],
            "narration_text": f"LookDev category {source['category']}: {source['subject']}; {source['action']}",
            "semantic_entities": source["required_entities"],
            "forbidden_entities": [*global_forbidden, *identity_forbidden, *source["forbidden_entities"]],
            "scene_mode": _SCENE_MODE[source["category"]],
            "lighting_intent": {
                "key": _profiles(payload["lighting_profiles"], "lighting_id")[source["lighting_id"]]["key"],
                "fill": _profiles(payload["lighting_profiles"], "lighting_id")[source["lighting_id"]]["fill"],
                "shadow_readability": _profiles(payload["lighting_profiles"], "lighting_id")[source["lighting_id"]]["shadow"],
                "temperature_role": "balanced",
            },
            "lighting_id": source["lighting_id"],
            "risk_flags": source["risk_flags"],
            "wardrobe_state": source["wardrobe_state"],
            "hat_state": source["hat_state"],
        }
        tasks.append(_task_record(
            task_id=task_id,
            task_kind="lookdev",
            source_anchor_id=None,
            category=source["category"],
            view=None,
            art_direction=_art_direction(payload, catalog, source["palette_id"], source["lighting_id"]),
            shot=shot,
            style_reference_ids=source["style_reference_ids"],
            anchor_refs=source["anchor_refs"],
            dependencies=dependencies,
            identity_dependencies=identity_dependencies,
            output_dir="lookdev",
            palette_id=source["palette_id"],
            lighting_id=source["lighting_id"],
            continuity_anchors=continuity_anchors,
            wardrobe_state=source["wardrobe_state"],
            hat_state=source["hat_state"],
            orientation=payload["orientation"],
        ))
    return tasks
