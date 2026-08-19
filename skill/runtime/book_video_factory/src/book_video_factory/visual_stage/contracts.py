from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from book_video_factory.image_qc import validate_reference_envelope
from book_video_factory.reference_visuals.catalog import ReferenceCatalog


class VisualStageContractError(ValueError):
    pass


_ID = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ABSOLUTE_PATH = re.compile(r"(?:^/|^[A-Za-z]:[\\/]|file://|/(?:Users|home|mnt|private|Volumes)/)", re.IGNORECASE)
LOOKDEV_CATEGORIES = {
    "identity_portrait",
    "full_body",
    "relationship",
    "interior",
    "exterior",
    "object",
    "daylight",
    "low_light",
    "action",
    "emotional_closeup",
    "environment",
    "hero_composition",
}
RISK_FLAGS = {
    "hands",
    "phone",
    "tool_use",
    "water_action",
    "animal_contact",
    "body_contact",
    "reflection",
    "death_climax",
    "hero_shot",
}
REQUIRED_VIEWS = {"front", "three_quarter", "full_body", "expression_range", "wardrobe"}

TOP_FIELDS = {
    "schema_version",
    "release_id",
    "hbg_bridge_digest",
    "release_text_sha256",
    "orientation",
    "global_kernel_id",
    "style_reference_ids",
    "book_look",
    "palette_profiles",
    "lighting_profiles",
    "material_profiles",
    "composition_rules",
    "repeated_motifs",
    "forbidden_traits",
    "character_anchors",
    "scene_anchors",
    "object_anchors",
    "symbolic_mappings",
    "lookdev_tasks",
}
BOOK_LOOK_FIELDS = {
    "period",
    "geography",
    "visual_world",
    "render_balance",
    "emotional_temperature",
    "composition_language",
    "portrait_language",
    "environment_language",
}
PALETTE_FIELDS = {"palette_id", "name", "colors", "use_cases", "diagnostic_envelope"}
LIGHTING_FIELDS = {"lighting_id", "name", "key", "fill", "shadow", "allowed_times"}
MATERIAL_FIELDS = {"material_id", "name", "materials"}
CHARACTER_ANCHOR_FIELDS = {
    "anchor_id",
    "anchor_type",
    "character_id",
    "name",
    "prompt_subject",
    "required_views",
    "invariants",
    "allowed_changes",
    "forbidden_changes",
    "wardrobe",
    "wardrobe_state",
    "hat_state",
    "anchor_status",
}
OTHER_ANCHOR_FIELDS = {"anchor_id", "name", "prompt_subject", "invariants", "anchor_status"}
TASK_FIELDS = {
    "task_id",
    "category",
    "subject",
    "action",
    "shot_size",
    "lens",
    "camera_angle",
    "composition",
    "depth",
    "emotion",
    "palette_id",
    "lighting_id",
    "required_entities",
    "forbidden_entities",
    "anchor_refs",
    "risk_flags",
    "generation_mode",
    "style_reference_ids",
    "identity_reference_ids",
    "wardrobe_state",
    "hat_state",
}

ANCHOR_TYPES = {
    "character_identity",
    "animal_anchor",
    "animal_group_anchor",
    "crowd_anchor",
}
HUMAN_VIEWS = {"front", "three_quarter", "full_body", "expression_range", "wardrobe"}
ANCHOR_VIEW_SETS = {
    "animal_anchor": {"surface_profile", "three_quarter", "full_body", "action_profile", "scale_reference"},
    "animal_group_anchor": {"group_wide", "approach_profile", "depth_profile", "action_profile", "scale_reference"},
    "crowd_anchor": {"crowd_wide", "crowd_mid", "work_action", "headwear_variation", "environment_scale"},
}


