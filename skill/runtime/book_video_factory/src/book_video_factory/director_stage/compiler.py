from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from book_video_factory.audio_stage.status import audio_stage_status
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.orientation import OrientationError, validate_orientation_contract
from book_video_factory.semantic_alignment.caption_grouping import NARRATIVE_FUNCTIONS
from book_video_factory.semantic_alignment.classifier import (
    PropositionClassifierError,
    classify_proposition,
)
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PromptSpecError,
    build_aligned_prompt_blocks,
    compute_prompt_binding,
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
_ALLOWED_MOTIONS = {"zoom-in", "zoom-out", "pan-left", "pan-right", "hold"}
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
) -> VisualProposition:
    """Return the frozen visual proposition this scene's image must satisfy.

    A scene may carry an Agent-authored ``visualProposition``; otherwise the
    deterministic classifier derives one from the captions, the scene
    description and the registered anchors. The scene's template
    ``semanticRationale`` is never used as the proposition rationale, because it
    is boilerplate and cannot justify anything.
    """
    authored = scene.get("visualProposition") or scene.get("visual_proposition")
    if isinstance(authored, Mapping):
        return VisualProposition.from_mapping(authored)
    try:
        return classify_proposition(
            shot_id=str(scene.get("id", "")),
            caption_texts=caption_texts,
            description=str(scene.get("description", "")),
            seed_rationale=str(scene.get("semanticRationale", "")),
            source_entities=required,
            character_anchors=_classifier_anchor_table(profile, "character_anchors"),
            scene_anchors=_classifier_anchor_table(profile, "scene_anchors"),
            object_anchors=_classifier_anchor_table(profile, "object_anchors"),
            lighting=str(light.get("lighting_id", "DUSK_SOFT")),
            palette=str(profile["palette_profiles"][0].get("palette_id", "EARTH_DUSK")),
        )
    except PropositionClassifierError as error:
        raise DirectorStageError(
            f"scene {scene.get('id')} cannot be given a defensible visual proposition: {error}"
        ) from error


def _task_for_scene(
    scene: Mapping[str, Any],
    profile: Mapping[str, Any],
    visual_assets: Mapping[str, Any],
    captions: Mapping[str, Mapping[str, Any]],
    canvas: Mapping[str, Any],
) -> dict[str, Any]:
    continuity, front_tasks = _anchor_context(profile, visual_assets)
    caption_ids = list(scene["captionIds"])
    caption_texts = [str(captions[item]["text"]) for item in caption_ids]
    caption_text = " / ".join(caption_texts)
    anchor_refs = list(scene.get("anchorRefs", []))
    character_anchors = [continuity[item] for item in anchor_refs if item in continuity]
    identity_tasks = sorted({front_tasks[item] for item in anchor_refs if item in front_tasks})
    required = list(scene.get("requiredEntities", []))
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
    narrative_function = _scene_narrative_function(scene)
    proposition = _scene_proposition(scene, profile, caption_texts, required, light)
    shot = {
        "shot_id": "SHOT_" + str(scene["id"]).upper().replace("-", "_").replace(".", "_"),
        "asset_tier": "narrative_scene",
        "subject": proposition.subject or str(scene.get("description", "")),
        "action": proposition.action or str(scene.get("description", "")),
        "shot_size": _shot_size(scene),
        "lens": "35mm" if _shot_size(scene).startswith("wide") else "50mm",
        "camera_angle": "eye level with story-motivated variation",
        "composition": "clear narrative hierarchy, readable silhouettes, and subtitle-safe lower frame",
        "depth": "story-critical entities readable; background simplified but period credible",
        "emotion": str(scene.get("captionIntent", caption_text)),
        "narration_text": caption_text,
        "semantic_entities": required,
        "forbidden_entities": forbidden,
        "scene_mode": _scene_mode(scene),
        "lighting_intent": {
            "key": str(light["key"]),
            "fill": str(light["fill"]),
            "shadow_readability": str(light["shadow"]),
            "temperature_role": "balanced",
        },
        "risk_flags": list(scene.get("riskFlags", [])),
    }
    art_direction = _visual_art_direction(profile)
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
        )
    except PromptSpecError as error:
        raise DirectorStageError(
            f"scene {scene.get('id')} cannot produce a caption-first prompt: {error}"
        ) from error
    # Caption-first: the priority blocks lead, the legacy art-direction body follows
    # as supporting detail. The model reads the top of the prompt hardest.
    prompt = "\n".join(priority_blocks) + "\n" + prompt
    generation_mode = "single" if scene.get("riskFlags") else str(scene.get("generationMode", "single"))
    if generation_mode not in {"single", "2x2"}:
        raise DirectorStageError(f"scene {scene['id']} has unsupported generation mode")
    task_id = "SCENE_" + str(scene["id"]).upper().replace("-", "_").replace(".", "_")
    return {
        "schema_version": "production-image-task.v1",
        "task_id": task_id,
        "scene_id": scene["id"],
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
        ),
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


