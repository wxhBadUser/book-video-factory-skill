from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from book_video_factory.manifests import sha256_file
from book_video_factory.semantic_alignment import (
    SemanticContractError,
    evaluate_semantic_bridge,
)
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    CaptionUnit,
    audit_shot_caption_groups,
)


class StoryboardPlanError(RuntimeError):
    """The Agent-authored real-audio storyboard plan is unsafe or incomplete."""


class VisualPropositionStaleError(StoryboardPlanError):
    """The visual proposition was frozen before the narration was last modified.

    This is the §8 "visual lag" kill switch: when the narration (``audio_meta``)
    is edited after the storyboard proposition was frozen, the imagery planned
    for those words can no longer be trusted, so the pipeline must not advance
    to visual production until the storyboard is re-planned.
    """


def _audio_meta_sha256(audio_meta: Mapping[str, Any]) -> str:
    # Deterministic, content-based fingerprint of the narration the visual
    # proposition was planned against. No wall-clock is involved, so recompiling
    # the same narration always yields the same hash and therefore a byte-identical
    # storyboard (the previous wall-clock ``proposition_frozen_at`` stamp broke that).
    canonical = json.dumps(
        audio_meta, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
_PLACEHOLDER_RE = re.compile(r"(?:TODO|TBD|PLACEHOLDER|FIXME|待定|后补|自动生成|占位)", re.I)
_LOCAL_PATH_RE = re.compile(r"(?:^|[\s'\"])(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:\\|~[/\\])")
_FAKE_COVERAGE_RE = re.compile(
    r"(?:只用|仅用|依靠|靠).{0,12}(?:zoom|pan|推近|拉远|平移|静态画面|不换场景|硬撑).{0,12}(?:拖|撑|替代|覆盖)",
    re.I,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_HIGH_RISK_FLAGS = {
    "hands", "phone", "tool_use", "water_action", "animal_contact",
    "body_contact", "reflection", "death_climax", "hero_shot",
}
_TOP_KEYS = {"schema_version", "release_id", "preliminary_manifest_sha256", "shots", "beat_dispositions"}
_DISPOSITION_KEYS = {"beat_id", "mode", "shot_ids"}
_SHOT_KEYS = {
    "id", "source_beat_ids", "chapter", "cue", "caption_ids", "description",
    "required_entities", "forbidden_entities", "risk_flags", "generation_mode",
    "anchor_refs", "participants", "motion", "visual_load", "intentional_hold",
    "hold_reason", "semantic_rationale", "nonverbal_window",
}
_PARTICIPANT_KEYS = {"count", "allowed"}
_WINDOW_KEYS = {"start", "end"}


def _exact(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    keys = set(value)
    if keys != expected:
        raise StoryboardPlanError(
            f"{label} fields are invalid; missing={sorted(expected-keys)}, extra={sorted(keys-expected)}"
        )


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or (not empty and not value):
        raise StoryboardPlanError(f"{label} must be a {'trimmed' if empty else 'nonempty trimmed'} string")
    if _PLACEHOLDER_RE.search(value):
        raise StoryboardPlanError(f"{label} contains placeholder text")
    if _LOCAL_PATH_RE.search(value):
        raise StoryboardPlanError(f"{label} contains a local filesystem path")
    return value


def _identifier(value: Any, label: str) -> str:
    result = _text(value, label)
    if not _ID_RE.fullmatch(result):
        raise StoryboardPlanError(f"{label} is not a safe identifier")
    return result


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise StoryboardPlanError(f"{label} must be a {'possibly empty' if allow_empty else 'nonempty'} array")
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise StoryboardPlanError(f"{label} contains duplicates")
    return result


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoryboardPlanError(f"{label} must be numeric")
    result = float(value)
    if not 0 <= result <= 24 * 3600:
        raise StoryboardPlanError(f"{label} is outside the supported timeline")
    return result


def _caption_index(audio_meta: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    raw = audio_meta.get("captions")
    if not isinstance(raw, list) or not raw:
        raise StoryboardPlanError("audio_meta captions are missing")
    result: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    previous_end = -1.0
    for index, caption in enumerate(raw):
        if not isinstance(caption, Mapping):
            raise StoryboardPlanError(f"caption {index} is not an object")
        caption_id = _identifier(caption.get("id"), f"captions[{index}].id")
        if caption_id in result:
            raise StoryboardPlanError("caption IDs must be unique")
        start = _number(caption.get("start"), f"captions[{index}].start")
        end = _number(caption.get("end"), f"captions[{index}].end")
        if end <= start or start + 1e-9 < previous_end:
            raise StoryboardPlanError("captions must be monotonic and non-overlapping")
        text = _text(caption.get("text"), f"captions[{index}].text")
        text_sha = caption.get("text_sha256")
        if not isinstance(text_sha, str) or not _SHA_RE.fullmatch(text_sha) or hashlib.sha256(text.encode()).hexdigest() != text_sha:
            raise StoryboardPlanError(f"caption {caption_id} text hash is invalid")
        if caption.get("restoration_status") != "display-restored":
            raise StoryboardPlanError(f"caption {caption_id} is not display-restored")
        normalized = dict(caption); normalized.update({"id": caption_id, "start": start, "end": end, "text": text})
        result[caption_id] = normalized; ordered.append(caption_id); previous_end = end
    return result, ordered


def _beat_index(phase2_beats: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str], set[str]]:
    if not isinstance(phase2_beats, Sequence) or isinstance(phase2_beats, (str, bytes)) or not phase2_beats:
        raise StoryboardPlanError("Phase 2 beats are missing")
    result: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    character_ids: set[str] = set()
    for index, beat in enumerate(phase2_beats):
        if not isinstance(beat, Mapping):
            raise StoryboardPlanError(f"Phase 2 beat {index} is invalid")
        beat_id = _identifier(beat.get("beatId"), f"phase2_beats[{index}].beatId")
        if beat_id in result:
            raise StoryboardPlanError("Phase 2 beat IDs must be unique")
        chapter = beat.get("chapter")
        if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
            raise StoryboardPlanError(f"Phase 2 beat {beat_id} has an invalid chapter")
        cue = _text(beat.get("cue"), f"phase2_beats[{index}].cue")
        anchors = beat.get("anchorRefs", [])
        participants = beat.get("participants", {})
        if isinstance(anchors, list): character_ids.update(item for item in anchors if isinstance(item, str))
        if isinstance(participants, Mapping) and isinstance(participants.get("allowed"), list):
            character_ids.update(item for item in participants["allowed"] if isinstance(item, str))
        result[beat_id] = dict(beat); ordered.append(beat_id)
    return result, ordered, character_ids


def _window_for_shot(shot: Mapping[str, Any], captions: Mapping[str, Mapping[str, Any]]) -> tuple[float, float]:
    ids = shot["caption_ids"]
    if ids:
        return float(captions[ids[0]]["start"]), float(captions[ids[-1]]["end"])
    window = shot.get("nonverbal_window")
    if not isinstance(window, Mapping):
        raise StoryboardPlanError(f"shot {shot['id']} without captions requires nonverbal_window")
    _exact(window, _WINDOW_KEYS, f"shot {shot['id']} nonverbal_window")
    start = _number(window.get("start"), f"shot {shot['id']} nonverbal_window.start")
    end = _number(window.get("end"), f"shot {shot['id']} nonverbal_window.end")
    if end <= start:
        raise StoryboardPlanError(f"shot {shot['id']} nonverbal window is empty")
    return start, end


# Hard boundaries that one generated image can never serve simultaneously.
# Soft caps (duration / caption count) are allowed by design; only the hard
# triggers are enforced here so a single picture is never asked to mean two
# incompatible things (see audit_shot_caption_groups).
_HARD_SPLIT_REASONS = frozenset({
    "character_change",
    "location_change",
    "time_change",
    "narrative_function_change",
})


def _enforce_caption_grouping(
    shots: Sequence[Mapping[str, Any]],
    units: Sequence[CaptionUnit],
) -> None:
    """Fail closed when one generated image would have to serve two meanings.

    This wires the previously-unused ``audit_shot_caption_groups`` into the
    storyboard audio plan gate. A shot that merges captions across a *hard*
    boundary -- different on-screen characters, a different location, a
    different time, or a different narrative register -- cannot be illustrated
    by a single image, so the plan that proposes it is rejected here.
    """

    findings = audit_shot_caption_groups(list(shots), list(units))
    for finding in findings:
        if _HARD_SPLIT_REASONS & set(finding["reasons"]):
            raise CaptionGroupingError(
                f"shot {finding['shot_id']} merges captions "
                f"{finding['previous_caption_id']} and {finding['boundary_caption_id']} "
                f"across a required boundary ({', '.join(finding['reasons'])}); "
                "one image cannot serve both"
            )


def validate_storyboard_audio_plan(
    project: Path,
    payload: Mapping[str, Any],
    preliminary: Mapping[str, Any],
    *,
    audio_meta: Mapping[str, Any],
    phase2_beats: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise StoryboardPlanError("storyboard audio plan must be an object")
    _exact(payload, _TOP_KEYS, "storyboard audio plan")
    if payload.get("schema_version") != "storyboard-audio-plan.v1":
        raise StoryboardPlanError("storyboard audio plan schema_version is invalid")
    release_id = _identifier(payload.get("release_id"), "release_id")
    if preliminary.get("release_id") != release_id or preliminary.get("next_stage_status") != "awaiting_audio_storyboard_plan":
        raise StoryboardPlanError("preliminary audio evidence is stale or in the wrong state")
    manifest_path = project.expanduser().resolve() / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
    expected_manifest = payload.get("preliminary_manifest_sha256")
    if not isinstance(expected_manifest, str) or not _SHA_RE.fullmatch(expected_manifest):
        raise StoryboardPlanError("preliminary_manifest_sha256 is invalid")
    if not manifest_path.is_file() or sha256_file(manifest_path) != expected_manifest:
        raise StoryboardPlanError("preliminary audio manifest hash is stale")

    captions, caption_order = _caption_index(audio_meta)
    caption_position = {caption_id: index for index, caption_id in enumerate(caption_order)}
    caption_units = [CaptionUnit.from_mapping(captions[caption_id]) for caption_id in caption_order]
    beats, beat_order, character_ids = _beat_index(phase2_beats)
    beat_position = {beat_id: index for index, beat_id in enumerate(beat_order)}

    raw_dispositions = payload.get("beat_dispositions")
    if not isinstance(raw_dispositions, list) or not raw_dispositions:
        raise StoryboardPlanError("beat_dispositions must be a nonempty array")
    dispositions: list[dict[str, Any]] = []
    disposition_by_beat: dict[str, dict[str, Any]] = {}
    referenced_shots: dict[str, set[str]] = {}
    for index, raw in enumerate(raw_dispositions):
        if not isinstance(raw, Mapping): raise StoryboardPlanError(f"beat_dispositions[{index}] is invalid")
        _exact(raw, _DISPOSITION_KEYS, f"beat_dispositions[{index}]")
        beat_id = _identifier(raw.get("beat_id"), f"beat_dispositions[{index}].beat_id")
        if beat_id not in beats or beat_id in disposition_by_beat:
            raise StoryboardPlanError("every Phase 2 beat must have exactly one known disposition")
        mode = raw.get("mode")
        if mode not in {"retain", "split", "merge"}:
            raise StoryboardPlanError(f"beat {beat_id} disposition mode is invalid")
        shot_ids = [_identifier(item, f"beat_dispositions[{index}].shot_ids") for item in raw.get("shot_ids", [])] if isinstance(raw.get("shot_ids"), list) else []
        if not shot_ids or len(shot_ids) != len(set(shot_ids)):
            raise StoryboardPlanError(f"beat {beat_id} disposition shot_ids are invalid")
        if mode == "retain" and len(shot_ids) != 1:
            raise StoryboardPlanError(f"retain disposition for {beat_id} requires exactly one shot")
        if mode == "split" and len(shot_ids) < 2:
            raise StoryboardPlanError(f"split disposition for {beat_id} requires at least two shots")
        normalized = {"beat_id": beat_id, "mode": mode, "shot_ids": shot_ids}
        dispositions.append(normalized); disposition_by_beat[beat_id] = normalized
        for shot_id in shot_ids: referenced_shots.setdefault(shot_id, set()).add(beat_id)
    if set(disposition_by_beat) != set(beats):
        raise StoryboardPlanError("beat dispositions do not cover every Phase 2 beat")

    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise StoryboardPlanError("shots must be a nonempty array")
    normalized_shots: list[dict[str, Any]] = []
    shot_ids: set[str] = set()
    used_captions: list[str] = []
    windows: list[tuple[float, float, str]] = []
    prior_chapter = 0
    prior_beat_position = -1
    for index, raw in enumerate(raw_shots):
        if not isinstance(raw, Mapping): raise StoryboardPlanError(f"shots[{index}] is invalid")
        _exact(raw, _SHOT_KEYS, f"shots[{index}]")
        shot_id = _identifier(raw.get("id"), f"shots[{index}].id")
        if shot_id in shot_ids: raise StoryboardPlanError("shot IDs must be unique")
        shot_ids.add(shot_id)
        source_beats = [_identifier(item, f"shots[{index}].source_beat_ids") for item in raw.get("source_beat_ids", [])] if isinstance(raw.get("source_beat_ids"), list) else []
        if not source_beats or len(source_beats) != len(set(source_beats)) or any(item not in beats for item in source_beats):
            raise StoryboardPlanError(f"shot {shot_id} source beats are invalid")
        expected_reverse = referenced_shots.get(shot_id, set())
        if set(source_beats) != expected_reverse:
            raise StoryboardPlanError(f"shot {shot_id} source beats disagree with beat dispositions")
        source_positions = [beat_position[item] for item in source_beats]
        if source_positions != sorted(source_positions) or min(source_positions) < prior_beat_position:
            raise StoryboardPlanError("shot source beat and cue order moves backwards")
        prior_beat_position = max(source_positions)
        source_chapters = {int(beats[item]["chapter"]) for item in source_beats}
        if len(source_chapters) != 1:
            raise StoryboardPlanError(f"shot {shot_id} cannot merge beats across chapters")
        chapter = raw.get("chapter")
        if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter not in source_chapters:
            raise StoryboardPlanError(f"shot {shot_id} chapter does not match its source beats")
        if chapter < prior_chapter: raise StoryboardPlanError("shot chapter order moves backwards")
        prior_chapter = chapter
        cue = _text(raw.get("cue"), f"shots[{index}].cue")
        caption_ids = [_identifier(item, f"shots[{index}].caption_ids") for item in raw.get("caption_ids", [])] if isinstance(raw.get("caption_ids"), list) else []
        if len(caption_ids) != len(set(caption_ids)) or any(item not in captions for item in caption_ids):
            raise StoryboardPlanError(f"shot {shot_id} caption IDs are invalid")
        positions = [caption_position[item] for item in caption_ids]
        if positions != sorted(positions):
            raise StoryboardPlanError(f"shot {shot_id} caption order is invalid")
        if caption_ids:
            first_caption_text = str(captions[caption_ids[0]]["text"])
            if cue != first_caption_text:
                raise StoryboardPlanError(f"shot {shot_id} cue must equal its first display caption text")
        elif cue not in {str(beats[item]["cue"]) for item in source_beats}:
            raise StoryboardPlanError(f"non-verbal shot {shot_id} cue must be a source-beat cue")
        used_captions.extend(caption_ids)
        description = _text(raw.get("description"), f"shots[{index}].description")
        required = _string_list(raw.get("required_entities"), f"shots[{index}].required_entities")
        forbidden = _string_list(raw.get("forbidden_entities"), f"shots[{index}].forbidden_entities", allow_empty=True)
        if not set(required).isdisjoint(forbidden):
            raise StoryboardPlanError(f"shot {shot_id} required and forbidden entities intersect")
        risks = _string_list(raw.get("risk_flags"), f"shots[{index}].risk_flags", allow_empty=True)
        unknown = set(risks) - _HIGH_RISK_FLAGS
        if unknown: raise StoryboardPlanError(f"shot {shot_id} risk flags are unknown: {sorted(unknown)}")
        generation_mode = raw.get("generation_mode")
        if generation_mode not in {"single", "2x2"}: raise StoryboardPlanError(f"shot {shot_id} generation mode is invalid")
        if risks and generation_mode != "single": raise StoryboardPlanError(f"high-risk shot {shot_id} must use single generation")
        anchors = _string_list(raw.get("anchor_refs"), f"shots[{index}].anchor_refs", allow_empty=True)
        if not set(anchors).issubset(character_ids): raise StoryboardPlanError(f"shot {shot_id} contains an unknown anchor")
        participants = raw.get("participants")
        if not isinstance(participants, Mapping): raise StoryboardPlanError(f"shot {shot_id} participants are invalid")
        _exact(participants, _PARTICIPANT_KEYS, f"shot {shot_id} participants")
        allowed = _string_list(participants.get("allowed"), f"shot {shot_id} participants.allowed", allow_empty=True)
        count = participants.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count != len(allowed):
            raise StoryboardPlanError(f"shot {shot_id} participant count is invalid")
        if not set(allowed).issubset(character_ids): raise StoryboardPlanError(f"shot {shot_id} contains an unknown participant")
        if not set(allowed).issubset(anchors): raise StoryboardPlanError(f"every visible participant in {shot_id} requires an identity anchor")
        motion = _text(raw.get("motion"), f"shots[{index}].motion")
        if motion != "hold":
            raise StoryboardPlanError(
                f"shot {shot_id} motion must be 'hold' (scene images are static stills); got {motion!r}"
            )
        visual_load = raw.get("visual_load")
        if visual_load not in {"ordinary", "strong"}: raise StoryboardPlanError(f"shot {shot_id} visual_load is invalid")
        intentional_hold = raw.get("intentional_hold")
        if not isinstance(intentional_hold, bool): raise StoryboardPlanError(f"shot {shot_id} intentional_hold must be boolean")
        hold_reason = _text(raw.get("hold_reason"), f"shots[{index}].hold_reason", empty=True)
        rationale = _text(raw.get("semantic_rationale"), f"shots[{index}].semantic_rationale")
        window_raw = raw.get("nonverbal_window")
        if window_raw is not None and not isinstance(window_raw, Mapping):
            raise StoryboardPlanError(f"shot {shot_id} nonverbal_window must be null or an object")
        normalized = {
            "id": shot_id, "source_beat_ids": source_beats, "chapter": chapter, "cue": cue,
            "caption_ids": caption_ids, "description": description, "required_entities": required,
            "forbidden_entities": forbidden, "risk_flags": risks, "generation_mode": generation_mode,
            "anchor_refs": anchors, "participants": {"count": count, "allowed": allowed},
            "motion": motion, "visual_load": visual_load, "intentional_hold": intentional_hold,
            "hold_reason": hold_reason, "semantic_rationale": rationale,
            "nonverbal_window": dict(window_raw) if isinstance(window_raw, Mapping) else None,
        }
        start, end = _window_for_shot(normalized, captions)
        duration = end - start
        if not caption_ids:
            if end > max(float(item["end"]) for item in captions.values()) + 1e-9:
                raise StoryboardPlanError(f"non-verbal shot {shot_id} exceeds the real narration timeline")
            if not intentional_hold or len(hold_reason) < 8:
                raise StoryboardPlanError(f"non-verbal shot {shot_id} requires an explicit intentional hold reason")
        elif window_raw is not None:
            raise StoryboardPlanError(f"caption-bound shot {shot_id} cannot declare a nonverbal window")
        if duration > 16.0 + 1e-9: raise StoryboardPlanError(f"shot {shot_id} exceeds the 16 second hard limit")
        if duration > 12.0 + 1e-9:
            if not intentional_hold or len(hold_reason) < 8:
                raise StoryboardPlanError(f"shot {shot_id} over 12 seconds requires a concrete intentional hold")
            if _FAKE_COVERAGE_RE.search(hold_reason):
                raise StoryboardPlanError(
                    "zoom/pan/static hold cannot substitute for missing visual coverage"
                )
        if 8.0 - 1e-9 <= duration <= 12.0 + 1e-9 and visual_load != "strong":
            raise StoryboardPlanError(f"shot {shot_id} between 8 and 12 seconds requires visual_load=strong")
        normalized.update({"relative_start": round(start, 3), "relative_end": round(end, 3), "duration": round(duration, 3)})
        normalized_shots.append(normalized); windows.append((start, end, shot_id))

    _enforce_caption_grouping(normalized_shots, caption_units)
    if set(shot_ids) != set(referenced_shots):
        raise StoryboardPlanError("shot list and beat dispositions reference different shots")
    if len(used_captions) != len(set(used_captions)):
        raise StoryboardPlanError("a caption cannot be assigned to multiple shots")
    if set(used_captions) != set(caption_order):
        raise StoryboardPlanError("every display caption must be assigned exactly once")
    flattened = [caption_position[item] for shot in normalized_shots for item in shot["caption_ids"]]
    if flattened != sorted(flattened):
        raise StoryboardPlanError("caption and shot order moves backwards")
    ordered_windows = sorted(windows, key=lambda item: (item[0], item[1], item[2]))
    if [item[2] for item in ordered_windows] != [item["id"] for item in normalized_shots]:
        raise StoryboardPlanError("shot window order disagrees with authored shot order")
    for previous, current in zip(ordered_windows, ordered_windows[1:]):
        if current[0] + 1e-9 < previous[1]:
            raise StoryboardPlanError(f"shot windows overlap: {previous[2]} and {current[2]}")
    caption_bound_durations = [item["duration"] for item in normalized_shots if item["caption_ids"]]
    median = statistics.median(caption_bound_durations)
    if not 3.2 - 1e-9 <= median <= 5.5 + 1e-9:
        raise StoryboardPlanError(f"project median shot duration {median:.3f}s is outside 3.2-5.5s")
    return {
        "schema_version": "storyboard-audio-plan.v1", "release_id": release_id,
        "preliminary_manifest_sha256": expected_manifest,
        "beat_dispositions": dispositions, "shots": normalized_shots,
        "density": {"median_duration": round(float(median), 3), "shot_count": len(normalized_shots)},
    }


def compile_final_storyboard(
    plan: Mapping[str, Any],
    audio_meta: Mapping[str, Any],
    phase2_beats: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    captions, ordered_caption_ids = _caption_index(audio_meta)
    beat_index, _, _ = _beat_index(phase2_beats)
    opening = audio_meta.get("opening")
    if not isinstance(opening, Mapping): raise StoryboardPlanError("audio_meta opening evidence is missing")
    # §8 visual-lag kill switch: bind the storyboard to a content hash of the
    # narration it was planned against. A later narration edit changes the hash,
    # which validate_visual_proposition_currency detects as stale. No wall-clock
    # is used, so recompiling identical inputs yields a byte-identical storyboard.
    audio_meta_sha256 = _audio_meta_sha256(audio_meta)
    body_start = _number(opening.get("bodyStart"), "audio_meta.opening.bodyStart")
    body = audio_meta.get("body")
    body_duration = (
        _number(body.get("duration"), "audio_meta.body.duration")
        if isinstance(body, Mapping) and body.get("duration") is not None
        else float(captions[ordered_caption_ids[-1]]["end"])
    )
    storyboard: list[dict[str, Any]] = []
    caption_bindings: dict[str, dict[str, Any]] = {
        caption_id: {
            "caption_id": caption_id, "text": captions[caption_id]["text"],
            "start": captions[caption_id]["start"], "end": captions[caption_id]["end"],
            "text_sha256": captions[caption_id]["text_sha256"], "shot_ids": [],
            "shared_required_entities": [], "semantic_rationale": "",
            "semantic_bridge_mode": "", "rationale_is_boilerplate": False,
            "restoration_status": captions[caption_id]["restoration_status"],
        }
        for caption_id in ordered_caption_ids
    }
    shot_bindings: dict[str, dict[str, Any]] = {}
    for shot_index, shot in enumerate(plan["shots"]):
        relative_start = float(shot["relative_start"]); relative_end = float(shot["relative_end"])
        timeline_start = 0.0 if shot_index == 0 else relative_start
        timeline_end = body_duration if shot_index == len(plan["shots"]) - 1 else relative_end
        source_entities: set[str] = set()
        for beat_id in shot["source_beat_ids"]:
            source_entities.update(beat_index[beat_id].get("requiredEntities", []))
        caption_texts = [str(captions[caption_id]["text"]) for caption_id in shot["caption_ids"]]
        try:
            bridge = evaluate_semantic_bridge(
                shot_id=str(shot["id"]),
                required_entities=list(shot["required_entities"]),
                source_entities=source_entities,
                rationale=str(shot["semantic_rationale"]),
                caption_texts=caption_texts,
            )
        except SemanticContractError as error:
            raise StoryboardPlanError(str(error)) from error
        shared = list(bridge.shared_entities)
        # Carry the narrative register explicitly so the director stage can no
        # longer silently collapse every scene to "plot". When the source
        # captions do not declare a register, CaptionUnit defaults it to "plot"
        # -- which is now an explicit value, not a missing field.
        shot_narrative_function = (
            CaptionUnit.from_mapping(captions[shot["caption_ids"][0]]).narrative_function
            if shot["caption_ids"]
            else "plot"
        )
        final = {
            "id": shot["id"], "beatId": shot["source_beat_ids"][0],
            "narrativeFunction": shot_narrative_function,
            "narrative_function": shot_narrative_function,
            "audio_meta_sha256": audio_meta_sha256,
            "sourceBeatIds": list(shot["source_beat_ids"]), "chapter": shot["chapter"],
            "cue": shot["cue"], "captionIds": list(shot["caption_ids"]),
            "description": shot["description"], "captionIntent": " / ".join(captions[c]["text"] for c in shot["caption_ids"]) or shot["hold_reason"],
            "requiredEntities": list(shot["required_entities"]), "forbiddenEntities": list(shot["forbidden_entities"]),
            "riskFlags": list(shot["risk_flags"]), "generationMode": shot["generation_mode"],
            "anchorRefs": list(shot["anchor_refs"]), "participants": dict(shot["participants"]),
            "motion": shot["motion"], "highRisk": bool(shot["risk_flags"]),
            "asset": f"assets/generated/scenes/{shot['id']}.png",
            "cueTime": round(relative_start, 3), "start": round(body_start + timeline_start, 3),
            "end": round(body_start + timeline_end, 3), "duration": round(timeline_end-timeline_start, 3),
            "intentionalHold": shot["intentional_hold"], "holdReason": shot["hold_reason"],
            "semanticRationale": shot["semantic_rationale"],
            "semanticBridgeMode": bridge.mode,
            "semanticSourceTermsNamed": list(bridge.source_terms_named),
            "semanticImageTermsNamed": list(bridge.image_terms_named),
            "rationaleIsBoilerplate": bridge.rationale_is_boilerplate,
        }
        storyboard.append(final)
        for caption_id in shot["caption_ids"]:
            caption_bindings[caption_id]["shot_ids"].append(shot["id"])
            caption_bindings[caption_id]["shared_required_entities"] = shared
            caption_bindings[caption_id]["semantic_rationale"] = shot["semantic_rationale"]
            caption_bindings[caption_id]["semantic_bridge_mode"] = bridge.mode
            caption_bindings[caption_id]["rationale_is_boilerplate"] = bridge.rationale_is_boilerplate
        shot_bindings[shot["id"]] = {
            "shot_id": shot["id"], "start": final["start"], "end": final["end"],
            "source_beat_ids": list(shot["source_beat_ids"]), "caption_ids": list(shot["caption_ids"]),
            "uncovered_time": 0.0, "intentional_hold": shot["intentional_hold"],
            "hold_reason": shot["hold_reason"], "shared_required_entities": shared,
            "semantic_bridge_mode": bridge.mode,
            "source_terms_named": list(bridge.source_terms_named),
            "image_terms_named": list(bridge.image_terms_named),
            "rationale_is_boilerplate": bridge.rationale_is_boilerplate,
        }
    bindings = {
        "schema_version": "caption-bindings.v1", "release_id": plan["release_id"],
        "captions": caption_bindings, "shots": shot_bindings,
        "coverage": {"caption_count": len(caption_bindings), "shot_count": len(shot_bindings), "unbound_captions": []},
    }
    return storyboard, bindings


def validate_visual_proposition_currency(
    storyboard: Sequence[Mapping[str, Any]],
    audio_meta: Mapping[str, Any],
) -> None:
    """§8 kill switch: block advancement when narration content changed after freeze.

    ``compile_final_storyboard`` embeds the content hash of the narration
    (``audio_meta``) it planned against into every scene as ``audio_meta_sha256``.
    If the current ``audio_meta`` hashes to a different value, the narration was
    edited after the visual proposition was frozen and the imagery can no longer
    be trusted, so the pipeline must re-plan before producing visuals.

    Fail-closed: a storyboard without a valid ``audio_meta_sha256`` stamp cannot
    certify currency, so it is rejected. The check is purely content-based -- no
    wall-clock is involved -- so it is reproducible and cannot be defeated by
    clock drift or a hand-edited ``last_modified`` field.
    """

    if not storyboard:
        return
    embedded = storyboard[0].get("audio_meta_sha256")
    if not isinstance(embedded, str) or not _SHA_RE.fullmatch(embedded):
        raise VisualPropositionStaleError(
            "visual proposition has no valid audio_meta_sha256 stamp; currency cannot be certified"
        )
    current = _audio_meta_sha256(audio_meta)
    if current != embedded:
        raise VisualPropositionStaleError(
            "narration content (audio_meta) changed after the visual proposition "
            "was frozen; re-plan the storyboard before visual production"
        )
