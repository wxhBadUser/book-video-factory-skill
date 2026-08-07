from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.manifests import record_approval, safe_project_output, sha256_file
from book_video_factory.render_stage.preflight import RenderPreflightError, verify_render_preflight
from book_video_factory.semantic_alignment.vision_review import validate_review_decision


class EncodedVisualQaError(RuntimeError):
    """Encoded semantic frame evidence is missing, stale, or incompletely reviewed."""


@dataclass(frozen=True)
class EncodedFramePlanResult:
    status: str
    plan_path: Path
    sample_count: int


@dataclass(frozen=True)
class EncodedVisualReviewResult:
    status: str
    report_path: Path
    contact_sheet_path: Path
    next_stage_status: str


_PLAN = "09_qc/ENCODED_FRAME_PLAN.json"
_REPORT = "09_qc/ENCODED_VISUAL_QA.json"
_CONTACT = "09_qc/FINAL_CONTACT_SHEET.jpg"
_CAPTION = "09_qc/CAPTION_PARITY_REPORT.json"
_DECISION_FIELDS = {"schema_version", "release_id", "video_sha256", "frame_plan_sha256", "reviewer", "decisions"}


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EncodedVisualQaError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EncodedVisualQaError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise EncodedVisualQaError(f"{label} must be an object")
    return value


def _file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise EncodedVisualQaError(f"{label} path is invalid")
    try:
        path = safe_project_output(root, Path(relative))
    except (OSError, ValueError) as error:
        raise EncodedVisualQaError(f"{label} path is invalid: {error}") from error
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise EncodedVisualQaError(f"{label} is missing, empty, or symlinked")
    return path


