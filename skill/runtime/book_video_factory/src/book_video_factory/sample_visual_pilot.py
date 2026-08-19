"""Immutable visual evidence for one approved-script sample excerpt.

This deliberately sits beside, rather than inside, the full-book Phase 3
approval.  A sample can establish and review only the assets it actually uses;
it must never be interpreted as a completed full-book visual approval.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_video_factory.manifests import safe_project_output, sha256_file, write_immutable_json
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    verify_visual_asset_manifest,
)


class SampleVisualPilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleVisualPilotResult:
    status: str
    path: Path
    sample_id: str
    scene_span_count: int


_INPUT_SCHEMA = "sample-visual-pilot-input.v2"
_OUTPUT_SCHEMA = "sample-visual-pilot-manifest.v2"
_OUTPUT_RELATIVE = "03_images_生成图片/SAMPLE_VISUAL_PILOT_MANIFEST.json"
_ALLOWED_TRANSITIONS = {"hard_cut", "dissolve_6_8_frames"}
_PROVIDERS = {"host-imagegen", "gemini-web", "flow-web"}
_MIN_SAMPLE_DURATION_SECONDS = 45.0
_MAX_SAMPLE_DURATION_SECONDS = 90.0
_MIN_SAMPLE_SCENE_IMAGES = 4


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SampleVisualPilotError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise SampleVisualPilotError(f"{label} must be a JSON object")
    return value


def _asset_record(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": asset["task_id"],
        "path": asset["path"],
        "sha256": asset["sha256"],
        "tool_call_id": asset["tool_call_id"],
        "identity_reference_evidence": asset["identity_reference_evidence"],
    }


def _excerpt_sequences(excerpt: dict[str, Any]) -> list[int]:
    if excerpt.get("schema_version") != "sample-excerpt.v1":
        raise SampleVisualPilotError("sample excerpt schema is invalid")
    if excerpt.get("integrity", {}).get("no_rewrite") is not True:
        raise SampleVisualPilotError("sample excerpt is not marked no_rewrite")
    if not isinstance(excerpt.get("sample_id"), str) or not excerpt["sample_id"].strip():
        raise SampleVisualPilotError("sample excerpt sample_id is invalid")
    selection = excerpt.get("selection")
    if not isinstance(selection, list) or not selection:
        raise SampleVisualPilotError("sample excerpt selection is missing")
    sequences: list[int] = []
    for item in selection:
        if not isinstance(item, dict) or not isinstance(item.get("source_sequence"), int):
            raise SampleVisualPilotError("sample excerpt source sequence is invalid")
        sequences.append(item["source_sequence"])
    if sequences != sorted(set(sequences)):
        raise SampleVisualPilotError("sample excerpt source sequence order is invalid")
    return sequences


def minimum_sample_scene_image_count(excerpt: dict[str, Any]) -> int:
    """Return the density floor for a 45-90 second visual sample excerpt.

    A full visual sample is judged like a finished book-talking channel clip:
    the audience must not drift because the same frame stays on screen for a
    whole minute.  This is a floor (4-6 is the documented reference rhythm),
    not a mechanical per-caption target: a span may cover many captions as
    long as its image still honestly represents the scene.
    """

    timing = excerpt.get("timing")
    if not isinstance(timing, dict):
        raise SampleVisualPilotError("sample excerpt timing is required for a visual sample")
    duration = timing.get("estimated_duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise SampleVisualPilotError("sample excerpt timing is invalid")
    duration = float(duration)
    if not (_MIN_SAMPLE_DURATION_SECONDS <= duration <= _MAX_SAMPLE_DURATION_SECONDS):
        raise SampleVisualPilotError("sample visual pilot requires a 45-90 second visual sample excerpt")
    return max(_MIN_SAMPLE_SCENE_IMAGES, math.ceil(duration / 15.0))


def _current_sample_scene_representative(
    *,
    root: Path,
    record_relative: str,
    release_id: str,
    excerpt: dict[str, Any],
    excerpt_relative: str,
    excerpt_sha256: str,
    source_sequence_ids: list[int],
    assets_by_task: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify the standalone sample-scene record and its current anchors."""
    try:
        record_path = safe_project_output(root, Path(record_relative))
    except ValueError as error:
        raise SampleVisualPilotError(str(error)) from error
    record = _load_object(record_path, "sample scene asset")
    if (
        record.get("schema_version") != "sample-scene-asset.v2"
        or record.get("release_id") != release_id
        or record.get("sample_id") != excerpt.get("sample_id")
        or record.get("sample_excerpt") != {"path": excerpt_relative, "sha256": excerpt_sha256}
        or record.get("source_sequence_ids") != source_sequence_ids
    ):
        raise SampleVisualPilotError("sample scene asset binding is stale or invalid")
    asset = record.get("asset")
    if not isinstance(asset, dict) or not isinstance(asset.get("path"), str) or not isinstance(asset.get("sha256"), str):
        raise SampleVisualPilotError("sample scene asset image record is invalid")
    try:
        asset_path = safe_project_output(root, Path(asset["path"]))
    except ValueError as error:
        raise SampleVisualPilotError(str(error)) from error
    if not asset_path.is_file() or sha256_file(asset_path) != asset["sha256"]:
        raise SampleVisualPilotError("sample scene asset image hash is stale or missing")
    for label in ("identity_references", "location_anchors"):
        references = record.get(label)
        if not isinstance(references, list) or not references:
            raise SampleVisualPilotError(f"sample scene {label} are invalid")
        for reference in references:
            if not isinstance(reference, dict) or not isinstance(reference.get("task_id"), str):
                raise SampleVisualPilotError(f"sample scene {label} are invalid")
            current = assets_by_task.get(reference["task_id"])
            if (
                current is None
                or current.get("task_kind") == "lookdev"
                or {key: current[key] for key in ("task_id", "path", "sha256")} != reference
            ):
                raise SampleVisualPilotError(f"sample scene {label} are stale or invalid")
    generation = record.get("generation")
    if not isinstance(generation, dict) or generation.get("provider") not in _PROVIDERS or not isinstance(generation.get("tool_call_id"), str):
        raise SampleVisualPilotError("sample scene generation evidence is invalid")
    return {
        "kind": "sample_scene_asset",
        "scene_id": record.get("scene_id"),
        "path": asset["path"],
        "sha256": asset["sha256"],
        "tool_call_id": generation["tool_call_id"],
        "source_record": {"path": record_relative, "sha256": sha256_file(record_path)},
    }


