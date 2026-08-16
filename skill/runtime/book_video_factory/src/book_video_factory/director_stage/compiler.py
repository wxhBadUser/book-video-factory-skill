from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from book_video_factory.audio_stage.status import audio_stage_status
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.orientation import OrientationError, validate_orientation_contract
from book_video_factory.semantic_alignment.caption_contract import (
    CaptionVisualContract,
    merge_visual_event_states,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    NARRATIVE_FUNCTIONS,
    load_current_caption_grouping_document,
)
from book_video_factory.semantic_alignment.classifier import (
    PropositionClassifierError,
    classify_proposition,
)
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.validation import (
    SemanticContractError,
    validate_visual_proposition,
)
from book_video_factory.semantic_alignment.group_contract import (
    aggregate_group_visual_contract,
)
from book_video_factory.semantic_alignment.scene_continuity import (
    SceneContinuityError,
    build_span_review_groups,
    load_current_scene_continuity_document,
)
from book_video_factory.semantic_alignment.prompting import (
    PromptSpecError,
    build_aligned_prompt_blocks,
    compute_prompt_binding,
)
from book_video_factory.semantic_alignment.contract_bindings import (
    ContractBindingError,
    aggregate_contract_bindings_sha256,
)
from book_video_factory.production_task_validation import (
    ProductionTaskValidationError,
    validate_production_image_task,
)
from book_video_factory.visual_assets import build_imagegen_prompt


class DirectorStageError(RuntimeError):
    """Phase 5 inputs are incomplete or semantically unsafe."""


class DirectorStageConflict(DirectorStageError):
    """Existing Phase 5 evidence differs from deterministic expected output."""


@dataclass(frozen=True)
class DirectorStageResult:
    status: str
    manifest_path: Path
    stage_manifest_path: Path
    next_stage_status: str
    audio_stage_manifest_sha256: str