def _expected_plan(root: Path, *, require_current_preflight: bool = True) -> dict[str, Any]:
    render_manifest_path = root / "07_render/RENDER_MANIFEST.json"
    render = _load(render_manifest_path, "render manifest")
    preflight_path = root / "07_render/RENDER_PREFLIGHT.json"
    if require_current_preflight:
        try:
            preflight = verify_render_preflight(root)
        except RenderPreflightError as error:
            raise EncodedVisualQaError(f"render preflight is not current: {error}") from error
    else:
        preflight = _load(preflight_path, "render preflight report")
        if (
            preflight.get("schema_version") != "render-preflight.v1"
            or preflight.get("status") != "pass"
            or preflight.get("render_manifest_sha256") != sha256_file(render_manifest_path)
        ):
            raise EncodedVisualQaError("render preflight binding is stale")
    timeline_path = root / "05_director/DIRECTOR_TIMELINE.json"
    timeline = _load(timeline_path, "director timeline")
    director_manifest_path = root / "05_director/DIRECTOR_STAGE_MANIFEST.json"
    director_manifest = _load(director_manifest_path, "director stage manifest")
    output_hashes = director_manifest.get("output_hashes")
    if not isinstance(output_hashes, dict) or output_hashes.get("05_director/DIRECTOR_TIMELINE.json") != sha256_file(timeline_path):
        raise EncodedVisualQaError("director timeline is not bound by the director stage manifest")
    audio_path = root / "audio_meta.json"
    audio = _load(audio_path, "audio metadata")
    render_input_path = root / "07_render/RENDER_INPUT.json"
    render_input = _load(render_input_path, "render input")
    scene_manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    scene_manifest = _load(scene_manifest_path, "scene asset manifest")
    scenes = timeline.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise EncodedVisualQaError("director timeline scenes are missing")
    total = audio.get("totalDuration")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or float(total) <= 1.0:
        raise EncodedVisualQaError("encoded frame plan requires a positive master duration")
    total = float(total)
    body_start = float(timeline.get("body_start", audio.get("opening", {}).get("bodyStart", 0)))
    if not 0 < body_start < total:
        raise EncodedVisualQaError("opening/body boundary is invalid")
    normalized: list[dict[str, Any]] = []
    seen_scenes: set[str] = set()
    for item in scenes:
        scene_id = item.get("scene_id") if isinstance(item, dict) else None
        if not isinstance(scene_id, str) or not scene_id or scene_id in seen_scenes:
            raise EncodedVisualQaError("director scene IDs are invalid")
        start = float(item.get("start", -1)); end = float(item.get("end", -1))
        if not 0 <= start < end <= total + 0.05:
            raise EncodedVisualQaError(f"director scene timing is invalid: {scene_id}")
        seen_scenes.add(scene_id)
        normalized.append(dict(item))

    samples: dict[tuple[str | None, float], dict[str, Any]] = {}

    def add(seconds: float, category: str, scene: dict[str, Any] | None, rationale: str) -> None:
        bounded = round(min(max(seconds, 0.05), total - 0.05), 3)
        scene_id = str(scene["scene_id"]) if scene else None
        key = (scene_id, bounded)
        record = samples.setdefault(key, {
            "seconds": bounded,
            "categories": [],
            "scene_id": scene_id,
            "chapter": int(scene.get("chapter", 0)) if scene else None,
            "risk_flags": list(scene.get("risk_flags", [])) if scene else [],
            "required_entities": list(scene.get("required_entities", [])) if scene else [],
            "rationales": [],
        })
        if category not in record["categories"]:
            record["categories"].append(category)
        if rationale not in record["rationales"]:
            record["rationales"].append(rationale)

    add(min(0.5, body_start / 2), "opening", None, "HBG opening lead/flash/reveal coverage")
    first_by_chapter: dict[int, dict[str, Any]] = {}
    for scene in normalized:
        first_by_chapter.setdefault(int(scene.get("chapter", 0)), scene)
    for chapter, scene in sorted(first_by_chapter.items()):
        add(float(scene["start"]) + 0.1, "chapter", scene, f"chapter {chapter} entry")
    high_risk = [scene for scene in normalized if scene.get("risk_flags")]
    for scene in high_risk:
        add((float(scene["start"]) + float(scene["end"])) / 2, "high_risk", scene, "high-risk encoded scene")
    heroes = [scene for scene in normalized if "hero_shot" in scene.get("risk_flags", [])]
    if not heroes:
        opening = render_input.get("opening") if isinstance(render_input.get("opening"), dict) else {}
        final_task = opening.get("final_image_task_id")
        assets = scene_manifest.get("assets") if isinstance(scene_manifest.get("assets"), list) else []
        hero_scene_id = next((item.get("scene_id") for item in assets if isinstance(item, dict) and item.get("task_id") == final_task), None)
        heroes = [scene for scene in normalized if scene["scene_id"] == hero_scene_id]
    if not heroes:
        heroes = [normalized[0]]
    for scene in heroes:
        add((float(scene["start"]) + float(scene["end"])) / 2, "hero", scene, "hero identity and composition")
    climaxes = [scene for scene in normalized if "death_climax" in scene.get("risk_flags", [])]
    climax = climaxes[0] if climaxes else normalized[-2 if len(normalized) > 1 else -1]
    add((float(climax["start"]) + float(climax["end"])) / 2, "climax", climax, "explicit or inferred narrative climax")
    ending = normalized[-1]
    add(min(total - 0.5, float(ending["end"]) - 0.5), "ending", ending, "encoded ending frame")

    seed_hex = hashlib.sha256(
        f"{sha256_file(render_manifest_path)}:{sha256_file(timeline_path)}:{preflight['render_job_id']}".encode("utf-8")
    ).hexdigest()
    rng = random.Random(int(seed_hex[:16], 16))
    random_count = min(2, len(normalized))
    for index in sorted(rng.sample(range(len(normalized)), k=random_count)):
        scene = normalized[index]
        add(float(scene["start"]) + 0.37 * (float(scene["end"]) - float(scene["start"])), "random", scene, "deterministic random encoded sample")

    assets = scene_manifest.get("assets") if isinstance(scene_manifest.get("assets"), list) else []
    by_scene = {item.get("scene_id"): item for item in assets if isinstance(item, dict) and isinstance(item.get("scene_id"), str)}
    luminance: list[tuple[float, dict[str, Any]]] = []
    for scene in normalized:
        asset = by_scene.get(scene["scene_id"])
        if not isinstance(asset, dict):
            continue
        image_path = _file(root, asset.get("path"), f"caption background scene {scene['scene_id']}")
        if asset.get("sha256") != sha256_file(image_path):
            raise EncodedVisualQaError(f"caption background asset hash is stale: {scene['scene_id']}")
        try:
            with Image.open(image_path) as image:
                luma = float(ImageStat.Stat(image.convert("L").resize((64, 36))).mean[0])
        except OSError as error:
            raise EncodedVisualQaError(f"caption background asset is undecodable: {scene['scene_id']}") from error
        luminance.append((luma, scene))
    if not luminance:
        raise EncodedVisualQaError("caption parity requires at least one registered scene image")
    luminance.sort(key=lambda item: (item[0], item[1]["scene_id"]))
    dark_scene = luminance[0][1]; bright_scene = luminance[-1][1]
    add((float(dark_scene["start"]) + float(dark_scene["end"])) / 2, "caption_dark", dark_scene, "encoded caption on darkest registered scene")
    add((float(bright_scene["start"]) + float(bright_scene["end"])) / 2, "caption_bright", bright_scene, "encoded caption on brightest registered scene")

    ordered = sorted(samples.values(), key=lambda item: (item["seconds"], item["scene_id"] or ""))
    for index, item in enumerate(ordered, start=1):
        item["sample_id"] = f"encoded-{index:03d}"
        item["categories"].sort()
        item["risk_flags"].sort()
        item["required_entities"].sort()
    return {
        "schema_version": "encoded-frame-plan.v1",
        "release_id": render["release_id"],
        "render_manifest_sha256": sha256_file(render_manifest_path),
        "render_preflight_sha256": sha256_file(root / "07_render/RENDER_PREFLIGHT.json"),
        "director_stage_manifest_sha256": sha256_file(director_manifest_path),
        "director_timeline_sha256": sha256_file(timeline_path),
        "audio_meta_sha256": sha256_file(audio_path),
        "scene_asset_manifest_sha256": sha256_file(scene_manifest_path),
        "total_duration": total,
        "random_seed_sha256": seed_hex,
        "random_sample_count": random_count,
        "sample_count": len(ordered),
        "required_categories": ["opening", "chapter", "high_risk", "hero", "climax", "ending", "random", "caption_bright", "caption_dark"],
        "samples": ordered,
        "next_stage_status": "ready_for_encoded_hbg_verification",
    }