def build_sample_visual_pilot(project: Path, input_path: Path) -> SampleVisualPilotResult:
    """Compile one sample-only static visual package from real registered assets."""
    root = project.expanduser().resolve()
    payload = _load_object(input_path.expanduser().resolve(), "sample visual pilot input")
    if payload.get("schema_version") != _INPUT_SCHEMA:
        raise SampleVisualPilotError("sample visual pilot input schema is invalid")
    release_id = payload.get("release_id")
    if not isinstance(release_id, str) or not release_id.strip():
        raise SampleVisualPilotError("sample visual pilot release_id is invalid")
    excerpt_relative = payload.get("sample_excerpt_path")
    expected_excerpt_hash = payload.get("sample_excerpt_sha256")
    if not isinstance(excerpt_relative, str) or not isinstance(expected_excerpt_hash, str):
        raise SampleVisualPilotError("sample excerpt binding is invalid")
    excerpt_path = safe_project_output(root, Path(excerpt_relative))
    if not excerpt_path.is_file() or sha256_file(excerpt_path) != expected_excerpt_hash:
        raise SampleVisualPilotError("sample excerpt hash is stale or missing")
    excerpt = _load_object(excerpt_path, "sample excerpt")
    if excerpt.get("release_id") != release_id:
        raise SampleVisualPilotError("sample excerpt release differs from pilot release")
    excerpt_sequences = _excerpt_sequences(excerpt)

    try:
        verified = verify_visual_asset_manifest(root, require_complete=False)
    except VisualAssetRegistrationError as error:
        raise SampleVisualPilotError(f"sample visual assets are stale or invalid: {error}") from error
    if verified.visual_stage_manifest.get("release_id") != release_id:
        raise SampleVisualPilotError("visual asset manifest release differs from pilot release")

    spans = payload.get("scene_spans")
    if not isinstance(spans, list) or not spans:
        raise SampleVisualPilotError("sample visual pilot requires scene_spans")
    normalized_spans: list[dict[str, Any]] = []
    covered: list[int] = []
    seen_ids: set[str] = set()
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise SampleVisualPilotError("sample visual scene span is invalid")
        span_id = span.get("span_id")
        source_sequence_ids = span.get("source_sequence_ids")
        representative_task_id = span.get("representative_task_id")
        representative_scene_asset_path = span.get("representative_scene_asset_path")
        anchor_task_ids = span.get("required_anchor_task_ids")
        transition_in = span.get("transition_in")
        if (
            not isinstance(span_id, str) or not span_id or span_id in seen_ids
            or not isinstance(source_sequence_ids, list) or not source_sequence_ids
            or not all(isinstance(value, int) for value in source_sequence_ids)
            or source_sequence_ids != sorted(set(source_sequence_ids))
            or not set(source_sequence_ids).issubset(excerpt_sequences)
            or not isinstance(anchor_task_ids, list) or not anchor_task_ids
            or not all(isinstance(value, str) and value for value in anchor_task_ids)
            or len(anchor_task_ids) != len(set(anchor_task_ids))
            or transition_in not in _ALLOWED_TRANSITIONS
        ):
            raise SampleVisualPilotError("sample visual scene span fields are invalid")
        if (isinstance(representative_task_id, str)) == (isinstance(representative_scene_asset_path, str)):
            raise SampleVisualPilotError("sample visual scene span requires exactly one representative source")
        if index == 0 and transition_in != "hard_cut":
            raise SampleVisualPilotError("first sample visual scene span must start with hard_cut")
        if isinstance(representative_task_id, str):
            image = verified.assets_by_task.get(representative_task_id)
            if image is None or image.get("task_kind") != "lookdev":
                raise SampleVisualPilotError("sample representative image must be a registered LookDev asset")
            representative = _asset_record(image)
        else:
            if not representative_scene_asset_path:
                raise SampleVisualPilotError("sample scene representative path is invalid")
            representative = _current_sample_scene_representative(
                root=root,
                record_relative=representative_scene_asset_path,
                release_id=release_id,
                excerpt=excerpt,
                excerpt_relative=excerpt_relative,
                excerpt_sha256=expected_excerpt_hash,
                source_sequence_ids=source_sequence_ids,
                assets_by_task=verified.assets_by_task,
            )
        anchors = []
        for task_id in anchor_task_ids:
            asset = verified.assets_by_task.get(task_id)
            if asset is None or asset.get("task_kind") == "lookdev":
                raise SampleVisualPilotError("sample anchor must be a registered non-LookDev asset")
            anchors.append(_asset_record(asset))
        seen_ids.add(span_id)
        covered.extend(source_sequence_ids)
        normalized_spans.append({
            "span_id": span_id,
            "source_sequence_ids": source_sequence_ids,
            "representative_image": representative,
            "required_anchors": anchors,
            "transition_in": transition_in,
        })
    if sorted(covered) != excerpt_sequences:
        raise SampleVisualPilotError("sample visual scene spans do not exactly cover the excerpt source sequence")
    required_images = minimum_sample_scene_image_count(excerpt)
    if len(normalized_spans) < required_images:
        raise SampleVisualPilotError(
            f"sample visual pilot requires at least {required_images} scene images for a "
            f"{float(excerpt['timing']['estimated_duration_seconds']):.0f}s visual sample; got {len(normalized_spans)}"
        )

    manifest = {
        "schema_version": _OUTPUT_SCHEMA,
        "release_id": release_id,
        "sample_id": excerpt["sample_id"],
        "sample_excerpt": {"path": excerpt_relative, "sha256": expected_excerpt_hash},
        "visual_asset_source": {
            "path": "03_images_生成图片/VISUAL_ASSET_MANIFEST.json",
            "visual_stage_digest": verified.visual_stage_manifest["visual_stage_digest"],
            "binding": "Only assets embedded in scene_spans are hash-bound; unrelated full-book registrations do not stale this sample.",
        },
        "static_image_policy": {
            "no_camera_motion": True,
            "forbidden": ["zoom", "pan", "ken_burns", "crop_animation", "drift"],
            "allowed_transitions": ["hard_cut", "dissolve_6_8_frames"],
        },
        "scene_spans": normalized_spans,
        "human_review_status": "pending",
        "next_stage_status": "blocked_by_sample_visual_review",
        "scope_note": "This manifest is a sample-only visual package and is not full-book visual approval.",
    }
    target = safe_project_output(root, Path(_OUTPUT_RELATIVE))
    if target.exists():
        existing = _load_object(target, "existing sample visual pilot")
        if existing != manifest:
            raise SampleVisualPilotError("existing sample visual pilot is stale or differs from current evidence")
        return SampleVisualPilotResult("unchanged", target, excerpt["sample_id"], len(normalized_spans))
    write_immutable_json(target, manifest)
    return SampleVisualPilotResult("created", target, excerpt["sample_id"], len(normalized_spans))