_OUTPUT_DIR = "05_director"
_OUTPUTS = (
    "DIRECTOR_TIMELINE.json",
    "MOTION_MANIFEST.json",
    "IMAGE_TASKS.jsonl",
    "SHEET_MAP.json",
)
_ALLOWED_MOTIONS = {"hold"}  # new production: static stills, hard cuts only
_HIGH_RISK = {
    "hands", "phone", "tool_use", "water_action", "animal_contact",
    "body_contact", "reflection", "death_climax", "hero_shot",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise DirectorStageError(f"{label} is missing or symlinked: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DirectorStageError(f"{label} is unreadable: {error}") from error


def _project_file(root: Path, relative: str, label: str) -> Path:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise DirectorStageError(f"{label} is missing or symlinked: {relative}")
    return path


def _load_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise DirectorStageError(f"{label} is missing or symlinked")
    values: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise DirectorStageError(f"{label} line {number} is invalid JSON") from error
        if not isinstance(value, dict):
            raise DirectorStageError(f"{label} line {number} is not an object")
        values.append(value)
    if not values:
        raise DirectorStageError(f"{label} is empty")
    return values


def _visual_art_direction(profile: Mapping[str, Any]) -> dict[str, Any]:
    look = profile.get("book_look")
    palettes = profile.get("palette_profiles")
    lights = profile.get("lighting_profiles")
    materials = profile.get("material_profiles")
    if not isinstance(look, Mapping) or not isinstance(palettes, list) or not palettes:
        raise DirectorStageError("visual profile has no usable book look or palette")
    if not isinstance(lights, list) or not lights or not isinstance(materials, list) or not materials:
        raise DirectorStageError("visual profile has no usable lighting or materials")
    palette = palettes[0]; light = lights[0]
    material_text = "; ".join(
        str(item) for group in materials if isinstance(group, Mapping)
        for item in group.get("materials", []) if isinstance(item, str) and item.strip()
    )
    if not material_text:
        raise DirectorStageError("visual profile material language is empty")
    style_ids = profile.get("style_reference_ids")
    if not isinstance(style_ids, list) or not style_ids:
        raise DirectorStageError("visual profile style references are missing")
    return {
        "visual_world": (
            f"{look.get('visual_world')}; period {look.get('period')}; geography {look.get('geography')}; "
            f"render balance {look.get('render_balance')}; emotional temperature {look.get('emotional_temperature')}"
        ),
        "palette": ", ".join(str(item) for item in palette.get("colors", [])),
        "texture": material_text,
        "lighting": {
            "key": str(light.get("key", "natural story-motivated key light")),
            "fill": str(light.get("fill", "restrained environmental fill")),
            "shadow": str(light.get("shadow", "deep but readable shadows")),
        },
        "painting_identity": "original literary cinematic realism with natural anatomy and a restrained painterly surface",
        "paint_handling": {
            "focal": "precise faces, hands when required, and story-critical objects",
            "accent": "controlled material texture on cloth, rope, wood, water and weather",
            "background": "period-credible atmospheric depth without generic decoration",
        },
        "reference_roles": {
            f"image_{index}": f"style only from {reference_id}; never copy identity, text, exact pose or layout"
            for index, reference_id in enumerate(style_ids, start=1)
        },
    }


def _anchor_context(
    profile: Mapping[str, Any], visual_assets: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    continuity: dict[str, str] = {}
    front_task: dict[str, str] = {}
    registered_ids = sorted({
        str(item.get("task_id"))
        for item in visual_assets.get("assets", [])
        if isinstance(item, Mapping) and isinstance(item.get("task_id"), str)
    })
    for anchor in profile.get("character_anchors", []):
        if not isinstance(anchor, Mapping):
            continue
        character_id = str(anchor.get("character_id", ""))
        anchor_id = str(anchor.get("anchor_id", ""))
        text = "; ".join([
            str(anchor.get("prompt_subject", "")),
            "invariants: " + ", ".join(map(str, anchor.get("invariants", []))),
            "wardrobe: " + ", ".join(map(str, anchor.get("wardrobe", []))),
            "forbidden changes: " + ", ".join(map(str, anchor.get("forbidden_changes", []))),
        ])
        preferred = f"ANCHOR_{character_id}_FRONT"
        candidates = [item for item in registered_ids if item.startswith(f"ANCHOR_{character_id}_")]
        if not candidates:
            raise DirectorStageError(f"character anchor has no registered identity asset: {character_id}")
        task_id = preferred if preferred in candidates else candidates[0]
        for key in (character_id, anchor_id):
            if key:
                continuity[key] = text
                front_task[key] = task_id
    return continuity, front_task


def _validate_storyboard(storyboard: Any, audio_meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(storyboard, list) or not storyboard:
        raise DirectorStageError("final audio storyboard is empty")
    body_start = float(audio_meta.get("opening", {}).get("bodyStart", -1))
    body_duration = float(audio_meta.get("body", {}).get("duration", -1))
    if body_start < 0 or body_duration <= 0:
        raise DirectorStageError("audio metadata has no real body timeline")
    previous_end = body_start
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(storyboard):
        if not isinstance(raw, Mapping):
            raise DirectorStageError(f"storyboard scene {index} is not an object")
        scene = dict(raw)
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id or scene_id in ids:
            raise DirectorStageError("storyboard scene IDs must be unique nonempty strings")
        ids.add(scene_id)
        start = float(scene.get("start", -1)); end = float(scene.get("end", -1)); duration = float(scene.get("duration", -1))
        if start + 1e-3 < previous_end or end <= start or abs((end - start) - duration) > 0.01:
            raise DirectorStageError(f"scene {scene_id} has a broken real-audio window")
        caption_ids = scene.get("captionIds")
        if not isinstance(caption_ids, list) or not caption_ids or not all(isinstance(item, str) and item for item in caption_ids):
            raise DirectorStageError(f"scene {scene_id} is not bound to captions")
        risk_flags = scene.get("riskFlags", [])
        if not isinstance(risk_flags, list) or any(item not in _HIGH_RISK for item in risk_flags):
            raise DirectorStageError(f"scene {scene_id} uses an unknown risk flag")
        motion = scene.get("motion")
        if motion not in _ALLOWED_MOTIONS:
            raise DirectorStageError(f"scene {scene_id} uses an unsupported HBG motion")
        if duration > 16.0:
            raise DirectorStageError(f"scene {scene_id} exceeds the 16-second hard limit")
        if duration > 12.0 and not scene.get("intentionalHold"):
            raise DirectorStageError(f"scene {scene_id} exceeds 12 seconds without an intentional hold")
        result.append(scene)
        previous_end = end
    expected_end = body_start + body_duration
    # Edge cues may end just before the encoded media tail (55 ms observed in
    # the real service). Keep the allowance bounded to a single video frame
    # pair rather than treating that encoder tail as missing storyboard time.
    if abs(previous_end - expected_end) > 0.1:
        raise DirectorStageError("storyboard does not cover the complete body audio timeline")
    return result


def _caption_map(audio_meta: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    captions = audio_meta.get("captions")
    if not isinstance(captions, list) or not captions:
        raise DirectorStageError("display-restored captions are missing")
    result: dict[str, dict[str, Any]] = {}
    for item in captions:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise DirectorStageError("caption record is invalid")
        result[str(item["id"])] = dict(item)
    return result


def _shot_size(scene: Mapping[str, Any]) -> str:
    risks = set(scene.get("riskFlags", []))
    if risks & {"hands", "phone", "tool_use", "reflection"}:
        return "story-critical close-up"
    participants = scene.get("participants", {})
    count = participants.get("count", 0) if isinstance(participants, Mapping) else 0
    if count == 0:
        return "wide environmental shot"
    if count >= 2:
        return "relationship medium-wide shot"
    return "medium character shot"


def _scene_mode(scene: Mapping[str, Any]) -> str:
    risks = set(scene.get("riskFlags", []))
    if risks & {"hands", "phone", "tool_use", "reflection", "death_climax", "animal_contact", "water_action"}:
        return "scene"
    participants = scene.get("participants", {})
    return "landscape" if isinstance(participants, Mapping) and participants.get("count") == 0 else "scene"


def _classifier_anchor_table(profile: Mapping[str, Any], key: str) -> dict[str, dict[str, Any]]:
    """Reshape a visual profile anchor list into the classifier's anchor table."""
    table: dict[str, dict[str, Any]] = {}
    for anchor in profile.get(key, []) or []:
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = str(anchor.get("anchor_id") or anchor.get("character_id") or "").strip()
        if not anchor_id:
            continue
        table[anchor_id] = {
            "prompt_subject": str(anchor.get("prompt_subject", "")),
            "natural_language": str(
                anchor.get("natural_language") or anchor.get("prompt_subject") or ""
            ),
            "aliases": [
                str(item)
                for item in (anchor.get("aliases") or [])
                if isinstance(item, str) and item.strip()
            ],
        }
    return table


def _scene_narrative_function(scene: Mapping[str, Any]) -> str:
    raw = scene.get("narrativeFunction") or scene.get("narrative_function")
    if not isinstance(raw, str) or not raw.strip():
        # Fail closed: the narrative register (theory / author_background /
        # closing / opening / transition vs plot) must be propagated explicitly
        # from the storyboard. Silently falling back to "plot" would erase the
        # distinction the caption grouping and bridge layers depend on.
        raise DirectorStageError(
            f"scene {scene.get('id')} is missing a required narrative_function; "
            "theory/author_background/closing distinctions must be propagated "
            "explicitly and cannot silently fall back to 'plot'"
        )
    raw = raw.strip()
    if raw not in NARRATIVE_FUNCTIONS:
        raise DirectorStageError(
            f"scene {scene.get('id')} has unsupported narrativeFunction {raw!r}; "
            f"expected one of {NARRATIVE_FUNCTIONS}"
        )
    return raw


def _scene_proposition(
    scene: Mapping[str, Any],
    profile: Mapping[str, Any],
    caption_texts: list[str],
    required: list[str],
    light: Mapping[str, Any],
    *,
    narrative_function: str = "",
    visual_mode: str = "",
    symbolic_mapping: Mapping[str, Any] | None = None,
    known_symbol_registry: Iterable[str] = (),
) -> VisualProposition:
    """Return the frozen visual proposition this scene's image must satisfy.

    A scene may carry an Agent-authored ``visualProposition``; otherwise the
    deterministic classifier derives one from the group captions, the
    Caption-Contract must-show set and approved visual anchors. Legacy scene
    description / ``semanticRationale`` fields are deliberately excluded: they
    are Beat-wide templates and are not evidence for the current Caption Group.

    An Agent-authored proposition receives NO special bypass (C-bridge): it is
    validated through the same seven-tuple contract as a derived one, against
    the current caption texts, source entities and the established symbol
    registry. A malformed or ungrounded authored proposition is rejected.
    """
    authored = scene.get("visualProposition") or scene.get("visual_proposition")
    if isinstance(authored, Mapping):
        prop = VisualProposition.from_mapping(authored)
        try:
            return validate_visual_proposition(
                prop,
                shot_id=str(scene.get("id", "")),
                caption_texts=caption_texts,
                source_entities=required,
                known_symbol_registry=known_symbol_registry,
            )
        except SemanticContractError as error:
            raise DirectorStageError(
                f"scene {scene.get('id')} agent-authored visual proposition is invalid: {error}"
            ) from error
    try:
        return classify_proposition(
            shot_id=str(scene.get("id", "")),
            caption_texts=caption_texts,
            description="",
            seed_rationale="",
            source_entities=required,
            character_anchors=_classifier_anchor_table(profile, "character_anchors"),
            scene_anchors=_classifier_anchor_table(profile, "scene_anchors"),
            object_anchors=_classifier_anchor_table(profile, "object_anchors"),
            lighting=str(light.get("lighting_id", "DUSK_SOFT")),
            palette=str(profile["palette_profiles"][0].get("palette_id", "EARTH_DUSK")),
            narrative_function=narrative_function,
            visual_mode=visual_mode,
            symbolic_mapping=symbolic_mapping,
        )
    except PropositionClassifierError as error:
        raise DirectorStageError(
            f"scene {scene.get('id')} cannot be given a defensible visual proposition: {error}"
        ) from error


def _persistent_contract_character_ids(contract: CaptionVisualContract) -> set[str]:
    """Return contract-approved persistent characters and reject split authority."""

    must_show_ids = {
        item.entity_id for item in contract.must_show
        if item.entity_id.startswith("C")
    }
    visible_ids = {
        str(item) for item in contract.scene_state.get("visible_character_ids", [])
        if str(item).startswith("C")
    }
    if must_show_ids and visible_ids and must_show_ids != visible_ids:
        raise DirectorStageError(
            f"caption contract {contract.caption_id} disagrees on persistent visible characters: "
            f"must_show={sorted(must_show_ids)}, scene_state={sorted(visible_ids)}"
        )
    return must_show_ids or visible_ids


def _approved_symbolic_mapping(
    profile: Mapping[str, Any], caption_texts: Iterable[str]
) -> Mapping[str, Any] | None:
    caption_blob = "".join(str(item) for item in caption_texts)
    raw_mappings = profile.get("symbolic_mappings", [])
    if raw_mappings is None:
        return None
    if not isinstance(raw_mappings, list):
        raise DirectorStageError("visual Profile symbolic_mappings must be an array")
    matches = [
        item for item in raw_mappings
        if isinstance(item, Mapping)
        and str(item.get("status", "")) == "approved"
        and str(item.get("source_concept", "")).strip()
        and str(item.get("source_concept", "")).strip() in caption_blob
    ]
    if len(matches) > 1:
        raise DirectorStageError("more than one approved symbolic mapping matches the Caption Group")
    return matches[0] if matches else None


def _contract_anchor_refs(
    scene: Mapping[str, Any],
    contracts: Iterable[CaptionVisualContract],
    continuity: Mapping[str, str],
    front_tasks: Mapping[str, str],
) -> list[str]:
    """FIX 4 (pilot R2): derive identity references from the Caption Contract.

    The Caption Visual Contract is authoritative: when it requires a persistent
    character, the Director takes the approved Visual Profile identity asset
    directly. The legacy Beat ``anchorRefs`` no longer vetoes the Contract; it
    may only contribute extra approved continuity context. The pipeline fails
    only when the required character genuinely has no approved identity asset.
    """

    approved_ids = set().union(*(_persistent_contract_character_ids(contract) for contract in contracts))
    scene_anchor_refs = [str(item) for item in scene.get("anchorRefs", []) if str(item).strip()]
    missing = sorted(
        entity_id for entity_id in approved_ids
        if entity_id not in continuity or entity_id not in front_tasks
    )
    if missing:
        raise DirectorStageError(
            f"scene {scene.get('id')} Caption Contract requires persistent characters "
            f"{missing} but no approved Visual Profile identity asset exists; create "
            "the missing anchor asset before generating"
        )
    # Contract-derived characters are the only identity references injected
    # into the prompt. The legacy Beat anchorRefs remain in the task record as
    # pure context evidence and never bias the frame (e.g. 老牛 must not become
    # a reference for 凤霞出嫁 merely because the old storyboard listed C010).
    # Scene order is preserved only as an ordering preference for the approved
    # contract characters; it can never add or remove an identity.
    ordered = [
        entity_id for entity_id in scene_anchor_refs
        if entity_id in approved_ids
    ]
    ordered.extend(
        entity_id for entity_id in sorted(approved_ids)
        if entity_id not in ordered
    )
    return ordered


def _merge_visual_event_state(
    contracts: Sequence[CaptionVisualContract],
) -> dict[str, Any] | None:
    """Merge per-caption Visual Event State into one group-level prompt record."""

    return merge_visual_event_states(contracts)


def _participant_constraint(
    contracts: Sequence[CaptionVisualContract],
) -> dict[str, Any] | None:
    """FIX A (pilot R2.1): merge the exact narrative participant constraint."""

    ids: list[str] = []
    for contract in contracts:
        for entity_id in contract.expected_visible_character_ids:
            if entity_id not in ids:
                ids.append(str(entity_id))
    if not ids:
        return None
    allow_unlisted = any(
        contract.allow_unlisted_narrative_characters for contract in contracts
    )
    return {
        "expected_visible_character_ids": ids,
        "expected_narrative_character_count": len(ids),
        "allow_unlisted_narrative_characters": bool(allow_unlisted),
    }


def _suppress_character_presence(visual_world: str) -> str:
    """Remove global 'characters are always present' language for non-Literal shots."""

    cleaned = str(visual_world or "").replace("人物渺小但始终在场", "")
    cleaned = cleaned.replace("；；", "；").replace(";;", ";")
    return cleaned.strip(" ；;，,　")


def _scene_reference_contract(
    root: Path,
    profile: Mapping[str, Any],
    scene: Mapping[str, Any],
    group_contract: Mapping[str, Any] | None,
    previous_scene: Mapping[str, Any] | None,
    vf_policy: str = "required",
) -> dict[str, Any] | None:
    """Declarative reference contract for a scene task, derived from the approved Visual
    Foundation. Returns None for legacy projects that have not built a foundation."""
    from book_video_factory.visual_foundation.manifests import load_foundation

    release_id = str(profile.get("release_id", ""))
    try:
        foundation = load_foundation(root, release_id)
    except Exception as error:
        if vf_policy == "required":
            raise DirectorStageError(f"visual foundation is required but invalid: {error}") from error
        return None
    character_ids = [str(item) for item in (group_contract or {}).get("primary_subject_ids", [])]
    known = {item.character_id: item for item in foundation["character_identities"]}
    characters = []
    for cid in character_ids:
        if cid not in known:
            continue
        identity = known[cid]
        characters.append({
            "character_id": cid,
            "life_stage": "",
            "apparent_age_range": "",
            "wardrobe_state": "",
            "narrative_role": identity.narrative_role,
            "gender_presentation": identity.gender_presentation,
        })
    location_id = ""
    location_reference_type = "none"
    if isinstance(group_contract, Mapping):
        loc = str(group_contract.get("location", "")).strip()
        for anchor in foundation["location_anchors"]:
            if anchor.location_id == loc or (loc and loc in anchor.aliases):
                location_id = anchor.location_id
                break
        if location_id:
            location_reference_type = "persistent_location_anchor"
        elif loc:
            location_reference_type = "generic_environment_reference"
    use_previous = False
    reason = ""
    if previous_scene is not None:
        prev_event = ""
        prev_state = previous_scene.get("event_state")
        if isinstance(prev_state, Mapping):
            prev_event = str(prev_state.get("action_predicate", ""))
        this_event = ""
        ves = (group_contract or {}).get("visual_event_state")
        if isinstance(ves, Mapping):
            this_event = str(ves.get("action_predicate", ""))
        prev_chars = {str(item) for item in previous_scene.get("anchor_refs", [])}
        this_chars = {str(item) for item in scene.get("anchorRefs", [])}
        same_location = str(previous_scene.get("location", "")) == str((group_contract or {}).get("location", ""))
        adjacent = abs(float(previous_scene.get("end", -1)) - float(scene.get("start", -2))) < 1.0
        if prev_event and prev_event == this_event and prev_chars == this_chars and same_location and adjacent:
            use_previous = True
            reason = "same event instance, same location, same characters, time-adjacent; continuity benefits"
    return {
        "schema_version": "visual-reference-contract.v1",
        "style": {"required": True},
        "identity": {"required_when_characters_present": True, "characters": characters},
        "location": {
            "location_id": location_id,
            "location_reference_type": location_reference_type,
            "required_persistent": False,
        },
        "continuity": {"use_previous_scene": use_previous, "required": False, "reason": reason},
    }


def _task_for_scene(
    scene: Mapping[str, Any],
    profile: Mapping[str, Any],
    visual_assets: Mapping[str, Any],
    captions: Mapping[str, Mapping[str, Any]],
    canvas: Mapping[str, Any],
    *,
    known_symbol_registry: Iterable[str] = (),
    caption_contracts: Mapping[str, CaptionVisualContract] | None = None,
    caption_group: Mapping[str, Any] | None = None,
    reference_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    continuity, front_tasks = _anchor_context(profile, visual_assets)
    caption_ids = list(scene["captionIds"])
    caption_texts = [str(captions[item]["text"]) for item in caption_ids]
    caption_text = " / ".join(caption_texts)
    # The Caption Visual Contract is the authoritative record of what the frame
    # MUST show. When it is in force (every remediated release carries
    # 04_audio/CAPTION_VISUAL_CONTRACT.json) we inject its must_show /
    # must_not_show into the prompt and bind its hash into the task so a caption
    # cannot be illustrated by a frame that ignores a named character or event.
    scene_contracts: list[CaptionVisualContract] = []
    if caption_contracts is not None:
        for cid in caption_ids:
            contract = caption_contracts.get(str(cid))
            if contract is None:
                raise DirectorStageError(f"scene {scene.get('id')} is missing caption visual contract for {cid}")
            scene_contracts.append(contract)
    group_id: str | None = None
    group_bindings: list[dict[str, Any]] = []
    group_sha: str | None = None
    if caption_group is not None:
        group_ids = [str(item) for item in caption_group.get("caption_ids", [])]
        group_bindings = [dict(item) for item in caption_group.get("contract_bindings", [])]
        group_id = str(caption_group.get("group_id", ""))
        group_sha = str(caption_group.get("caption_group_sha256", ""))
        if group_ids != caption_ids:
            raise DirectorStageError(f"scene {scene.get('id')} caption ids do not exactly match its caption group")
        if [str(item.get("caption_id", "")) for item in group_bindings] != caption_ids:
            raise DirectorStageError(f"scene {scene.get('id')} caption contract bindings do not exactly match its captions")
    narrative_function = _scene_narrative_function(scene)
    group_contract: dict[str, Any] | None = None
    if caption_group is not None and caption_contracts is not None:
        try:
            group_contract = aggregate_group_visual_contract(
                group=caption_group,
                contracts=caption_contracts,
                captions=captions,
            )
        except (CaptionGroupingError, KeyError, TypeError) as error:
            raise DirectorStageError(
                f"scene {scene.get('id')} cannot aggregate its Group Visual Contract: {error}"
            ) from error
        representative_core = str(caption_group.get("representative_visual_core", "")).strip()
        if representative_core:
            group_contract["group_visual_focus"] = representative_core
    must_show: list[str] = []
    must_not_show: list[str] = []
    if group_contract is not None:
        # Group Visual Contract decides what one image MUST show. Author
        # background keeps story characters as supporting context only.
        must_show = [
            str(item.get("natural_language", ""))
            for item in group_contract.get("must_show", [])
            if str(item.get("natural_language", "")).strip()
        ]
        must_not_show = [
            str(item.get("natural_language", ""))
            for item in group_contract.get("must_not_show", [])
            if str(item.get("natural_language", "")).strip()
        ]
    else:
        for contract in scene_contracts:
            for item in contract.must_show:
                if item.natural_language and item.natural_language not in must_show:
                    must_show.append(item.natural_language)
            for item in contract.must_not_show_as_primary:
                if item.natural_language and item.natural_language not in must_not_show:
                    must_not_show.append(item.natural_language)
    contract_without_subject = bool(scene_contracts) and not must_show
    caption_visual_contract_sha256: str | None = None
    if caption_group is not None:
        try:
            caption_visual_contract_sha256 = aggregate_contract_bindings_sha256(
                group_bindings, expected_caption_ids=caption_ids
            )
        except ContractBindingError as error:
            raise DirectorStageError(f"scene {scene.get('id')} has invalid contract bindings: {error}") from error
    elif scene_contracts:
        raise DirectorStageError(
            f"scene {scene.get('id')} has Caption Contracts but no exact Caption Group binding"
        )
    if scene_contracts:
        if narrative_function == "author_background":
            # Author-background frames never depict story characters, so no
            # persistent story identity anchor is injected.
            anchor_refs: list[str] = []
        else:
            anchor_refs = _contract_anchor_refs(scene, scene_contracts, continuity, front_tasks)
    else:
        anchor_refs = list(scene.get("anchorRefs", []))
    character_anchors = [continuity[item] for item in anchor_refs if item in continuity]
    identity_tasks = list(dict.fromkeys(front_tasks[item] for item in anchor_refs if item in front_tasks))
    # A v2 Caption Visual Contract is authoritative over the Beat template.
    # In particular, an abstract/theory caption with no text-grounded entity
    # must not regain a Beat person as the proposition or prompt subject.
    required = list(must_show) if scene_contracts else list(scene.get("requiredEntities", []))
    forbidden = [*profile.get("forbidden_traits", []), *scene.get("forbiddenEntities", [])]
    light = profile["lighting_profiles"][0]
    beat_ids = [
        str(item)
        for item in (scene.get("sourceBeatIds") or scene.get("source_beat_ids") or [])
        if str(item).strip()
    ]
    if not beat_ids:
        raise DirectorStageError(
            f"scene {scene.get('id')} declares no sourceBeatIds; the image task cannot be bound"
        )
    visual_modes = {contract.visual_mode for contract in scene_contracts}
    # Literal dominates inside one concrete event phase: the group frame is
    # concrete if any caption names a drawable referent.
    visual_mode = "literal" if "literal" in visual_modes else (next(iter(visual_modes)) if visual_modes else "")
    symbolic_mapping = (
        _approved_symbolic_mapping(profile, caption_texts)
        if visual_mode in {"symbolic", "symbolic_or_abstract"}
        else None
    )
    proposition = _scene_proposition(
        scene,
        profile,
        caption_texts,
        required,
        light,
        narrative_function=narrative_function,
        visual_mode=visual_mode,
        symbolic_mapping=symbolic_mapping,
        known_symbol_registry=known_symbol_registry,
    )
    # Semantic Shot Group (recalibration): the prompt proposition summarises
    # what ONE image must show for the whole group, instead of concatenating
    # every caption into the action. The [1/9 CAPTION] block still shows the
    # group's real captions; the proposition carries the group visual focus.
    group_visual_focus = (
        str(group_contract.get("group_visual_focus") or "")
        if group_contract is not None
        else ""
    )
    if group_visual_focus:
        proposition = replace(proposition, action=group_visual_focus)
    # FIX 1 (pilot R2): the group-level observable event state is bound into the
    # prompt and the image task so the frame must establish the event, not just
    # the nouns.
    # The Group Visual Contract is re-derived from the final group text, so the
    # prompt consumes that authoritative state instead of a stale caption-union.
    visual_event_state = (
        group_contract.get("visual_event_state")
        if group_contract is not None
        else _merge_visual_event_state(scene_contracts)
    )
    if (
        proposition.mode == "Literal"
        and visual_event_state is not None
        and not visual_event_state.get("required_observable_evidence")
    ):
        raise DirectorStageError(
            f"scene {scene.get('id')} is a concrete Literal proposition with no "
            "observable Visual Event State evidence; fail closed instead of "
            "generating a noun-only frame"
        )
    participant_constraint = _participant_constraint(scene_contracts)
    art_direction = _visual_art_direction(profile)
    if proposition.mode != "Literal":
        # FIX 2 (pilot R2): remove the global "characters are always present"
        # art-direction language that contradicted abstract/symbolic frames.
        art_direction = dict(art_direction)
        art_direction["visual_world"] = _suppress_character_presence(
            str(art_direction.get("visual_world", ""))
        )
    shot = {
        "shot_id": "SHOT_" + str(scene["id"]).upper().replace("-", "_").replace(".", "_"),
        "asset_tier": "narrative_scene",
        "subject": proposition.subject or caption_text,
        "action": proposition.action or caption_text,
        "shot_size": _shot_size(scene),
        "lens": "35mm" if _shot_size(scene).startswith("wide") else "50mm",
        "camera_angle": "eye level with story-motivated variation",
        "composition": "clear narrative hierarchy, readable silhouettes, and subtitle-safe lower frame",
        "depth": "story-critical entities readable; background simplified but period credible",
        "emotion": str(scene.get("captionIntent", caption_text)),
        "narration_text": caption_text,
        "semantic_entities": required,
        "forbidden_entities": forbidden,
        "scene_mode": "landscape" if contract_without_subject else _scene_mode(scene),
        "lighting_intent": {
            "key": str(light["key"]),
            "fill": str(light["fill"]),
            "shadow_readability": str(light["shadow"]),
            "temperature_role": "balanced",
        },
        "risk_flags": list(scene.get("riskFlags", [])),
    }
    if proposition.mode == "Symbolic":
        # A symbolic surrogate is the frame's visible object; stage it as the
        # primary object instead of a landscape that demands character presence.
        shot["semantic_entities"] = list(proposition.surrogate_objects)
        shot["scene_mode"] = "object"
    prompt = build_imagegen_prompt(
        art_direction,
        {**shot, "output_orientation": canvas["orientation"]},
        [{"continuity_anchor": item} for item in character_anchors],
    )
    prompt += (
        f"\nOutput canvas: native {canvas['orientation']} {canvas['width']}x{canvas['height']}; "
        "compose for this aspect ratio without rotation or embedded text."
    )
    try:
        priority_blocks = build_aligned_prompt_blocks(
            caption_text=caption_text,
            proposition=proposition,
            narrative_function=narrative_function,
            camera={
                "shot_size": shot["shot_size"],
                "lens": shot["lens"],
                "camera_angle": shot["camera_angle"],
                "composition": shot["composition"],
                "depth": shot["depth"],
            },
            style={
                "visual_world": art_direction["visual_world"],
                "palette": art_direction["palette"],
                "texture": art_direction["texture"],
            },
            forbidden_entities=forbidden,
            anchors=character_anchors,
            must_show=must_show,
            must_not_show=must_not_show,
            visual_event_state=visual_event_state,
            expected_visible_character_ids=(
                participant_constraint["expected_visible_character_ids"]
                if participant_constraint else ()
            ),
            expected_narrative_character_count=(
                participant_constraint["expected_narrative_character_count"]
                if participant_constraint else 0
            ),
            allow_unlisted_narrative_characters=(
                participant_constraint["allow_unlisted_narrative_characters"]
                if participant_constraint else True
            ),
        )
    except PromptSpecError as error:
        raise DirectorStageError(
            f"scene {scene.get('id')} cannot produce a caption-first prompt: {error}"
        ) from error
    # Caption-first: the priority blocks lead, the legacy art-direction body follows
    # as supporting detail. The model reads the top of the prompt hardest.
    prompt = "\n".join(priority_blocks) + "\n" + prompt
    narration_only_actions = [
        str(item).strip()
        for item in (caption_group or {}).get("narration_only_actions", [])
        if str(item).strip()
    ]
    if narration_only_actions:
        prompt += (
            "\nScene-continuity rule: keep one stable representative frame. "
            "These narrated micro-actions/event phases do not need literal simultaneous depiction: "
            + ", ".join(narration_only_actions)
            + ". Do not create an impossible multi-moment composite."
        )
    if reference_contract is not None:
        prompt += (
            "\nRENDER-STYLE LOCK: this frame must be rendered in the approved book visual "
            "language only (BOOK_VISUAL_PROFILE + the approved book Style Master). Do not "
            "switch into illustration, anime, oil painting, concept art, commercial poster, "
            "or fashion-photography rendering."
        )
    generation_mode = "single" if scene.get("riskFlags") else str(scene.get("generationMode", "single"))
    if generation_mode not in {"single", "2x2"}:
        raise DirectorStageError(f"scene {scene['id']} has unsupported generation mode")
    task_id = "SCENE_" + str(scene["id"]).upper().replace("-", "_").replace(".", "_")
    return {
        "schema_version": "production-image-task.v1",
        "task_id": task_id,
        "scene_id": scene["id"],
        "shot_id": shot["shot_id"],
        "release_id": profile["release_id"],
        "generation_lane": "host-imagegen",
        "generation_mode": generation_mode,
        "canvas": dict(canvas),
        "caption_ids": caption_ids,
        "caption_text": caption_text,
        "required_entities": required,
        "forbidden_entities": forbidden,
        "risk_flags": list(scene.get("riskFlags", [])),
        "anchor_refs": anchor_refs,
        "identity_reference_task_ids": identity_tasks,
        "style_reference_ids": list(profile["style_reference_ids"]),
        "palette_id": profile["palette_profiles"][0]["palette_id"],
        "lighting_id": profile["lighting_profiles"][0]["lighting_id"],
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "narrative_function": narrative_function,
        "source_beat_ids": beat_ids,
        "visual_proposition": proposition.to_dict(),
        "prompt_binding": compute_prompt_binding(
            caption_ids=caption_ids,
            caption_text=caption_text,
            proposition=proposition,
            prompt=prompt,
            scene_id=str(scene["id"]),
            beat_ids=beat_ids,
            shot_id=shot["shot_id"],
            caption_visual_contract_sha256=caption_visual_contract_sha256,
            group_id=group_id,
            caption_contract_bindings=group_bindings,
            caption_group_sha256=group_sha,
        ),
        "caption_visual_contract_sha256": caption_visual_contract_sha256,
        "contract_must_show": must_show,
        "contract_must_not_show": must_not_show,
        "visual_event_state": visual_event_state,
        "participant_constraint": participant_constraint,
        "group_visual_focus": group_visual_focus,
        "reference_contract": reference_contract,
        "output_target": f"assets/generated/scenes/{scene['id']}.png",
        "status": "planned",
    }


def _sheet_plan(tasks: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> dict[str, Any]:
    scene_by_id = {item["id"]: item for item in scenes}
    groups: list[dict[str, Any]] = []
    singles: list[dict[str, Any]] = []
    safe_buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}

    def add_group(items: list[dict[str, Any]]) -> None:
        group_id = f"SHEET_{len(groups)+1:04d}"
        groups.append({
            "sheet_id": group_id,
            "task_ids": [item["task_id"] for item in items],
            "output_target": f"assets/generated/sheets/{group_id}.png",
            "split_targets": [item["output_target"] for item in items],
        })

    for task in tasks:
        scene = scene_by_id[task["scene_id"]]
        high_risk = bool(task["risk_flags"]) or task["generation_mode"] == "single"
        signature = (
            tuple(task["anchor_refs"]), tuple(scene.get("participants", {}).get("allowed", [])),
            task["palette_id"], task["lighting_id"],
        )
        if high_risk:
            singles.append({"task_id": task["task_id"], "reason": "high_risk_or_single"})
            continue
        safe_buckets.setdefault(signature, []).append(task)
    for bucket in safe_buckets.values():
        while len(bucket) >= 4:
            add_group(bucket[:4])
            del bucket[:4]
        singles.extend({"task_id": item["task_id"], "reason": "incomplete_safe_sheet_group"} for item in bucket)
    return {
        "schema_version": "sheet-map.v1",
        "sheet_groups": groups,
        "single_tasks": singles,
        "sheet_task_count": sum(len(item["task_ids"]) for item in groups),
        "single_task_count": len(singles),
    }


def _scene_for_caption_group(
    group: Mapping[str, Any],
    captions: Mapping[str, Mapping[str, Any]],
    upstream_by_caption: Mapping[str, Mapping[str, Any]],
    caption_contracts: Mapping[str, Any],
) -> dict[str, Any]:
    caption_ids = [str(item) for item in group.get("caption_ids", [])]
    group_id = str(group.get("group_id", ""))
    if not group_id or not caption_ids:
        raise DirectorStageError("caption grouping audit contains an empty group")
    missing = [caption_id for caption_id in caption_ids if caption_id not in captions]
    if missing:
        raise DirectorStageError(f"caption group {group_id} references unknown captions: {missing}")
    missing_upstream = [caption_id for caption_id in caption_ids if caption_id not in upstream_by_caption]
    if missing_upstream:
        raise DirectorStageError(
            f"caption group {group_id} lacks upstream storyboard evidence for {missing_upstream}"
        )
    upstream_scenes = list(dict.fromkeys(
        str(upstream_by_caption[caption_id]["id"]) for caption_id in caption_ids
    ))
    scene_by_id = {
        str(upstream_by_caption[caption_id]["id"]): upstream_by_caption[caption_id]
        for caption_id in caption_ids
    }
    evidence = [scene_by_id[scene_id] for scene_id in upstream_scenes]

    def ordered_union(field: str) -> list[str]:
        return list(dict.fromkeys(
            str(item)
            for scene in evidence
            for item in scene.get(field, [])
            if str(item).strip()
        ))

    start = float(captions[caption_ids[0]]["start"])
    end = float(captions[caption_ids[-1]]["end"])
    if abs(float(group.get("start", start)) - start) > 1e-6 or abs(float(group.get("end", end)) - end) > 1e-6:
        raise DirectorStageError(f"caption group {group_id} time window is stale")
    risk_flags = ordered_union("riskFlags")
    allowed = list(dict.fromkeys(
        str(character_id)
        for caption_id in caption_ids
        for character_id in caption_contracts[caption_id].scene_state.get("visible_character_ids", [])
        if str(character_id).strip()
    ))
    forbidden_participants = list(dict.fromkeys(
        str(item)
        for scene in evidence
        for item in scene.get("participants", {}).get("forbidden", [])
        if str(item).strip()
    ))
    span_id = str(group.get("span_id", ""))
    scene_id = (
        f"scene-continuity-{span_id.lower()}"
        if span_id
        else f"caption-group-{group_id.lower()}"
    )
    return {
        "id": scene_id,
        "sourceSceneIds": upstream_scenes,
        "sourceShotIds": upstream_scenes,
        "sourceBeatIds": ordered_union("sourceBeatIds"),
        "chapter": int(evidence[0]["chapter"]),
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "captionIds": caption_ids,
        "cue": str(captions[caption_ids[0]]["text"]),
        "description": "",
        "semanticRationale": "",
        "requiredEntities": ordered_union("requiredEntities"),
        "forbiddenEntities": ordered_union("forbiddenEntities"),
        "riskFlags": risk_flags,
        "anchorRefs": ordered_union("anchorRefs"),
        "participants": {
            "count": len(allowed),
            "mode": "environmental" if not allowed else ("single_character" if len(allowed) == 1 else "multi_character"),
            "allowed": allowed,
            "forbidden": forbidden_participants,
        },
        "motion": str(evidence[0]["motion"]),
        "generationMode": "single" if risk_flags else str(evidence[0].get("generationMode", "single")),
        "narrativeFunction": str(group.get("narrative_function", "")),
        "sceneContinuitySpanId": span_id,
    }


def _production_group_for_span(
    span: Mapping[str, Any],
    groups_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate fine-grained Caption Groups into one final image unit."""

    span_id = str(span.get("span_id", ""))
    continuity = {"spans": [span]}
    grouping = {"groups": list(groups_by_id.values())}
    try:
        return build_span_review_groups(continuity=continuity, grouping=grouping)[span_id]
    except (SceneContinuityError, KeyError) as error:
        raise DirectorStageError(f"cannot build production group for span {span_id}: {error}") from error


def _scene_for_beat(
    beat: Mapping[str, Any],
    *,
    captions: Mapping[str, Mapping[str, Any]],
    caption_contracts: Mapping[str, Any],
    caption_group: Mapping[str, Any],
) -> dict[str, Any]:
    """Map one approved VisualBeat into the director scene shape (P0-3).

    VISUAL_TIMELINE beats are the authoritative shot grid; each beat becomes
    exactly one scene (== one production image task). The scene dict mirrors
    ``_scene_for_caption_group``'s shape so the shared emit/task pipeline
    consumes it unchanged. The beat carries no upstream storyboard evidence,
    so source ids are empty and chapter defaults to 0.
    """

    caption_ids = [str(item) for item in beat.get("caption_ids", [])]
    beat_id = str(beat.get("beat_id", ""))
    if not beat_id or not caption_ids:
        raise DirectorStageError(f"visual beat has no beat_id or caption_ids: {beat!r}")
    missing = [cid for cid in caption_ids if cid not in captions]
    if missing:
        raise DirectorStageError(f"visual beat {beat_id} references unknown captions: {missing}")
    contracts = [caption_contracts[cid] for cid in caption_ids]
    allowed = list(dict.fromkeys(
        str(character_id)
        for contract in contracts
        for character_id in contract.scene_state.get("visible_character_ids", [])
        if str(character_id).strip()
    ))
    narrative_function = _scene_narrative_function(caption_group)
    span_id = str(caption_group.get("span_id", ""))
    scene_id = f"visual-beat-{beat_id.lower()}"
    return {
        "id": scene_id,
        "sourceSceneIds": [],
        "sourceShotIds": [],
        "sourceBeatIds": [beat_id],
        "chapter": int(caption_group.get("chapter", 0)),
        "start": float(beat.get("start", 0.0)),
        "end": float(beat.get("end", 0.0)),
        "duration": round(float(beat.get("duration", 0.0)), 3),
        "captionIds": caption_ids,
        "cue": str((beat.get("caption_texts") or [""])[0]),
        "description": "",
        "semanticRationale": "",
        "requiredEntities": [],
        "forbiddenEntities": [],
        "riskFlags": [],
        "anchorRefs": [],
        "participants": {
            "count": len(allowed),
            "mode": "environmental" if not allowed else ("single_character" if len(allowed) == 1 else "multi_character"),
            "allowed": allowed,
            "forbidden": [],
        },
        "motion": str(beat.get("motion", "hold")) or "hold",
        "generationMode": "single",
        "narrativeFunction": narrative_function,
        "sceneContinuitySpanId": span_id,
    }


def _scenes_from_visual_timeline(
    timeline: Mapping[str, Any],
    *,
    captions: Mapping[str, Mapping[str, Any]],
    caption_contracts: Mapping[str, Any],
    group_by_caption: Mapping[str, Mapping[str, Any]],
    span_by_caption: Mapping[str, str],
    span_narrative_by_id: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build (scenes, per-scene caption_groups) from the approved VISUAL_TIMELINE beats.

    Each beat == one scene (one image task). A beat's production 'group' is
    synthesized by unioning the fine-grained caption groups that intersect the
    beat's caption_ids; its span_id and narrative_function come from the
    caption->span map (VISUAL_TIMELINE beat JSON does NOT carry source_span_id).
    Fail-closed: (a) every caption must exist; (b) a beat whose captions span
    MORE THAN ONE distinct span, or a caption in no span, cannot be bound to
    exactly one production group -> error.
    """

    beats = timeline.get("beats")
    if not isinstance(beats, list) or not beats:
        raise DirectorStageError("visual timeline contains no beats")
    scenes: list[dict[str, Any]] = []
    scene_groups: list[dict[str, Any]] = []
    for beat in beats:
        caption_ids = [str(item) for item in beat.get("caption_ids", [])]
        if not caption_ids:
            raise DirectorStageError(f"visual beat {beat.get('beat_id')} has no caption_ids")
        missing = [cid for cid in caption_ids if cid not in captions]
        if missing:
            raise DirectorStageError(f"visual beat {beat['beat_id']} references unknown captions: {missing}")
        beat_spans = sorted({span_by_caption[cid] for cid in caption_ids if span_by_caption.get(cid)})
        if len(beat_spans) != 1:
            raise DirectorStageError(
                f"visual beat {beat['beat_id']} must map to exactly one scene continuity span, "
                f"got {beat_spans or 'none'}"
            )
        span_id = beat_spans[0]
        members = [group_by_caption[cid] for cid in caption_ids if group_by_caption.get(cid)]
        source_group_ids = list(dict.fromkeys(
            str(g.get("group_id", "")) for g in members if str(g.get("group_id", ""))
        ))
        narrative_function = str(
            span_narrative_by_id.get(span_id) or (members[0].get("narrative_function", "") if members else "")
        )
        beat_group: dict[str, Any] = {
            "group_id": str(beat.get("beat_id", "")),
            "span_id": span_id,
            "source_group_ids": source_group_ids,
            "caption_ids": caption_ids,
            "start": float(beat.get("start", 0.0)),
            "end": float(beat.get("end", 0.0)),
            "duration": float(beat.get("duration", 0.0)),
            "narrative_function": narrative_function,
            "location": str(beat.get("location_id", "")),
            "core_participants": list(beat.get("participants", [])),
            "representative_visual_core": str(beat.get("visual_proposition", "")),
            "contract_bindings": [
                {"caption_id": cid, "content_sha256": caption_contracts[cid].content_sha256()}
                for cid in caption_ids
            ],
        }
        beat_group["caption_group_sha256"] = hashlib.sha256(
            json.dumps(beat_group, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        scene = _scene_for_beat(
            beat, captions=captions, caption_contracts=caption_contracts, caption_group=beat_group,
        )
        scenes.append(scene)
        scene_groups.append(beat_group)
    return scenes, scene_groups


def _expected(root: Path) -> tuple[dict[str, bytes], str, str]:
    audio_manifest_path = _project_file(root, "04_audio/AUDIO_STAGE_MANIFEST.json", "audio stage manifest")
    audio_manifest_probe = _load_json(audio_manifest_path, "audio stage manifest")
    release_id = audio_manifest_probe.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise DirectorStageError("audio stage release_id is invalid")
    from book_video_factory.style_profiles import project_workflow
    from book_video_factory.visual_foundation.approval import verify_visual_foundation_approval
    from book_video_factory.visual_foundation.contracts import VisualFoundationError
    try:
        vf_policy = project_workflow(root)["visual_foundation_policy"]
    except Exception as error:
        raise DirectorStageError(f"cannot resolve visual_foundation_policy: {error}") from error
    if vf_policy == "required":
        try:
            verify_visual_foundation_approval(root, release_id)
        except VisualFoundationError as error:
            raise DirectorStageError(
                f"visual_foundation_policy=required but the visual foundation is missing, "
                f"unapproved, or stale: {error}"
            ) from error
    status = audio_stage_status(root, release_id)
    if status != "ready_for_image_task_planning":
        raise DirectorStageError(f"Phase 4 final audio gate is not current: {status}")
    paths = {
        "audio_manifest": _project_file(root, "04_audio/AUDIO_STAGE_MANIFEST.json", "audio stage manifest"),
        "caption_visual_contract": _project_file(
            root, "04_audio/CAPTION_VISUAL_CONTRACT.json", "caption visual contract"
        ),
        "caption_grouping_audit": _project_file(
            root, "04_audio/CAPTION_GROUPING_AUDIT.json", "caption grouping audit"
        ),
        "scene_continuity_spans": _project_file(
            root, "04_audio/SCENE_CONTINUITY_SPANS.json", "scene continuity spans"
        ),
    }
    try:
        grouping = load_current_caption_grouping_document(root)
        continuity = load_current_scene_continuity_document(root)
        from book_video_factory.semantic_alignment.caption_contract import load_caption_visual_contract_document

        caption_contracts = load_caption_visual_contract_document(paths["caption_visual_contract"])
    except (CaptionGroupingError, SceneContinuityError, OSError, ValueError) as error:
        raise DirectorStageError(
            f"current caption visual contract/grouping/scene continuity is unavailable: {error}"
        ) from error
    groups = grouping.get("groups")
    if not isinstance(groups, list) or not groups:
        raise DirectorStageError("caption grouping audit contains no groups")
    group_by_caption: dict[str, Mapping[str, Any]] = {}
    for group in groups:
        if not isinstance(group, Mapping):
            raise DirectorStageError("caption grouping audit contains an invalid group")
        for caption_id in group.get("caption_ids", []):
            key = str(caption_id)
            if not key or key in group_by_caption:
                raise DirectorStageError("caption grouping audit has missing or duplicate caption coverage")
            group_by_caption[key] = group
    groups_by_id = {str(group.get("group_id", "")): group for group in groups}
    spans = continuity.get("spans")
    if not isinstance(spans, list) or not spans:
        raise DirectorStageError("scene continuity document contains no spans")
    production_groups = [_production_group_for_span(span, groups_by_id) for span in spans]
    paths.update({
        "audio_meta": _project_file(root, "audio_meta.json", "audio metadata"),
        "storyboard": _project_file(root, "STORYBOARD.json", "final audio storyboard"),
        "bindings": _project_file(root, "04_audio/CAPTION_BINDINGS.json", "caption bindings"),
        "visual_profile": _project_file(root, "03_images_生成图片/BOOK_VISUAL_PROFILE.json", "visual profile"),
        "visual_approval": _project_file(root, "03_images_生成图片/ANCHOR_APPROVAL.json", "visual approval"),
        "visual_assets": _project_file(root, "03_images_生成图片/VISUAL_ASSET_MANIFEST.json", "visual asset manifest"),
        "hbg_style": _project_file(root, "HBG_STYLE.json", "HBG style"),
    })
    audio_manifest = _load_json(paths["audio_manifest"], "audio stage manifest")
    audio_meta = _load_json(paths["audio_meta"], "audio metadata")
    storyboard = _validate_storyboard(_load_json(paths["storyboard"], "storyboard"), audio_meta)
    profile = _load_json(paths["visual_profile"], "visual profile")
    visual_assets = _load_json(paths["visual_assets"], "visual asset manifest")
    hbg_style = _load_json(paths["hbg_style"], "HBG style")
    try:
        canvas = validate_orientation_contract(
            str(hbg_style.get("orientation", "")),
            {
                **(hbg_style.get("canvas") if isinstance(hbg_style.get("canvas"), Mapping) else {}),
                "orientation": hbg_style.get("orientation"),
            },
        )
    except OrientationError as error:
        raise DirectorStageError(f"HBG style orientation is invalid: {error}") from error
    if profile.get("release_id") != audio_manifest.get("release_id"):
        raise DirectorStageError("audio and visual releases differ")
    captions = _caption_map(audio_meta)
    upstream_by_caption: dict[str, Mapping[str, Any]] = {}
    for upstream_scene in storyboard:
        for caption_id in upstream_scene["captionIds"]:
            key = str(caption_id)
            if key not in captions:
                raise DirectorStageError(f"scene {upstream_scene['id']} references unknown caption {key}")
            if key in upstream_by_caption:
                raise DirectorStageError(f"caption {key} appears in more than one upstream storyboard scene")
            upstream_by_caption[key] = upstream_scene
    group_scenes = [
        _scene_for_caption_group(group, captions, upstream_by_caption, caption_contracts)
        for group in production_groups
    ]
    scenes: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    used_group_ids: set[str] = set()
    # Established symbol registry: surrogates declared by PRIOR scenes become
    # legitimate stand-ins for later Symbolic propositions (G8/G9). An empty
    # registry fails any Symbolic proposition, so order matters and the first
    # occurrence of a trope must be seeded by a registered trope / hash-bound
    # symbol upstream of this loop.
    # FIX 2 (pilot R2): the project's approved Visual Profile symbolic mappings
    # are part of the established symbol system and seed the registry directly.
    established_symbols: set[str] = {
        str(item.get("surrogate_object", "")).strip()
        for item in profile.get("symbolic_mappings", [])
        if isinstance(item, Mapping)
        and str(item.get("status", "")) == "approved"
        and str(item.get("surrogate_object", "")).strip()
    }
    visualize_path = root / "04_audio" / "VISUAL_TIMELINE.json"
    has_visual_timeline = visualize_path.is_file() and not visualize_path.is_symlink()

    def _emit_scene(scene: Mapping[str, Any], scene_group: Mapping[str, Any]) -> None:
        scene_caption_ids = [str(item) for item in scene["captionIds"]]
        if [str(item) for item in scene_group.get("caption_ids", [])] != scene_caption_ids:
            raise DirectorStageError(f"scene {scene['id']} is not exactly one persisted scene continuity span")
        group_id = str(scene_group.get("group_id", ""))
        if not group_id or group_id in used_group_ids:
            raise DirectorStageError(f"scene continuity span {group_id or '?'} does not map to exactly one image task")
        caption_text = " / ".join(str(captions[item]["text"]) for item in scene["captionIds"])
        group_contract = aggregate_group_visual_contract(
            group=scene_group,
            contracts=caption_contracts,
            captions=captions,
        )
        group_contract["group_visual_focus"] = str(
            scene_group.get("representative_visual_core") or group_contract.get("group_visual_focus") or ""
        )
        previous_scene = scenes[-1] if scenes else None
        reference_contract = _scene_reference_contract(
            root, profile, scene, group_contract, previous_scene, vf_policy
        )
        scenes.append({
            "scene_id": scene["id"],
            "source_beat_ids": list(scene.get("sourceBeatIds", [])),
            "chapter": int(scene["chapter"]),
            "start": float(scene["start"]),
            "end": float(scene["end"]),
            "duration": float(scene["duration"]),
            "caption_ids": list(scene["captionIds"]),
            "caption_text": caption_text,
            "narrative_cue": str(scene.get("cue", "")),
            "visual_description": str(scene.get("description", "")),
            "semantic_rationale": str(scene.get("semanticRationale", "")),
            "source_scene_ids": list(scene.get("sourceSceneIds", [])),
            "source_shot_ids": list(scene.get("sourceShotIds", [])),
            "required_entities": list(scene.get("requiredEntities", [])),
            "forbidden_entities": list(scene.get("forbiddenEntities", [])),
            "risk_flags": list(scene.get("riskFlags", [])),
            "anchor_refs": list(scene.get("anchorRefs", [])),
            "participants": dict(scene.get("participants", {})),
            "motion": str(scene["motion"]),
            "generation_mode": "single" if scene.get("riskFlags") else str(scene.get("generationMode", "single")),
            "location": str((group_contract or {}).get("location", "")),
            "event_state": dict((group_contract or {}).get("visual_event_state") or {}),
            "scene_continuity_span_id": str(scene_group.get("span_id", "")),
            "source_caption_group_ids": list(scene_group.get("source_group_ids", [])),
            "narration_only_actions": list(scene_group.get("narration_only_actions", [])),
        })
        task = _task_for_scene(
            scene,
            profile,
            visual_assets,
            captions,
            canvas,
            known_symbol_registry=tuple(established_symbols),
            caption_contracts=caption_contracts,
            caption_group=scene_group,
            reference_contract=reference_contract,
        )
        try:
            validate_production_image_task(task)
        except ProductionTaskValidationError as error:
            raise DirectorStageError(f"production image task is invalid: {error}") from error
        tasks.append(task)
        used_group_ids.add(group_id)
        vp = task.get("visual_proposition") or {}
        established_symbols.update(str(item) for item in (vp.get("surrogate_objects") or ()))

    if has_visual_timeline:
        timeline_doc = _load_json(visualize_path, "visual timeline")
        if timeline_doc.get("schema_version") != "visual-timeline.v1":
            raise DirectorStageError(
                f"VISUAL_TIMELINE.json must be visual-timeline.v1, got {timeline_doc.get('schema_version')!r}"
            )
        # caption -> span 反查表（VISUAL_TIMELINE beat 不携带 source_span_id）
        all_span_groups = build_span_review_groups(continuity=continuity, grouping=grouping)
        span_by_caption = {
            cid: span_id
            for span_id, payload in all_span_groups.items()
            for cid in payload.get("caption_ids", [])
        }
        span_narrative_by_id = {
            str(span.get("span_id", "")): str(span.get("narrative_function", ""))
            for span in (continuity.get("spans") or []) if str(span.get("span_id", ""))
        }
        beat_scenes, beat_scene_groups = _scenes_from_visual_timeline(
            timeline_doc, captions=captions, caption_contracts=caption_contracts,
            group_by_caption=group_by_caption, span_by_caption=span_by_caption,
            span_narrative_by_id=span_narrative_by_id,
        )
        raw_scenes = beat_scenes
        for scene, scene_group in zip(beat_scenes, beat_scene_groups):
            _emit_scene(scene, scene_group)
        expected_groups = {str(item.get("group_id", "")) for item in beat_scene_groups}
    else:
        raw_scenes = group_scenes
        for scene, scene_group in zip(group_scenes, production_groups):
            _emit_scene(scene, scene_group)
        expected_groups = {str(item.get("group_id", "")) for item in production_groups}
    if used_group_ids != expected_groups:
        raise DirectorStageError("scene continuity spans are missing from the production image task coverage")
    durations = [item["duration"] for item in scenes]
    timeline = {
        "schema_version": "director-timeline.v1",
        "release_id": audio_manifest["release_id"],
        "audio_stage_manifest_sha256": sha256_file(paths["audio_manifest"]),
        "audio_meta_sha256": sha256_file(paths["audio_meta"]),
        "caption_bindings_sha256": sha256_file(paths["bindings"]),
        "caption_visual_contract_sha256": sha256_file(paths["caption_visual_contract"]),
        "caption_grouping_audit_sha256": sha256_file(paths["caption_grouping_audit"]),
        "scene_continuity_spans_sha256": sha256_file(paths["scene_continuity_spans"]),
        "visual_profile_sha256": sha256_file(paths["visual_profile"]),
        "visual_approval_sha256": sha256_file(paths["visual_approval"]),
        "scene_count": len(scenes),
        "median_scene_duration": round(statistics.median(durations), 3),
        "body_start": float(audio_meta["opening"]["bodyStart"]),
        "body_duration": float(audio_meta["body"]["duration"]),
        "scenes": scenes,
    }
    motion = {
        "schema_version": "motion-manifest.v1",
        "release_id": audio_manifest["release_id"],
        "director_timeline_sha256": _sha_bytes(_pretty(timeline)),
        "motions": [
            {
                "scene_id": item["scene_id"],
                "start": item["start"],
                "end": item["end"],
                "duration": item["duration"],
                "type": item["motion"],
                "implementation": "static-hard-cut",
            }
            for item in scenes
        ],
    }
    sheet_map = _sheet_plan(tasks, raw_scenes)
    task_bytes = b"".join(_canonical(item) + b"\n" for item in tasks)
    prompt_lines = ["# Production Image Prompts", "", "> Generated deterministically from the approved visual profile and real-audio director timeline.", ""]
    for task in tasks:
        prompt_lines.extend([
            f"## {task['task_id']} · {task['scene_id']}",
            "",
            f"- Caption: {task['caption_text']}",
            f"- Mode: {task['generation_mode']}",
            f"- Risk flags: {', '.join(task['risk_flags']) if task['risk_flags'] else 'none'}",
            f"- Style references: {', '.join(task['style_reference_ids'])}",
            f"- Identity references: {', '.join(task['identity_reference_task_ids']) if task['identity_reference_task_ids'] else 'none'}",
            f"- Output: `{task['output_target']}`",
            "",
            "```text",
            task["prompt"],
            "```",
            "",
        ])
    prompts = ("\n".join(prompt_lines).rstrip() + "\n").encode("utf-8")
    payloads = {
        f"{_OUTPUT_DIR}/DIRECTOR_TIMELINE.json": _pretty(timeline),
        f"{_OUTPUT_DIR}/MOTION_MANIFEST.json": _pretty(motion),
        f"{_OUTPUT_DIR}/IMAGE_TASKS.jsonl": task_bytes,
        f"{_OUTPUT_DIR}/PROMPTS.md": prompts,
        f"{_OUTPUT_DIR}/SHEET_MAP.json": _pretty(sheet_map),
    }
    inputs = {name: sha256_file(path) for name, path in paths.items()}
    input_digest = _sha_bytes(_canonical(inputs))
    return payloads, input_digest, sha256_file(paths["audio_manifest"])


def _verify_existing(root: Path, manifest: Mapping[str, Any], payloads: Mapping[str, bytes], input_digest: str) -> bool:
    if manifest.get("schema_version") != "director-stage-manifest.v1" or manifest.get("input_digest") != input_digest:
        raise DirectorStageConflict("existing director manifest is bound to different inputs")
    hashes = manifest.get("output_hashes")
    if not isinstance(hashes, Mapping) or set(hashes) != set(payloads):
        raise DirectorStageConflict("existing director manifest output table is invalid")
    for relative, expected_bytes in payloads.items():
        path = _project_file(root, relative, "director output")
        expected = _sha_bytes(expected_bytes)
        if hashes.get(relative) != expected or sha256_file(path) != expected or path.read_bytes() != expected_bytes:
            raise DirectorStageConflict(f"director output was modified: {relative}")
    stage_relative = manifest.get("stage_manifest_path")
    if not isinstance(stage_relative, str):
        raise DirectorStageConflict("director stage manifest path is invalid")
    stage_path = _project_file(root, stage_relative, "director stage manifest")
    if sha256_file(stage_path) != manifest.get("stage_manifest_sha256"):
        raise DirectorStageConflict("director stage manifest was modified")
    return True


def compile_director_stage(project: Path) -> DirectorStageResult:
    root = project.expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise DirectorStageError("project root must be a real directory")
    manifest_path = safe_project_output(root, Path(f"{_OUTPUT_DIR}/DIRECTOR_STAGE_MANIFEST.json"))
    try:
        payloads, input_digest, audio_manifest_sha = _expected(root)
    except DirectorStageError as error:
        if manifest_path.exists():
            raise DirectorStageConflict(f"upstream evidence changed after director compilation: {error}") from error
        raise
    if manifest_path.exists():
        manifest = _load_json(manifest_path, "director stage manifest")
        _verify_existing(root, manifest, payloads, input_digest)
        return DirectorStageResult(
            "unchanged", manifest_path, root / manifest["stage_manifest_path"],
            "awaiting_scene_assets", audio_manifest_sha,
        )
    existing = [relative for relative in payloads if (root / relative).exists()]
    if existing:
        raise DirectorStageConflict(f"unmanaged director outputs already exist: {existing}")

    stage_relative = f"manifests/stages/director/director-{input_digest[:16]}.json"
    output_hashes = {relative: _sha_bytes(data) for relative, data in payloads.items()}
    stage = {
        "schema_version": "1.0",
        "manifest_id": f"director-{input_digest[:16]}",
        "project_id": root.name,
        "stage": "director",
        "release_id": _load_json(root / "04_audio/AUDIO_STAGE_MANIFEST.json", "audio manifest")["release_id"],
        "producer": {"tool": "book-video-factory-director-stage"},
        "status": "success",
        "inputs": {"digest": input_digest, "audio_stage_manifest_sha256": audio_manifest_sha},
        "outputs": [
            {"path": relative, "bytes": len(data), "sha256": output_hashes[relative]}
            for relative, data in sorted(payloads.items())
        ],
        "checks": [
            {"id": "real_audio_timeline", "result": "pass", "severity": "error"},
            {"id": "caption_visual_binding", "result": "pass", "severity": "error"},
            {"id": "high_risk_single_routing", "result": "pass", "severity": "error"},
            {"id": "hbg_motion_allowlist", "result": "pass", "severity": "error"},
        ],
    }
    stage_bytes = _pretty(stage)
    manifest = {
        "schema_version": "director-stage-manifest.v1",
        "release_id": stage["release_id"],
        "input_digest": input_digest,
        "audio_stage_manifest_sha256": audio_manifest_sha,
        "output_hashes": output_hashes,
        "stage_manifest_path": stage_relative,
        "stage_manifest_sha256": _sha_bytes(stage_bytes),
        "next_stage_status": "awaiting_scene_assets",
    }
    with tempfile.TemporaryDirectory(prefix="book-video-director-", dir=root.parent) as temp:
        staging = Path(temp)
        for relative, data in {**payloads, stage_relative: stage_bytes, f"{_OUTPUT_DIR}/DIRECTOR_STAGE_MANIFEST.json": _pretty(manifest)}.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        published: list[Path] = []
        try:
            for relative in [*payloads, stage_relative, f"{_OUTPUT_DIR}/DIRECTOR_STAGE_MANIFEST.json"]:
                target = safe_project_output(root, Path(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / relative, target)
                published.append(target)
        except Exception:
            for path in reversed(published):
                path.unlink(missing_ok=True)
            raise
    return DirectorStageResult("created", manifest_path, root / stage_relative, "awaiting_scene_assets", audio_manifest_sha)