def _expected(root: Path) -> tuple[dict[str, bytes], str, str]:
    audio_manifest_path = _project_file(root, "04_audio/AUDIO_STAGE_MANIFEST.json", "audio stage manifest")
    audio_manifest_probe = _load_json(audio_manifest_path, "audio stage manifest")
    release_id = audio_manifest_probe.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise DirectorStageError("audio stage release_id is invalid")
    status = audio_stage_status(root, release_id)
    if status != "ready_for_image_task_planning":
        raise DirectorStageError(f"Phase 4 final audio gate is not current: {status}")
    paths = {
        "audio_manifest": _project_file(root, "04_audio/AUDIO_STAGE_MANIFEST.json", "audio stage manifest"),
        "audio_meta": _project_file(root, "audio_meta.json", "audio metadata"),
        "storyboard": _project_file(root, "STORYBOARD.json", "final audio storyboard"),
        "bindings": _project_file(root, "04_audio/CAPTION_BINDINGS.json", "caption bindings"),
        "visual_profile": _project_file(root, "03_images_生成图片/BOOK_VISUAL_PROFILE.json", "visual profile"),
        "visual_approval": _project_file(root, "03_images_生成图片/ANCHOR_APPROVAL.json", "visual approval"),
        "visual_assets": _project_file(root, "03_images_生成图片/VISUAL_ASSET_MANIFEST.json", "visual asset manifest"),
        "hbg_style": _project_file(root, "HBG_STYLE.json", "HBG style"),
    }
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
    scenes: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for scene in storyboard:
        missing = [item for item in scene["captionIds"] if item not in captions]
        if missing:
            raise DirectorStageError(f"scene {scene['id']} references unknown captions: {missing}")
        caption_text = " / ".join(str(captions[item]["text"]) for item in scene["captionIds"])
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
            "required_entities": list(scene.get("requiredEntities", [])),
            "forbidden_entities": list(scene.get("forbiddenEntities", [])),
            "risk_flags": list(scene.get("riskFlags", [])),
            "anchor_refs": list(scene.get("anchorRefs", [])),
            "participants": dict(scene.get("participants", {})),
            "motion": str(scene["motion"]),
            "generation_mode": "single" if scene.get("riskFlags") else str(scene.get("generationMode", "single")),
        })
        tasks.append(_task_for_scene(scene, profile, visual_assets, captions, canvas))
    durations = [item["duration"] for item in scenes]
    timeline = {
        "schema_version": "director-timeline.v1",
        "release_id": audio_manifest["release_id"],
        "audio_stage_manifest_sha256": sha256_file(paths["audio_manifest"]),
        "audio_meta_sha256": sha256_file(paths["audio_meta"]),
        "caption_bindings_sha256": sha256_file(paths["bindings"]),
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
                "implementation": "hbg-native-zoom-pan",
            }
            for item in scenes
        ],
    }
    sheet_map = _sheet_plan(tasks, storyboard)
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