def build_encoded_frame_plan(project: Path) -> EncodedFramePlanResult:
    root = project.expanduser().resolve()
    expected = _expected_plan(root)
    path = safe_project_output(root, Path(_PLAN))
    payload = _pretty(expected)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise EncodedVisualQaError("existing encoded frame plan is stale or modified")
        return EncodedFramePlanResult("unchanged", path, expected["sample_count"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(payload)
    except OSError as error:
        raise EncodedVisualQaError(f"encoded frame plan could not be published: {error}") from error
    return EncodedFramePlanResult("created", path, expected["sample_count"])


def verify_encoded_frame_plan(project: Path, *, require_current_preflight: bool = True) -> dict[str, Any]:
    root = project.expanduser().resolve()
    path = root / _PLAN
    actual = _load(path, "encoded frame plan")
    expected = _expected_plan(root, require_current_preflight=require_current_preflight)
    if actual != expected or path.read_bytes() != _pretty(expected):
        raise EncodedVisualQaError("encoded frame plan is stale or modified")
    categories = {category for item in actual["samples"] for category in item["categories"]}
    if not set(actual["required_categories"]).issubset(categories):
        raise EncodedVisualQaError("encoded frame plan category coverage is incomplete")
    return actual


def _estimated_caption_width(text: str, font_size: float, spacing_em: float) -> float:
    units = sum(0.55 if ord(character) < 128 else 1.0 for character in text)
    gaps = max(0, len(text) - 1) * spacing_em
    return round((units + gaps) * font_size, 3)


def evaluate_caption_parity_contract(
    project: Path,
    *,
    plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    video: Path,
) -> dict[str, Any]:
    root = project.expanduser().resolve()
    video_path = video.expanduser().resolve()
    try:
        video_relative = video_path.relative_to(root).as_posix()
    except ValueError as error:
        raise EncodedVisualQaError("caption parity video must be inside the project") from error
    if video_path.is_symlink() or not video_path.is_file() or video_path.stat().st_size < 1:
        raise EncodedVisualQaError("caption parity video is missing")
    style_path = root / "HBG_STYLE.json"; style = _load(style_path, "HBG style")
    audio_path = root / "audio_meta.json"; audio = _load(audio_path, "audio metadata")
    canvas = style.get("canvas") if isinstance(style.get("canvas"), dict) else {}
    captions_style = style.get("captions") if isinstance(style.get("captions"), dict) else {}
    width = float(canvas.get("width", 0)); height = float(canvas.get("height", 0))
    font = captions_style.get("fontFamily"); font_size = float(captions_style.get("fontSize", 0))
    bottom = float(captions_style.get("bottom", 0)); box_padding = float(captions_style.get("boxPadding", 0))
    max_width = float(captions_style.get("maxWidth", 0)); spacing = float(captions_style.get("letterSpacingEm", 0))
    orientation = style.get("orientation")
    if width <= 0 or height <= 0 or orientation not in {"landscape", "portrait"}:
        raise EncodedVisualQaError("caption parity canvas is invalid")
    try:
        _verify_vendor(repository_root())
    except RuntimeError as error:
        raise EncodedVisualQaError(f"HBG vendor integrity failed: {error}") from error
    vendor = repository_root() / "vendor/hbg-life-simulation"
    html_script = vendor / "scripts/build_composition.mjs"
    ass_script = vendor / "scripts/render_streaming_ffmpeg.mjs"
    html_source = html_script.read_text(encoding="utf-8")
    ass_source = ass_script.read_text(encoding="utf-8")
    html_bound = all(token in html_source for token in (
        "captionStyle.fontFamily", "captionStyle.fontSize", "captionStyle.backgroundRgba",
        "captionStyle.bottom", "captionStyle.maxWidth", "captionStyle.htmlPadding",
    ))
    ass_bound = all(token in ass_source for token in (
        "${captionFontFamily}", "${captionFontSize}", "${captionAssBoxColor}",
        "3,${captionBoxPadding}", "${captionMarginV}", "[basev]subtitles=",
    ))
    captions = audio.get("captions") if isinstance(audio.get("captions"), list) else []
    texts = [str(item.get("text", "")) for item in captions if isinstance(item, dict)]
    if not texts:
        raise EncodedVisualQaError("caption parity requires final audio captions")
    estimated_widths = [_estimated_caption_width(line, font_size, spacing) for text in texts for line in text.splitlines()]
    line_counts = [max(1, len(text.splitlines())) for text in texts]
    ass_available_width = width - 108.0
    allowed_width = min(max_width, ass_available_width)
    sample_by_id = {item.get("sample_id"): item for item in plan.get("samples", []) if isinstance(item, dict)}
    frame_ids = {item.get("sample_id") for item in frames if isinstance(item, dict)}
    decision_by_id = {item.get("sample_id"): item for item in decisions if isinstance(item, dict)}
    bright = [item for item in sample_by_id.values() if "caption_bright" in item.get("categories", [])]
    dark = [item for item in sample_by_id.values() if "caption_dark" in item.get("categories", [])]
    caption_samples = {item["sample_id"] for item in [*bright, *dark]}
    human_pass = bool(caption_samples) and all(
        sample_id in frame_ids
        and decision_by_id.get(sample_id, {}).get("caption_status") == "pass"
        and bool(decision_by_id.get(sample_id, {}).get("caption_note"))
        for sample_id in caption_samples
    )
    safe = {"landscape": (0.04, 0.10), "portrait": (0.12, 0.20)}[str(orientation)]
    bottom_ratio = bottom / height
    checks: list[dict[str, Any]] = []

    def check(identifier: str, passed: bool, observed: Any) -> None:
        checks.append({"id": identifier, "result": "pass" if passed else "fail", "observed": observed})

    check("font_family_parity", isinstance(font, str) and bool(font) and html_bound and ass_bound, {"font": font, "html_bound": html_bound, "ass_bound": ass_bound})
    check("font_size_parity", font_size > 0 and html_bound and ass_bound, font_size)
    check("background_box_parity", bool(captions_style.get("backgroundRgba")) and bool(captions_style.get("assBoxColor")) and "BorderStyle, Outline" in ass_source, {"html": captions_style.get("backgroundRgba"), "ass": captions_style.get("assBoxColor")})
    check("padding_outline_parity", box_padding >= 8 and bool(captions_style.get("htmlPadding")) and ass_bound, {"html_padding": captions_style.get("htmlPadding"), "ass_outline": box_padding})
    check("bottom_safe_area", safe[0] <= bottom_ratio <= safe[1], round(bottom_ratio, 4))
    check("maximum_width", max(estimated_widths) <= allowed_width, {"estimated_max_text_width": max(estimated_widths), "allowed_width": allowed_width, "ass_available_width": ass_available_width})
    check("maximum_two_lines", max(line_counts) <= 2, max(line_counts))
    check("bright_dark_encoded_frames", bool(bright) and bool(dark) and {item["sample_id"] for item in bright} != {item["sample_id"] for item in dark} and caption_samples.issubset(frame_ids), sorted(caption_samples))
    check("encoded_caption_human_review", human_pass, sorted(caption_samples))
    check("subject_not_occluded", human_pass, {sample_id: decision_by_id.get(sample_id, {}).get("caption_note") for sample_id in sorted(caption_samples)})
    passed = all(item["result"] == "pass" for item in checks)
    return {
        "schema_version": "caption-parity-report.v1",
        "release_id": plan.get("release_id"),
        "video_path": video_relative,
        "video_sha256": sha256_file(video_path),
        "frame_plan_sha256": hashlib.sha256(_pretty(plan)).hexdigest(),
        "hbg_style_sha256": sha256_file(style_path),
        "audio_meta_sha256": sha256_file(audio_path),
        "hbg_vendor_lock_sha256": sha256_file(vendor / "UPSTREAM_LOCK.json"),
        "html_renderer_sha256": sha256_file(html_script),
        "ass_renderer_sha256": sha256_file(ass_script),
        "orientation": orientation,
        "canvas": {"width": int(width), "height": int(height)},
        "caption_sample_ids": sorted(caption_samples),
        "checks": checks,
        "status": "pass" if passed else "fail",
        "next_stage_status": "caption_parity_passed" if passed else "blocked_by_caption_parity",
    }


def _review_decision(
    path: Path, *, release_id: str, video_sha: str, plan_sha: str,
    sample_ids: list[str], caption_sample_ids: set[str],
) -> dict[str, Any]:
    value = _load(path, "encoded visual review decision")
    if set(value) != _DECISION_FIELDS or value.get("schema_version") != "encoded-visual-review-decision.v1":
        raise EncodedVisualQaError("encoded visual review decision fields are invalid")
    if value.get("release_id") != release_id or value.get("video_sha256") != video_sha or value.get("frame_plan_sha256") != plan_sha:
        raise EncodedVisualQaError("encoded visual review decision is bound to stale media or plan")
    reviewer = value.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise EncodedVisualQaError("encoded visual reviewer is invalid")
    decisions = value.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(sample_ids):
        raise EncodedVisualQaError("encoded visual review must cover every planned sample")
    by_id: dict[str, dict[str, Any]] = {}
    fields = {"sample_id", "semantic_status", "visual_reality_status", "identity_status", "caption_status", "note", "caption_note"}
    # §10.2 additive fields, mirroring the per-shot scene review gate: an encoded
    # sample may carry real vision evidence bound to the extracted frame, or be
    # marked as historical legacy. No other keys are permitted.
    optional_fields = {"vision_evidence", "legacy_pass"}
    for item in decisions:
        if not isinstance(item, dict):
            raise EncodedVisualQaError("encoded visual decision sample coverage is invalid")
        keys = set(item)
        if (not fields <= keys or not keys <= (fields | optional_fields)
                or item.get("sample_id") not in sample_ids or item["sample_id"] in by_id):
            raise EncodedVisualQaError("encoded visual decision sample coverage is invalid")
        if item.get("semantic_status") not in {"pass", "fail"} or item.get("visual_reality_status") not in {"pass", "fail"}:
            raise EncodedVisualQaError("semantic and visual reality decisions must be pass or fail")
        if item.get("identity_status") not in {"pass", "fail", "not_applicable"}:
            raise EncodedVisualQaError("identity decision is invalid")
        expected_caption = {"pass", "fail"} if item["sample_id"] in caption_sample_ids else {"not_applicable"}
        if item.get("caption_status") not in expected_caption:
            raise EncodedVisualQaError("caption decision does not match the encoded frame plan")
        graded = {
            "semantic_status": item.get("semantic_status"),
            "visual_reality_status": item.get("visual_reality_status"),
            "identity_status": item.get("identity_status"),
            "caption_status": item.get("caption_status"),
        }
        note = item.get("note")
        if not isinstance(note, str) or note != note.strip() or ("fail" in graded.values() and not note):
            raise EncodedVisualQaError("failed encoded visual decisions require a precise note")
        caption_note = item.get("caption_note")
        if not isinstance(caption_note, str) or caption_note != caption_note.strip() or (item["sample_id"] in caption_sample_ids and not caption_note):
            raise EncodedVisualQaError("encoded caption samples require a precise caption note")
        if "legacy_pass" in item and not isinstance(item["legacy_pass"], bool):
            raise EncodedVisualQaError("encoded visual legacy_pass must be a boolean")
        # Fail closed: an encoded sample may not advance without authoritative
        # vision evidence bound to the extracted frame, or an explicit legacy mark.
        validate_review_decision({**item, "shot_id": item["sample_id"]})
        by_id[item["sample_id"]] = dict(item)
    value["decisions"] = [by_id[sample_id] for sample_id in sample_ids]
    return value


def _frame_evidence(root: Path, plan: dict[str, Any]) -> tuple[list[dict[str, Any]], Path]:
    qa_dir = root / "09_qc/final-video"
    evidence: list[dict[str, Any]] = []
    for index, sample in enumerate(plan["samples"], start=1):
        relative = f"09_qc/final-video/{index:02d}-{sample['sample_id']}.png"
        path = _file(root, relative, f"encoded sample {sample['sample_id']}")
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
        except OSError as error:
            raise EncodedVisualQaError(f"encoded sample is not a decodable image: {sample['sample_id']}") from error
        evidence.append({
            "sample_id": sample["sample_id"], "path": relative, "sha256": sha256_file(path),
            "bytes": path.stat().st_size, "width": width, "height": height,
            "seconds": sample["seconds"], "categories": sample["categories"],
        })
    contact = _file(root, "09_qc/final-video/contact-sheet.jpg", "HBG encoded contact sheet")
    return evidence, contact


def review_encoded_master(project: Path, decision_path: Path) -> EncodedVisualReviewResult:
    root = project.expanduser().resolve()
    plan = verify_encoded_frame_plan(root, require_current_preflight=False)
    plan_path = root / _PLAN
    final_path = root / "08_render_合成/final/FINAL_RENDER_MANIFEST.json"
    final = _load(final_path, "final render manifest")
    if final.get("schema_version") != "final-render-manifest.v1" or final.get("next_stage_status") not in {
        "awaiting_encoded_visual_review", "blocked_by_encoded_visual_qa", "awaiting_final_master_approval"
    }:
        raise EncodedVisualQaError("encoded master is not awaiting semantic visual review")
    video = _file(root, final.get("video_path"), "encoded master")
    if sha256_file(video) != final.get("video_sha256"):
        raise EncodedVisualQaError("encoded master hash is stale")
    qa_path = _file(root, final.get("qa_report_path"), "technical final QA report")
    qa = _load(qa_path, "technical final QA report")
    if qa.get("technical_status") != "pass":
        raise EncodedVisualQaError("technical HBG QA has not passed")
    plan_sha = sha256_file(plan_path)
    if qa.get("encoded_frame_plan_sha256") != plan_sha or final.get("encoded_frame_plan_sha256") != plan_sha:
        raise EncodedVisualQaError("technical QA did not use the current encoded frame plan")
    decision_resolved = decision_path.expanduser().resolve()
    try:
        decision_resolved.relative_to(root)
    except ValueError as error:
        raise EncodedVisualQaError("encoded visual review decision must be inside the project") from error
    decision = _review_decision(
        decision_resolved,
        release_id=final["release_id"], video_sha=sha256_file(video), plan_sha=plan_sha,
        sample_ids=[item["sample_id"] for item in plan["samples"]],
        caption_sample_ids={
            item["sample_id"] for item in plan["samples"]
            if {"caption_bright", "caption_dark"} & set(item["categories"])
        },
    )
    frames, hbg_contact = _frame_evidence(root, plan)
    visual_pass = all(
        item["semantic_status"] == "pass"
        and item["visual_reality_status"] == "pass"
        and item["identity_status"] != "fail"
        for item in decision["decisions"]
    )
    caption_report = evaluate_caption_parity_contract(
        root, plan=plan, decisions=decision["decisions"], frames=frames, video=video,
    )
    all_pass = visual_pass and caption_report["status"] == "pass"
    report_path = safe_project_output(root, Path(_REPORT))
    contact_path = safe_project_output(root, Path(_CONTACT))
    caption_path = safe_project_output(root, Path(_CAPTION))
    if report_path.exists() or contact_path.exists() or caption_path.exists():
        if not (report_path.is_file() and contact_path.is_file() and caption_path.is_file()):
            raise EncodedVisualQaError("encoded visual review transaction is incomplete")
        existing = _load(report_path, "encoded visual QA report")
        if (
            existing.get("decision_sha256") != sha256_file(decision_resolved)
            or existing.get("video_sha256") != sha256_file(video)
            or existing.get("frame_plan_sha256") != plan_sha
            or existing.get("final_contact_sheet_sha256") != sha256_file(contact_path)
            or final.get("encoded_visual_qa_sha256") != sha256_file(report_path)
            or final.get("final_contact_sheet_sha256") != sha256_file(contact_path)
            or existing.get("next_stage_status") != final.get("next_stage_status")
            or existing.get("caption_parity_sha256") != sha256_file(caption_path)
            or final.get("caption_parity_sha256") != sha256_file(caption_path)
        ):
            raise EncodedVisualQaError("existing encoded visual QA is stale or modified")
        if existing.get("status") == "pass" and qa.get("status") != "pass":
            raise EncodedVisualQaError("passing encoded visual QA is not bound by final QA")
        return EncodedVisualReviewResult("unchanged", report_path, contact_path, existing["next_stage_status"])
    if final.get("next_stage_status") != "awaiting_encoded_visual_review" or qa.get("status") != "awaiting_encoded_visual_review":
        raise EncodedVisualQaError("technical HBG QA is not awaiting encoded visual review")

    event_path: Path | None = None
    old_final = final_path.read_bytes(); old_qa = qa_path.read_bytes()
    try:
        with tempfile.TemporaryDirectory(prefix="encoded-visual-review-", dir=root / "09_qc") as temp:
            staging = Path(temp)
            staged_contact = staging / "contact-sheet.jpg"
            shutil.copy2(hbg_contact, staged_contact)
            if sha256_file(staged_contact) != sha256_file(hbg_contact):
                raise EncodedVisualQaError("final contact sheet copy changed bytes")
            subjects = [plan_path, decision_resolved, video, hbg_contact]
            subjects.extend(root / item["path"] for item in frames)
            event_path = record_approval(
                root,
                release_id=final["release_id"],
                gate="encoded_visual_qa",
                decision="approved" if all_pass else "rejected",
                reviewer=decision["reviewer"],
                subjects=subjects,
                evidence_refs=[_PLAN, "09_qc/final-video/contact-sheet.jpg"],
                note="Encoded semantic, visual reality, and identity frame review.",
            )
            report = {
                "schema_version": "encoded-visual-qa.v1",
                "release_id": final["release_id"],
                "video_path": final["video_path"],
                "video_sha256": sha256_file(video),
                "frame_plan_path": _PLAN,
                "frame_plan_sha256": plan_sha,
                "decision_path": decision_resolved.relative_to(root).as_posix(),
                "decision_sha256": sha256_file(decision_resolved),
                "reviewer": decision["reviewer"],
                "decisions": decision["decisions"],
                "frames": frames,
                "hbg_contact_sheet_sha256": sha256_file(hbg_contact),
                "final_contact_sheet_path": _CONTACT,
                "final_contact_sheet_sha256": sha256_file(staged_contact),
                "caption_parity_path": _CAPTION,
                "caption_parity_sha256": hashlib.sha256(_pretty(caption_report)).hexdigest(),
                "approval_event_path": event_path.relative_to(root).as_posix(),
                "approval_event_sha256": sha256_file(event_path),
                "human_review_passed": all_pass,
                "status": "pass" if all_pass else "fail",
                "next_stage_status": "awaiting_final_master_approval" if all_pass else "blocked_by_encoded_visual_qa",
            }
            report_bytes = _pretty(report)
            staged_report = staging / "encoded.json"; staged_report.write_bytes(report_bytes)
            staged_caption = staging / "caption.json"; staged_caption.write_bytes(_pretty(caption_report))
            if all_pass:
                qa["status"] = "pass"
                qa["encoded_visual_qa_path"] = _REPORT
                qa["encoded_visual_qa_sha256"] = hashlib.sha256(report_bytes).hexdigest()
                qa["final_contact_sheet_path"] = _CONTACT
                qa["final_contact_sheet_sha256"] = sha256_file(staged_contact)
                qa["caption_parity_path"] = _CAPTION
                qa["caption_parity_sha256"] = sha256_file(staged_caption)
                qa_bytes = _pretty(qa)
                final["qa_report_sha256"] = hashlib.sha256(qa_bytes).hexdigest()
            else:
                qa_bytes = old_qa
            final["encoded_visual_qa_path"] = _REPORT
            final["encoded_visual_qa_sha256"] = hashlib.sha256(report_bytes).hexdigest()
            final["final_contact_sheet_path"] = _CONTACT
            final["final_contact_sheet_sha256"] = sha256_file(staged_contact)
            final["caption_parity_path"] = _CAPTION
            final["caption_parity_sha256"] = sha256_file(staged_caption)
            final["next_stage_status"] = report["next_stage_status"]
            staged_qa = staging / "qa.json"; staged_qa.write_bytes(qa_bytes)
            staged_final = staging / "final.json"; staged_final.write_bytes(_pretty(final))
            report_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_contact, contact_path)
            os.replace(staged_caption, caption_path)
            os.replace(staged_report, report_path)
            if all_pass:
                os.replace(staged_qa, qa_path)
            os.replace(staged_final, final_path)
    except Exception as error:
        report_path.unlink(missing_ok=True); contact_path.unlink(missing_ok=True); caption_path.unlink(missing_ok=True)
        final_path.write_bytes(old_final); qa_path.write_bytes(old_qa)
        if event_path is not None:
            event_path.unlink(missing_ok=True)
        if isinstance(error, EncodedVisualQaError):
            raise
        raise EncodedVisualQaError(f"encoded visual review transaction failed: {error}") from error
    return EncodedVisualReviewResult("created", report_path, contact_path, report["next_stage_status"])
