"""SceneSpec: the single semantic scene contract for Phase 1+.

A SceneSpec answers "what story现场 is this several seconds of narration?" It
does NOT answer "which exact image should be generated?" — that is the
VisualShot Planner's job in Phase 2.

Phase 1 refactoring collapses the former chain
  Caption → MeaningBlock → CaptionVisualContract → CaptionGroup →
  GroupVisualContract → VisualBeat → VisualHold → SCS → VisualTimeline → ImageTask
into:
  CaptionCue → SceneSpec → VisualShot (Phase 2) → ImageTask

SceneSpec is the authoritative semantic unit. Python validates the contract
(IDs, time ranges, character existence, life-stage validity, conflict
detection); the Semantic Director Agent interprets the literature and
populates the fields.

Principle: LLM interprets literature; Python validates contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from book_video_factory.narrative_functions import (
    NARRATIVE_FUNCTION_SET,
    is_valid_narrative_function,
    validate_narrative_function,
)

SCENE_SPEC_SCHEMA_VERSION = "scene-spec.v1"

VALID_VISUAL_MODES = frozenset({"literal", "symbolic", "abstract", "atmospheric"})


class SceneSpecError(ValueError):
    """A SceneSpec failed contract validation."""


@dataclass(frozen=True)
class SceneCharacter:
    """A character appearing in this scene, with life-stage and role context."""

    character_id: str
    life_stage: str = ""
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"character_id": self.character_id}
        if self.life_stage:
            data["life_stage"] = self.life_stage
        if self.role:
            data["role"] = self.role
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SceneCharacter":
        cid = str(data.get("character_id", "")).strip()
        if not cid:
            raise SceneSpecError("SceneCharacter requires a nonempty character_id")
        return cls(
            character_id=cid,
            life_stage=str(data.get("life_stage", "") or "").strip(),
            role=str(data.get("role", "") or "").strip(),
        )


@dataclass(frozen=True)
class SceneSpec:
    """One continuous semantic scene bound to a real-audio time window.

    Fields are intentionally descriptive, not prescriptive about image count.
    A SceneSpec may spawn multiple VisualShots in Phase 2.
    """

    scene_id: str
    start: float
    end: float
    caption_ids: tuple[str, ...]
    narrative_function: str
    period: str = ""
    geography: str = ""
    location_id: str = ""
    time_context: str = ""
    characters: tuple[SceneCharacter, ...] = ()
    event: str = ""
    required_visuals: tuple[str, ...] = ()
    forbidden_visuals: tuple[str, ...] = ()
    key_props: tuple[str, ...] = ()
    visual_mode: str = "literal"
    continuity_key: str = ""
    risk_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validate on construction so a bad SceneSpec never enters the pipeline.
        if not str(self.scene_id).strip():
            raise SceneSpecError("scene_id must be nonempty")
        if self.end <= self.start:
            raise SceneSpecError(
                f"scene {self.scene_id}: end ({self.end}) must be > start ({self.start})"
            )
        if not self.caption_ids:
            raise SceneSpecError(f"scene {self.scene_id}: at least one caption_id required")
        if any(not str(c).strip() for c in self.caption_ids):
            raise SceneSpecError(f"scene {self.scene_id}: empty caption_id found")
        validate_narrative_function(self.narrative_function, label=f"scene {self.scene_id} narrative_function")
        if self.visual_mode not in VALID_VISUAL_MODES:
            raise SceneSpecError(
                f"scene {self.scene_id}: visual_mode {self.visual_mode!r} not in {sorted(VALID_VISUAL_MODES)}"
            )
        # Duplicate character_ids within one scene are a contract error: the
        # same life-stage instance should appear once.
        seen: set[str] = set()
        for ch in self.characters:
            if ch.character_id in seen:
                raise SceneSpecError(
                    f"scene {self.scene_id}: duplicate character_id {ch.character_id!r}"
                )
            seen.add(ch.character_id)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "scene_id": self.scene_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": self.duration,
            "caption_ids": list(self.caption_ids),
            "narrative_function": self.narrative_function,
            "visual_mode": self.visual_mode,
        }
        if self.period:
            data["period"] = self.period
        if self.geography:
            data["geography"] = self.geography
        if self.location_id:
            data["location_id"] = self.location_id
        if self.time_context:
            data["time_context"] = self.time_context
        if self.characters:
            data["characters"] = [ch.to_dict() for ch in self.characters]
        if self.event:
            data["event"] = self.event
        if self.required_visuals:
            data["required_visuals"] = list(self.required_visuals)
        if self.forbidden_visuals:
            data["forbidden_visuals"] = list(self.forbidden_visuals)
        if self.key_props:
            data["key_props"] = list(self.key_props)
        if self.continuity_key:
            data["continuity_key"] = self.continuity_key
        if self.risk_flags:
            data["risk_flags"] = list(self.risk_flags)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SceneSpec":
        """Parse and validate a SceneSpec from a dict (e.g. loaded JSON)."""
        try:
            characters = tuple(
                SceneCharacter.from_mapping(ch)
                for ch in data.get("characters", []) or []
            )
            return cls(
                scene_id=str(data["scene_id"]).strip(),
                start=float(data["start"]),
                end=float(data["end"]),
                caption_ids=tuple(str(c).strip() for c in data.get("caption_ids", [])),
                narrative_function=str(data.get("narrative_function", "plot")).strip(),
                period=str(data.get("period", "") or "").strip(),
                geography=str(data.get("geography", "") or "").strip(),
                location_id=str(data.get("location_id", "") or "").strip(),
                time_context=str(data.get("time_context", "") or "").strip(),
                characters=characters,
                event=str(data.get("event", "") or "").strip(),
                required_visuals=tuple(str(v).strip() for v in data.get("required_visuals", []) if str(v).strip()),
                forbidden_visuals=tuple(str(v).strip() for v in data.get("forbidden_visuals", []) if str(v).strip()),
                key_props=tuple(str(p).strip() for p in data.get("key_props", []) if str(p).strip()),
                visual_mode=str(data.get("visual_mode", "literal")).strip(),
                continuity_key=str(data.get("continuity_key", "") or "").strip(),
                risk_flags=tuple(str(r).strip() for r in data.get("risk_flags", []) if str(r).strip()),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SceneSpecError(f"invalid SceneSpec mapping: {error}") from error


def build_scene_spec_document(
    *,
    release_id: str,
    scenes: Sequence[SceneSpec],
    source_caption_cues_sha256: str = "",
) -> dict[str, Any]:
    """Build the canonical SCENE_SPECS.json document."""
    if not scenes:
        raise SceneSpecError("at least one SceneSpec is required")
    # Validate temporal ordering and non-overlap.
    sorted_scenes = sorted(scenes, key=lambda s: s.start)
    previous_end: float | None = None
    for scene in sorted_scenes:
        if previous_end is not None and scene.start < previous_end - 1e-6:
            raise SceneSpecError(
                f"scene {scene.scene_id} starts at {scene.start} before previous scene ends at {previous_end}"
            )
        previous_end = scene.end
    return {
        "schema_version": SCENE_SPEC_SCHEMA_VERSION,
        "release_id": str(release_id),
        "source_caption_cues_sha256": source_caption_cues_sha256,
        "scene_count": len(scenes),
        "scenes": [s.to_dict() for s in sorted_scenes],
    }


def validate_scene_spec_document(document: Mapping[str, Any]) -> list[SceneSpec]:
    """Validate a SCENE_SPECS.json document and return parsed SceneSpec objects.

    Raises SceneSpecError on any contract violation. This is the Python-side
    validation gate; the Agent produces the content, Python enforces the
    contract.
    """
    if document.get("schema_version") != SCENE_SPEC_SCHEMA_VERSION:
        raise SceneSpecError(
            f"unsupported schema_version {document.get('schema_version')!r}; expected {SCENE_SPEC_SCHEMA_VERSION}"
        )
    raw_scenes = document.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise SceneSpecError("document must contain a nonempty 'scenes' array")
    scenes = [SceneSpec.from_mapping(s) for s in raw_scenes]
    # Cross-scene validations
    seen_ids: set[str] = set()
    all_caption_ids: set[str] = set()
    for scene in scenes:
        if scene.scene_id in seen_ids:
            raise SceneSpecError(f"duplicate scene_id {scene.scene_id!r}")
        seen_ids.add(scene.scene_id)
        for cid in scene.caption_ids:
            if cid in all_caption_ids:
                raise SceneSpecError(
                    f"caption_id {cid!r} appears in more than one SceneSpec"
                )
            all_caption_ids.add(cid)
    return scenes


def load_scene_specs(path: str | Path) -> list[SceneSpec]:
    """Load and validate a SCENE_SPECS.json file from disk."""
    p = Path(path)
    if not p.is_file():
        raise SceneSpecError(f"scene specs file not found: {p}")
    try:
        document = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SceneSpecError(f"cannot read scene specs file {p}: {error}") from error
    return validate_scene_spec_document(document)


def build_scene_specs_from_caption_cues(
    caption_cues: Sequence["CaptionCue"],
    scene_definitions: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
) -> tuple[list[SceneSpec], dict[str, Any]]:
    """Build SceneSpec list + document from CaptionCue list and scene groupings.

    This is the canonical production bridge from CaptionCue to SceneSpec. Each
    scene definition lists the ``caption_ids`` it spans; the SceneSpec's
    ``start``/``end`` are derived **exclusively** from the referenced
    CaptionCue objects (which themselves carry provider timing). No estimated
    or externally-supplied start/end is accepted.

    Fail-closed enforcement:
    * Every ``caption_ids`` entry MUST reference an existing CaptionCue.
    * A caption_id may appear in at most one scene (enforced by validation).
    * Scene start/end are taken from the first/last referenced cue in
      temporal order; they must match the cue timing exactly.
    * The returned document binds ``source_caption_cues_sha256`` so downstream
      consumers can verify timing provenance.

    Each *scene_definition* mapping supports all SceneSpec fields except
    ``start``/``end`` (which are derived) and ``caption_ids`` (required).
    """
    from book_video_factory.caption_cue import CaptionCue, caption_cue_content_hash

    if not caption_cues:
        raise SceneSpecError("at least one CaptionCue is required")
    cue_by_id: dict[str, CaptionCue] = {c.caption_id: c for c in caption_cues}
    if len(cue_by_id) != len(caption_cues):
        raise SceneSpecError("duplicate caption_id in CaptionCue list")

    scenes: list[SceneSpec] = []
    for index, definition in enumerate(scene_definitions, start=1):
        raw_ids = definition.get("caption_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise SceneSpecError(
                f"scene definition #{index}: caption_ids must be a nonempty list"
            )
        caption_ids = tuple(str(c).strip() for c in raw_ids)
        for cid in caption_ids:
            if cid not in cue_by_id:
                raise SceneSpecError(
                    f"scene definition #{index}: caption_id {cid!r} does not "
                    f"reference any CaptionCue"
                )
        ordered = sorted((cue_by_id[cid] for cid in caption_ids), key=lambda c: c.start)
        start = ordered[0].start
        end = ordered[-1].end

        characters = tuple(
            SceneCharacter.from_mapping(ch)
            for ch in definition.get("characters", []) or []
        )
        scene = SceneSpec(
            scene_id=str(definition.get("scene_id") or f"SCENE_{index:04d}").strip(),
            start=start,
            end=end,
            caption_ids=caption_ids,
            narrative_function=str(
                definition.get("narrative_function", "plot")
            ).strip(),
            period=str(definition.get("period", "") or "").strip(),
            geography=str(definition.get("geography", "") or "").strip(),
            location_id=str(definition.get("location_id", "") or "").strip(),
            time_context=str(definition.get("time_context", "") or "").strip(),
            characters=characters,
            event=str(definition.get("event", "") or "").strip(),
            required_visuals=tuple(
                str(v).strip()
                for v in definition.get("required_visuals", [])
                if str(v).strip()
            ),
            forbidden_visuals=tuple(
                str(v).strip()
                for v in definition.get("forbidden_visuals", [])
                if str(v).strip()
            ),
            key_props=tuple(
                str(p).strip()
                for p in definition.get("key_props", [])
                if str(p).strip()
            ),
            visual_mode=str(definition.get("visual_mode", "literal")).strip(),
            continuity_key=str(definition.get("continuity_key", "") or "").strip(),
            risk_flags=tuple(
                str(r).strip()
                for r in definition.get("risk_flags", [])
                if str(r).strip()
            ),
        )
        scenes.append(scene)

    cues_hash = caption_cue_content_hash(caption_cues)
    document = build_scene_spec_document(
        release_id=release_id,
        scenes=scenes,
        source_caption_cues_sha256=cues_hash,
    )
    return scenes, document


def scene_spec_content_hash(scenes: Iterable[SceneSpec]) -> str:
    """Stable hash of scene spec content for provenance binding."""
    payload = [s.to_dict() for s in scenes]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "SCENE_SPEC_SCHEMA_VERSION",
    "VALID_VISUAL_MODES",
    "SceneSpecError",
    "SceneCharacter",
    "SceneSpec",
    "build_scene_spec_document",
    "validate_scene_spec_document",
    "load_scene_specs",
    "build_scene_specs_from_caption_cues",
    "scene_spec_content_hash",
]