def _unknown(mapping: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = set(mapping) - allowed
    if extra:
        raise VisualStageContractError(f"{label} contains unknown fields: {', '.join(sorted(extra))}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VisualStageContractError(f"{label} must be an object")
    return value


def _strict_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise VisualStageContractError(f"{label} must be a nonempty trimmed string")
    if _ABSOLUTE_PATH.search(value):
        raise VisualStageContractError(f"{label} must not contain a local absolute path")
    return value


def _strict_id(value: Any, label: str) -> str:
    text = _strict_text(value, label)
    if _ID.fullmatch(text) is None:
        raise VisualStageContractError(f"{label} must be a safe uppercase identifier")
    return text


def _sha(value: Any, label: str) -> str:
    text = _strict_text(value, label)
    if _SHA256.fullmatch(text) is None:
        raise VisualStageContractError(f"{label} must be a sha256")
    return text


def _text_list(value: Any, label: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise VisualStageContractError(f"{label} must contain at least {minimum} item(s)")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _strict_text(item, f"{label}[{index}]")
        if text in result:
            raise VisualStageContractError(f"{label} contains duplicate value: {text}")
        result.append(text)
    return result


def _id_list(value: Any, label: str, *, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise VisualStageContractError(f"{label} must contain at least {minimum} item(s)")
    result = [_strict_id(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise VisualStageContractError(f"{label} contains duplicate identifiers")
    return result


def _require_exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    mapping = _mapping(value, label)
    _unknown(mapping, fields, label)
    missing = fields - set(mapping)
    if missing:
        raise VisualStageContractError(f"{label} is missing: {', '.join(sorted(missing))}")
    return mapping


def _validate_book_look(value: Any) -> dict[str, Any]:
    look = _require_exact_mapping(value, BOOK_LOOK_FIELDS, "book_look")
    for key in ("period", "geography", "visual_world", "render_balance", "emotional_temperature"):
        _strict_text(look[key], f"book_look.{key}")
    for key in ("composition_language", "portrait_language", "environment_language"):
        _text_list(look[key], f"book_look.{key}", minimum=2)
    return look


def _validate_anchor_status(anchor: dict[str, Any], label: str) -> None:
    status = anchor.get("anchor_status", "pending")
    if status != "pending":
        raise VisualStageContractError(f"{label}.anchor_status must be pending before human approval")
    anchor["anchor_status"] = "pending"


def _validate_character_anchors(
    value: Any, phase2_character_names: Mapping[str, str]
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise VisualStageContractError("character_anchors must be an array")
    result: list[dict[str, Any]] = []
    seen_anchor: set[str] = set()
    seen_character: set[str] = set()
    for index, raw in enumerate(value):
        label = f"character_anchors[{index}]"
        anchor = _mapping(raw, label)
        _unknown(anchor, CHARACTER_ANCHOR_FIELDS, label)
        required_without_status = CHARACTER_ANCHOR_FIELDS - {"anchor_status"}
        missing = required_without_status - set(anchor)
        if missing:
            raise VisualStageContractError(f"{label} is missing: {', '.join(sorted(missing))}")
        anchor_id = _strict_id(anchor["anchor_id"], f"{label}.anchor_id")
        character_id = _strict_id(anchor["character_id"], f"{label}.character_id")
        if anchor_id in seen_anchor or character_id in seen_character:
            raise VisualStageContractError("duplicate character anchor or character_id")
        _strict_text(anchor["name"], f"{label}.name")
        _strict_text(anchor["prompt_subject"], f"{label}.prompt_subject")
        anchor_type = _strict_text(anchor["anchor_type"], f"{label}.anchor_type")
        if anchor_type not in ANCHOR_TYPES:
            raise VisualStageContractError(f"{label}.anchor_type is unsupported: {anchor_type}")
        views = set(_text_list(anchor["required_views"], f"{label}.required_views", minimum=5))
        expected_views = HUMAN_VIEWS if anchor_type == "character_identity" else ANCHOR_VIEW_SETS[anchor_type]
        if views != expected_views:
            raise VisualStageContractError(f"{label}.required_views must equal {sorted(expected_views)}")
        for key in ("invariants", "allowed_changes", "forbidden_changes", "wardrobe"):
            _text_list(anchor[key], f"{label}.{key}", minimum=1)
        _strict_text(anchor["wardrobe_state"], f"{label}.wardrobe_state")
        _strict_text(anchor["hat_state"], f"{label}.hat_state")
        character_name = phase2_character_names.get(character_id, "")
        if character_name == "圣地亚哥" and "旧草帽" not in anchor["hat_state"]:
            raise VisualStageContractError("CHAR_C001.hat_state must explicitly bind the existing 旧草帽")
        if character_name == "大马林鱼" and anchor_type != "animal_anchor":
            raise VisualStageContractError("C003 must use animal_anchor, not a human identity template")
        if character_name == "鲨鱼群" and anchor_type != "animal_group_anchor":
            raise VisualStageContractError("C004 must use animal_group_anchor, not a portrait template")
        if character_name == "海港渔民" and anchor_type != "crowd_anchor":
            raise VisualStageContractError("C005 must use crowd_anchor and cannot be an identity anchor")
        _validate_anchor_status(anchor, label)
        seen_anchor.add(anchor_id)
        seen_character.add(character_id)
        result.append(anchor)
    return result


def _validate_other_anchors(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise VisualStageContractError(f"{label} must be an array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item_label = f"{label}[{index}]"
        anchor = _mapping(raw, item_label)
        _unknown(anchor, OTHER_ANCHOR_FIELDS, item_label)
        missing = (OTHER_ANCHOR_FIELDS - {"anchor_status"}) - set(anchor)
        if missing:
            raise VisualStageContractError(f"{item_label} is missing: {', '.join(sorted(missing))}")
        anchor_id = _strict_id(anchor["anchor_id"], f"{item_label}.anchor_id")
        if anchor_id in seen:
            raise VisualStageContractError(f"duplicate anchor_id: {anchor_id}")
        _strict_text(anchor["name"], f"{item_label}.name")
        _strict_text(anchor["prompt_subject"], f"{item_label}.prompt_subject")
        _text_list(anchor["invariants"], f"{item_label}.invariants", minimum=1)
        _validate_anchor_status(anchor, item_label)
        seen.add(anchor_id)
        result.append(anchor)
    return result


def validate_visual_stage_input(
    payload: dict[str, Any],
    *,
    phase2_characters: list[dict[str, Any]],
    catalog: ReferenceCatalog,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VisualStageContractError("visual stage input must be an object")
    value = copy.deepcopy(payload)
    _unknown(value, TOP_FIELDS, "visual stage input")
    missing = TOP_FIELDS - set(value)
    if missing:
        raise VisualStageContractError(f"visual stage input is missing: {', '.join(sorted(missing))}")
    if value.get("schema_version") != "visual-stage-input.v1":
        raise VisualStageContractError("unsupported visual stage schema_version")
    _strict_text(value["release_id"], "release_id")
    _sha(value["hbg_bridge_digest"], "hbg_bridge_digest")
    _sha(value["release_text_sha256"], "release_text_sha256")
    if value.get("orientation") not in {"landscape", "portrait"}:
        raise VisualStageContractError("orientation must be landscape or portrait")
    if value.get("global_kernel_id") != catalog.kernel_id:
        raise VisualStageContractError("global_kernel_id does not match locked catalog")
    known_refs = catalog.by_id()
    refs = _id_list(value["style_reference_ids"], "style_reference_ids", minimum=1)
    unknown_refs = sorted(set(refs) - set(known_refs))
    if unknown_refs:
        raise VisualStageContractError(f"unknown style reference: {', '.join(unknown_refs)}")
    _validate_book_look(value["book_look"])
    raw_mappings = value.get("symbolic_mappings", [])
    if not isinstance(raw_mappings, list):
        raise VisualStageContractError("visual stage input symbolic_mappings must be an array")
    for index, raw in enumerate(raw_mappings):
        label = f"symbolic_mappings[{index}]"
        if not isinstance(raw, dict):
            raise VisualStageContractError(f"{label} must be an object")
        _unknown(raw, {"mapping_id", "status", "source_concept", "surrogate_object", "rationale"}, label)
        _strict_id(str(raw.get("mapping_id", "")), f"{label}.mapping_id")
        if raw.get("status") != "approved":
            raise VisualStageContractError(f"{label}.status must be 'approved'")
        _strict_text(str(raw.get("source_concept", "")), f"{label}.source_concept")
        _strict_text(str(raw.get("surrogate_object", "")), f"{label}.surrogate_object")

    palettes = value["palette_profiles"]
    if not isinstance(palettes, list) or not palettes:
        raise VisualStageContractError("palette_profiles must be nonempty")
    palette_ids: set[str] = set()
    for index, raw in enumerate(palettes):
        label = f"palette_profiles[{index}]"
        item = _require_exact_mapping(raw, PALETTE_FIELDS, label)
        identifier = _strict_id(item["palette_id"], f"{label}.palette_id")
        if identifier in palette_ids:
            raise VisualStageContractError(f"duplicate palette_id: {identifier}")
        _strict_text(item["name"], f"{label}.name")
        _text_list(item["colors"], f"{label}.colors", minimum=3)
        _text_list(item["use_cases"], f"{label}.use_cases", minimum=1)
        try:
            validate_reference_envelope(item["diagnostic_envelope"])
        except ValueError as error:
            raise VisualStageContractError(f"{label}.diagnostic_envelope: {error}") from error
        palette_ids.add(identifier)

    lights = value["lighting_profiles"]
    if not isinstance(lights, list) or not lights:
        raise VisualStageContractError("lighting_profiles must be nonempty")
    lighting_ids: set[str] = set()
    for index, raw in enumerate(lights):
        label = f"lighting_profiles[{index}]"
        item = _require_exact_mapping(raw, LIGHTING_FIELDS, label)
        identifier = _strict_id(item["lighting_id"], f"{label}.lighting_id")
        if identifier in lighting_ids:
            raise VisualStageContractError(f"duplicate lighting_id: {identifier}")
        for key in ("name", "key", "fill", "shadow"):
            _strict_text(item[key], f"{label}.{key}")
        _text_list(item["allowed_times"], f"{label}.allowed_times", minimum=1)
        lighting_ids.add(identifier)

    materials = value["material_profiles"]
    if not isinstance(materials, list) or not materials:
        raise VisualStageContractError("material_profiles must be nonempty")
    material_ids: set[str] = set()
    for index, raw in enumerate(materials):
        label = f"material_profiles[{index}]"
        item = _require_exact_mapping(raw, MATERIAL_FIELDS, label)
        identifier = _strict_id(item["material_id"], f"{label}.material_id")
        if identifier in material_ids:
            raise VisualStageContractError(f"duplicate material_id: {identifier}")
        _strict_text(item["name"], f"{label}.name")
        _text_list(item["materials"], f"{label}.materials", minimum=3)
        material_ids.add(identifier)

    for key in ("composition_rules", "repeated_motifs", "forbidden_traits"):
        _text_list(value[key], key, minimum=2)

    phase2_character_names = {
        str(item.get("character_id")): str(item.get("name", ""))
        for item in phase2_characters
        if isinstance(item, dict)
    }
    character_anchors = _validate_character_anchors(
        value["character_anchors"], phase2_character_names
    )
    scene_anchors = _validate_other_anchors(value["scene_anchors"], "scene_anchors")
    object_anchors = _validate_other_anchors(value["object_anchors"], "object_anchors")
    expected_character_ids = {
        _strict_id(item.get("character_id"), "phase2 character_id")
        for item in phase2_characters
        if isinstance(item, dict)
    }
    actual_character_ids = {item["character_id"] for item in character_anchors}
    if actual_character_ids != expected_character_ids:
        raise VisualStageContractError(
            f"character anchor coverage mismatch: expected {sorted(expected_character_ids)}, got {sorted(actual_character_ids)}"
        )
    all_anchor_ids = {
        *(item["anchor_id"] for item in character_anchors),
        *(item["anchor_id"] for item in scene_anchors),
        *(item["anchor_id"] for item in object_anchors),
    }
    anchor_names = {item["anchor_id"]: item["name"] for item in character_anchors}
    if len(all_anchor_ids) != len(character_anchors) + len(scene_anchors) + len(object_anchors):
        raise VisualStageContractError("duplicate anchor_id across anchor types")

    tasks = value["lookdev_tasks"]
    if not isinstance(tasks, list) or len(tasks) != 12:
        raise VisualStageContractError("lookdev_tasks must contain exactly 12 tasks")
    seen_tasks: set[str] = set()
    categories: set[str] = set()
    for index, raw in enumerate(tasks):
        label = f"lookdev_tasks[{index}]"
        task = _mapping(raw, label)
        _unknown(task, TASK_FIELDS, label)
        required = TASK_FIELDS - {"identity_reference_ids"}
        missing = required - set(task)
        if missing:
            raise VisualStageContractError(f"{label} is missing: {', '.join(sorted(missing))}")
        task_id = _strict_id(task["task_id"], f"{label}.task_id")
        if task_id in seen_tasks:
            raise VisualStageContractError(f"duplicate task_id: {task_id}")
        category = _strict_text(task["category"], f"{label}.category")
        if category not in LOOKDEV_CATEGORIES:
            raise VisualStageContractError(f"unsupported LookDev category: {category}")
        categories.add(category)
        for key in ("subject", "action", "shot_size", "lens", "camera_angle", "composition", "depth", "emotion"):
            _strict_text(task[key], f"{label}.{key}")
        _strict_text(task["wardrobe_state"], f"{label}.wardrobe_state")
        _strict_text(task["hat_state"], f"{label}.hat_state")
        palette_id = _strict_id(task["palette_id"], f"{label}.palette_id")
        lighting_id = _strict_id(task["lighting_id"], f"{label}.lighting_id")
        if palette_id not in palette_ids:
            raise VisualStageContractError(f"{label} uses unknown palette_id: {palette_id}")
        if lighting_id not in lighting_ids:
            raise VisualStageContractError(f"{label} uses unknown lighting_id: {lighting_id}")
        _text_list(task["required_entities"], f"{label}.required_entities", minimum=1)
        _text_list(task["forbidden_entities"], f"{label}.forbidden_entities", minimum=1)
        anchors = _id_list(task["anchor_refs"], f"{label}.anchor_refs", minimum=1)
        unknown_anchors = sorted(set(anchors) - all_anchor_ids)
        if unknown_anchors:
            raise VisualStageContractError(f"{label} uses unknown anchor: {', '.join(unknown_anchors)}")
        risks = _text_list(task["risk_flags"], f"{label}.risk_flags", minimum=0)
        unknown_risks = sorted(set(risks) - RISK_FLAGS)
        if unknown_risks:
            raise VisualStageContractError(f"{label} uses unknown risk flag: {', '.join(unknown_risks)}")
        if task.get("generation_mode") != "single":
            raise VisualStageContractError(f"{label}.generation_mode must be single in Phase 3")
        task_refs = _id_list(task["style_reference_ids"], f"{label}.style_reference_ids", minimum=1)
        if not set(task_refs).issubset(set(refs)):
            raise VisualStageContractError(f"{label} uses unknown style reference")
        identity_refs = task.get("identity_reference_ids", [])
        if not isinstance(identity_refs, list):
            raise VisualStageContractError(f"{label}.identity_reference_ids must be an array")
        if any(item in known_refs for item in identity_refs):
            raise VisualStageContractError(f"{label} may not use a gold style reference as an identity reference")
        if identity_refs:
            raise VisualStageContractError(f"{label} identity reference images do not exist before anchor generation")
        task["identity_reference_ids"] = []
        if (
            any(anchor_names.get(anchor_id) == "圣地亚哥" for anchor_id in anchors)
            and "旧草帽" not in task["hat_state"]
        ):
            raise VisualStageContractError(f"{label}.hat_state must explicitly describe CHAR_C001's 旧草帽")
        if task_id == "LOOKDEV_LD05":
            if set(anchors) != {"SCENE_HARBOR", "OBJ_BOAT"} or "圣地亚哥" in task["required_entities"]:
                raise VisualStageContractError("LOOKDEV_LD05 must be a pure harbor environment anchor")
        if task_id == "LOOKDEV_LD08":
            required = {"圣地亚哥", "夜海", "钓线"}
            if not required.issubset(set(task["required_entities"])):
                raise VisualStageContractError("LOOKDEV_LD08 must explicitly show Santiago, the night sea and the fishing line")
            if not {"鱼体进入船内", "鱼平躺在船内"}.issubset(set(task["forbidden_entities"])):
                raise VisualStageContractError("LOOKDEV_LD08 must forbid the fish entering or lying in the boat")
        seen_tasks.add(task_id)
    if categories != LOOKDEV_CATEGORIES:
        raise VisualStageContractError(
            f"lookdev category coverage mismatch: expected {sorted(LOOKDEV_CATEGORIES)}, got {sorted(categories)}"
        )
    return value
