from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from book_video_factory.director_stage.compiler import DirectorStageError, compile_director_stage
from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.hbg_bridge.shell import bash_executable, path_for_bash
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.production_visuals.registry import (
    SceneAssetError,
    _load as load_scene_json,
    _manifest as load_scene_manifest,
    _tasks,
)
from book_video_factory.render_stage.qa import evaluate_final_video


class RenderStageError(RuntimeError):
    """Phase 7 render inputs or HBG output are incomplete or stale."""


class VisualSemanticAlignmentError(RenderStageError):
    """Current scene/task/image/anchor evidence is incomplete or stale."""


@dataclass(frozen=True)
class RenderStageResult:
    status: str
    render_manifest_path: Path
    workspace: Path
    output_path: Path | None
    next_stage_status: str


@dataclass(frozen=True)
class OpeningPreviewResult:
    status: str
    manifest_path: Path
    output_path: Path
    next_stage_status: str


RenderRunner = Callable[[Path, Path, dict[str, Any]], None]
PreviewRunner = Callable[[Path, Path, dict[str, Any]], None]
QaRunner = Callable[[Path, Path, list[tuple[float, str]]], None]


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RenderStageError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderStageError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise RenderStageError(f"{label} must be an object")
    return value


def _project_media(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise RenderStageError(f"{label} path is invalid")
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise RenderStageError(f"{label} is missing, empty, or symlinked: {relative}")
    return path


def _render_input(
    root: Path, input_path: Path, release_id: str, tasks: Mapping[str, Any], *, require_preview: bool = True,
) -> dict[str, Any]:
    value = _load(input_path, "render stage input")
    expected = {
        "schema_version", "release_id", "renderer", "output_name", "bgm_source",
        "opening", "quality", "minimum_free_gib", "hyperframes_version",
    }
    if set(value) != expected or value.get("schema_version") != "render-stage-input.v1":
        raise RenderStageError("render stage input fields are invalid")
    if value.get("release_id") != release_id:
        raise RenderStageError("render stage input release is stale")
    if value.get("renderer") not in {"streaming_ffmpeg", "hyperframes"}:
        raise RenderStageError("renderer must be streaming_ffmpeg or hyperframes")
    output_name = value.get("output_name")
    if not isinstance(output_name, str) or not output_name.endswith(".mp4") or Path(output_name).name != output_name:
        raise RenderStageError("output_name must be a simple .mp4 filename")
    if value.get("quality") not in {"draft", "standard", "high"}:
        raise RenderStageError("render quality is invalid")
    minimum = value.get("minimum_free_gib")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise RenderStageError("minimum_free_gib must be a positive integer")
    version = value.get("hyperframes_version")
    if not isinstance(version, str) or not __import__("re").fullmatch(r"\d+\.\d+\.\d+", version):
        raise RenderStageError("hyperframes_version must be pinned exactly")
    _project_media(root, value.get("bgm_source"), "BGM source")
    opening = value.get("opening")
    if not isinstance(opening, dict) or set(opening) != {
        "preview_video", "final_image_task_id", "flash_task_ids",
    }:
        raise RenderStageError("render opening fields are invalid")
    preview = opening.get("preview_video")
    if preview is not None:
        if not isinstance(preview, str) or not preview or preview != preview.strip():
            raise RenderStageError("opening preview path is invalid")
        safe_project_output(root, Path(preview))
    if require_preview and value["renderer"] == "streaming_ffmpeg":
        _verify_opening_preview(root, preview, release_id, input_path=input_path)
    elif require_preview and preview is not None:
        _project_media(root, preview, "opening preview video")
    final_task = opening.get("final_image_task_id")
    flash_tasks = opening.get("flash_task_ids")
    if final_task not in tasks:
        raise RenderStageError("opening final image task is unknown")
    if not isinstance(flash_tasks, list) or not 1 <= len(flash_tasks) <= 9 or any(item not in tasks for item in flash_tasks):
        raise RenderStageError("opening flash task IDs must contain 1-9 known scene tasks")
    if require_preview:
        from book_video_factory.render_stage.mix_calibration import MixCalibrationError, verify_opening_mix_approval
        try:
            verify_opening_mix_approval(root, input_path)
        except MixCalibrationError as error:
            raise RenderStageError(f"opening mix does not have current human approval: {error}") from error
    return value


def _opening_preview_binding(root: Path, input_path: Path) -> dict[str, str]:
    paths = {
        "render_input_sha256": input_path,
        "scene_approval_sha256": root / "06_visual_production/SCENE_ASSET_APPROVAL.json",
        "audio_stage_sha256": root / "04_audio/AUDIO_STAGE_MANIFEST.json",
        "hbg_style_sha256": root / "HBG_STYLE.json",
        "project_spec_sha256": root / "PROJECT_SPEC.json",
        "hbg_vendor_lock_sha256": repository_root() / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json",
    }
    result: dict[str, str] = {}
    for key, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise RenderStageError(f"opening preview evidence is missing: {path}")
        result[key] = sha256_file(path)
    return result


def _verify_opening_preview(
    root: Path, relative: Any, release_id: str, *, input_path: Path,
) -> dict[str, Any]:
    preview = _project_media(root, relative, "HBG opening preview video")
    manifest_path = root / "07_render/OPENING_PREVIEW_MANIFEST.json"
    manifest = _load(manifest_path, "opening preview manifest")
    expected_fields = {
        "schema_version", "release_id", "render_input_sha256",
        "scene_approval_sha256", "audio_stage_sha256", "hbg_style_sha256",
        "project_spec_sha256", "preview_path", "preview_sha256",
        "preview_bytes", "renderer", "hbg_vendor_lock_sha256",
        "next_stage_status",
    }
    if set(manifest) != expected_fields or manifest.get("schema_version") != "opening-preview-manifest.v1":
        raise RenderStageError("opening preview manifest fields are invalid")
    if manifest.get("release_id") != release_id or manifest.get("preview_path") != relative:
        raise RenderStageError("opening preview manifest belongs to different inputs")
    if manifest.get("renderer") != "hbg-hyperframes-opening-preview":
        raise RenderStageError("opening preview was not produced by the HBG HyperFrames route")
    if manifest.get("preview_bytes") != preview.stat().st_size or manifest.get("preview_sha256") != sha256_file(preview):
        raise RenderStageError("opening preview video was modified")
    binding = _opening_preview_binding(root, input_path)
    for key, expected in binding.items():
        if manifest.get(key) != expected:
            raise RenderStageError(f"opening preview evidence is stale: {key}")
    if manifest.get("next_stage_status") != "awaiting_opening_mix_calibration":
        raise RenderStageError("opening preview manifest status is invalid")
    return manifest


def _scene_approval(root: Path, director_sha: str, tasks: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    approval_path = root / "06_visual_production/SCENE_ASSET_APPROVAL.json"
    approval = _load(approval_path, "scene asset approval")
    if approval.get("schema_version") != "scene-asset-approval.v1" or not approval.get("human_approved") or approval.get("next_stage_status") != "ready_for_render":
        raise VisualSemanticAlignmentError("scene assets do not have current human approval")
    manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    manifest = load_scene_manifest(root, approval.get("release_id"), director_sha, len(tasks))
    if approval.get("scene_asset_manifest_sha256") != sha256_file(manifest_path):
        raise VisualSemanticAlignmentError("scene asset approval is stale")
    by_task = {item["task_id"]: item for item in manifest["assets"]}
    if set(by_task) != set(tasks) or approval.get("asset_hashes") != {task_id: by_task[task_id]["sha256"] for task_id in tasks}:
        raise VisualSemanticAlignmentError("scene asset approval does not bind every current image")
    event = _project_media(root, approval.get("approval_event_path"), "scene approval event")
    if sha256_file(event) != approval.get("approval_event_sha256"):
        raise VisualSemanticAlignmentError("scene approval event is stale")
    return approval, manifest


def _copy(root: Path, workspace: Path, relative: str) -> Path:
    source = _project_media(root, relative, relative)
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256_file(source) != sha256_file(target):
        raise RenderStageError(f"render workspace copy changed bytes: {relative}")
    return target


def _current_director_storyboard(root: Path) -> list[dict[str, Any]]:
    timeline_path = root / "05_director/DIRECTOR_TIMELINE.json"
    timeline = _load(timeline_path, "current Director timeline")
    if timeline.get("schema_version") != "director-timeline.v1":
        raise RenderStageError("current Director timeline schema is invalid")
    scenes = timeline.get("scenes")
    if not isinstance(scenes, list) or not scenes or timeline.get("scene_count") != len(scenes):
        raise RenderStageError("current Director timeline scene set is invalid")
    audio = _load(root / "audio_meta.json", "audio metadata")
    body = audio.get("body")
    opening = audio.get("opening")
    if not isinstance(body, Mapping) or not isinstance(opening, Mapping):
        raise RenderStageError("audio metadata has no body timeline")
    if float(timeline.get("body_start", -1)) != float(opening.get("bodyStart", -2)) or float(
        timeline.get("body_duration", -1)
    ) != float(body.get("duration", -2)):
        raise RenderStageError("current Director timeline is not bound to the audio timeline")
    if timeline.get("audio_meta_sha256") is not None and timeline.get("audio_meta_sha256") != sha256_file(
        root / "audio_meta.json"
    ):
        raise RenderStageError("current Director timeline audio metadata evidence is stale")

    rendered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, Mapping):
            raise RenderStageError("current Director timeline contains an invalid scene")
        scene_id = str(scene.get("scene_id", ""))
        caption_ids = scene.get("caption_ids")
        start = scene.get("start")
        end = scene.get("end")
        duration = scene.get("duration")
        if (
            not scene_id
            or scene_id in seen
            or not isinstance(caption_ids, list)
            or not caption_ids
            or isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or abs(float(end) - float(start) - float(duration)) > 0.002
        ):
            raise RenderStageError(f"current Director timeline scene is invalid: {scene_id or '?'}")
        seen.add(scene_id)
        rendered.append({
            "id": scene_id,
            "sourceBeatIds": list(scene.get("source_beat_ids", [])),
            "chapter": int(scene.get("chapter", 0)),
            "start": float(start),
            "end": float(end),
            "duration": float(duration),
            "captionIds": [str(item) for item in caption_ids],
            "cue": str(scene.get("narrative_cue", "")),
            "description": str(scene.get("visual_description", "")),
            "semanticRationale": str(scene.get("semantic_rationale", "")),
            "requiredEntities": list(scene.get("required_entities", [])),
            "forbiddenEntities": list(scene.get("forbidden_entities", [])),
            "riskFlags": list(scene.get("risk_flags", [])),
            "anchorRefs": list(scene.get("anchor_refs", [])),
            "participants": dict(scene.get("participants", {})),
            "motion": str(scene.get("motion", "hold")),
            "generationMode": str(scene.get("generation_mode", "single")),
        })
    return rendered


def _workspace_payloads(root: Path, render_input: Mapping[str, Any], manifest: Mapping[str, Any]) -> tuple[dict[str, bytes], list[str]]:
    storyboard = _current_director_storyboard(root)
    by_scene = {item["scene_id"]: item for item in manifest["assets"]}
    if set(by_scene) != {item.get("id") for item in storyboard}:
        raise VisualSemanticAlignmentError("scene assets and current Director timeline differ")
    rendered_storyboard: list[dict[str, Any]] = []
    scene_paths: list[str] = []
    for scene in storyboard:
        asset = by_scene[scene["id"]]
        item = copy.deepcopy(scene)
        item["asset"] = asset["path"]
        rendered_storyboard.append(item)
        scene_paths.append(asset["path"])
    spec = _load(root / "PROJECT_SPEC.json", "project spec")
    audio = _load(root / "audio_meta.json", "audio metadata")
    final_asset = next(item for item in manifest["assets"] if item["task_id"] == render_input["opening"]["final_image_task_id"])
    flash_assets = [next(item for item in manifest["assets"] if item["task_id"] == task_id) for task_id in render_input["opening"]["flash_task_ids"]]
    spec = copy.deepcopy(spec)
    spec.setdefault("opening", {})
    spec["opening"].update({
        "finalImage": final_asset["path"],
        "flashLives": [
            {"label": f"名著镜头 {index}", "asset": item["path"]}
            for index, item in enumerate(flash_assets, start=1)
        ],
    })
    spec.setdefault("audio", {})
    spec["audio"].update({
        "bgmSource": render_input["bgm_source"],
        "bgmLooped": "assets/audio/bgm/looped.m4a",
        "narrationOutput": audio.get("body", {}).get("path"),
    })
    audio = copy.deepcopy(audio)
    audio.setdefault("bgm", {})["path"] = "assets/audio/bgm/looped.m4a"
    audio.setdefault("opening", {})["previewVideo"] = render_input["opening"]["preview_video"]
    payloads = {
        "PROJECT_SPEC.json": _pretty(spec),
        "STORYBOARD.json": _pretty(rendered_storyboard),
        "audio_meta.json": _pretty(audio),
        "05_director/DIRECTOR_TIMELINE.json": (root / "05_director/DIRECTOR_TIMELINE.json").read_bytes(),
        "package.json": _pretty({
            "private": True,
            "scripts": {"render": f"npx --yes hyperframes@{render_input['hyperframes_version']} render"},
        }),
    }
    return payloads, sorted(set(scene_paths))




def _populate_workspace(
    root: Path, staging: Path, render_input: Mapping[str, Any], scene_manifest: Mapping[str, Any], *, include_preview: bool,
) -> tuple[dict[str, bytes], list[str]]:
    payloads, scene_paths = _workspace_payloads(root, render_input, scene_manifest)
    for relative in ("SCRIPT_SOURCE.md", "SCRIPT.md", "CHARACTERS.md", "HBG_STYLE.json"):
        _copy(root, staging, relative)
    caption_bindings = root / "04_audio/CAPTION_BINDINGS.json"
    if caption_bindings.is_file() and not caption_bindings.is_symlink():
        _copy(root, staging, "04_audio/CAPTION_BINDINGS.json")
    for relative in scene_paths:
        _copy(root, staging, relative)
    audio_meta = json.loads(payloads["audio_meta.json"])
    audio_paths = [
        audio_meta.get("body", {}).get("path"),
        audio_meta.get("opening", {}).get("lead", {}).get("path"),
        audio_meta.get("opening", {}).get("reveal", {}).get("path"),
        audio_meta.get("opening", {}).get("flash", {}).get("audio"),
        render_input["bgm_source"],
    ]
    body_vtt = audio_meta.get("body", {}).get("vtt")
    if isinstance(body_vtt, str) and body_vtt:
        audio_paths.append(body_vtt)
    if include_preview and render_input["opening"]["preview_video"]:
        audio_paths.append(render_input["opening"]["preview_video"] )
    for relative in sorted({item for item in audio_paths if isinstance(item, str) and item}):
        _copy(root, staging, relative)
    for relative, data in payloads.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return payloads, scene_paths


def _verify_workspace(workspace: Path, manifest: Mapping[str, Any]) -> None:
    hashes = manifest.get("workspace_file_hashes")
    if not isinstance(hashes, Mapping) or not hashes:
        raise RenderStageError("render workspace hash table is missing")
    def is_recoverable_render_work(path: Path) -> bool:
        relative = path.relative_to(workspace)
        return (
            len(relative.parts) >= 2
            and relative.parts[0] == "renders"
            and (relative.parts[1].startswith("work-") or relative.parts[1].startswith("ffmpeg-work-"))
        )

    actual = {
        path.relative_to(workspace).as_posix(): sha256_file(path)
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink() and not is_recoverable_render_work(path)
    }
    if actual != dict(hashes):
        raise RenderStageError("render workspace contents differ from the locked manifest")

def prepare_render_stage(project: Path, input_path: Path) -> RenderStageResult:
    root = project.expanduser().resolve()
    try:
        director = compile_director_stage(root)
    except DirectorStageError as error:
        raise RenderStageError(f"director stage is not current: {error}") from error
    try:
        tasks = _tasks(root)
    except SceneAssetError as error:
        raise VisualSemanticAlignmentError(f"production image tasks are not current: {error}") from error
    director_sha = sha256_file(director.manifest_path)
    try:
        approval, scene_manifest = _scene_approval(root, director_sha, tasks)
    except SceneAssetError as error:
        raise VisualSemanticAlignmentError(f"scene image or identity evidence is not current: {error}") from error
    render_input = _render_input(root, input_path.expanduser().resolve(), approval["release_id"], tasks)
    input_hashes = {
        "render_input": sha256_file(input_path.expanduser().resolve()),
        "director_stage": director_sha,
        "scene_approval": sha256_file(root / "06_visual_production/SCENE_ASSET_APPROVAL.json"),
        "scene_manifest": sha256_file(root / "06_visual_production/SCENE_ASSET_MANIFEST.json"),
        "audio_stage": sha256_file(root / "04_audio/AUDIO_STAGE_MANIFEST.json"),
        "hbg_style": sha256_file(root / "HBG_STYLE.json"),
        "opening_mix_approval": sha256_file(root / "07_render/OPENING_MIX_APPROVAL.json"),
    }
    digest = _sha(_canonical(input_hashes))
    workspace = safe_project_output(root, Path(f"07_render/workspaces/{digest[:16]}"))
    render_manifest_path = safe_project_output(root, Path("07_render/RENDER_MANIFEST.json"))
    if render_manifest_path.exists():
        existing = _load(render_manifest_path, "render manifest")
        if existing.get("input_digest") != digest:
            raise RenderStageError("existing render manifest is bound to different inputs")
        if not workspace.is_dir():
            raise RenderStageError("render workspace disappeared")
        _verify_workspace(workspace, existing)
        return RenderStageResult("unchanged", render_manifest_path, workspace, None, existing["next_stage_status"])
    if workspace.exists():
        raise RenderStageError("unmanaged render workspace already exists")
    render_root = root / "07_render"
    render_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="book-video-render-", dir=render_root) as temp:
        staging = Path(temp) / "workspace"
        staging.mkdir(parents=True)
        _populate_workspace(root, staging, render_input, scene_manifest, include_preview=True)
        manifest = {
            "schema_version": "render-manifest.v1",
            "release_id": approval["release_id"],
            "input_digest": digest,
            "renderer": render_input["renderer"],
            "quality": render_input["quality"],
            "minimum_free_gib": render_input["minimum_free_gib"],
            "output_name": render_input["output_name"],
            "workspace": workspace.relative_to(root).as_posix(),
            "input_hashes": input_hashes,
            "workspace_file_hashes": {
                path.relative_to(staging).as_posix(): sha256_file(path)
                for path in staging.rglob("*") if path.is_file()
            },
            "next_stage_status": "awaiting_render_preflight",
        }
        workspace.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, workspace)
        render_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        render_manifest_path.write_bytes(_pretty(manifest))
    return RenderStageResult("created", render_manifest_path, workspace, None, "awaiting_render_preflight")


def _hbg_bash_command(script: Path, *arguments: Path | str) -> list[str]:
    bash = bash_executable()
    converted = [
        path_for_bash(argument) if isinstance(argument, Path) else argument
        for argument in (script, *arguments)
    ]
    if os.name == "nt" and Path(bash).name.lower() == "bash.exe":
        return [
            bash,
            "-c",
            'pwd() { builtin pwd -W; }; export -f pwd; exec "$@"',
            "hbg-script",
            *converted,
        ]
    return [bash, *converted]


def _default_preview_runner(workspace: Path, output: Path, manifest: dict[str, Any]) -> None:
    root = repository_root()
    try:
        _verify_vendor(root)
    except RuntimeError as error:
        raise RenderStageError(f"HBG vendor integrity failed: {error}") from error
    scripts = root / "vendor/hbg-life-simulation/scripts"

    def run(command: list[str], env: dict[str, str] | None = None) -> None:
        completed = subprocess.run(
            command, cwd=workspace, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RenderStageError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {command}")

    run(["node", str(scripts / "build_composition.mjs")])
    preview_html = workspace / "qa/opening-bgm-preview.html"
    if preview_html.is_symlink() or not preview_html.is_file():
        raise RenderStageError("HBG did not create the opening preview composition")
    shutil.copy2(preview_html, workspace / "index.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HBG_RENDER_QUALITY"] = "standard"
    env["HBG_MIN_FREE_GIB"] = str(manifest["minimum_free_gib"])
    run(_hbg_bash_command(scripts / "render_long_video.sh", workspace, output), env)
    if output.is_symlink() or not output.is_file() or output.stat().st_size == 0:
        raise RenderStageError("HBG opening preview renderer did not create a nonempty MP4")
    relative = manifest["preview_path"]
    workspace_preview = workspace / relative
    workspace_preview.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output, workspace_preview)
    audio_meta_path = workspace / "audio_meta.json"
    audio_meta = _load(audio_meta_path, "opening preview audio metadata")
    audio_meta.setdefault("opening", {})["previewVideo"] = relative
    audio_meta_path.write_bytes(_pretty(audio_meta))
    run(["node", str(scripts / "validate_style_system.mjs"), str(workspace)])


def generate_opening_preview(
    project: Path, input_path: Path, *, preview_runner: PreviewRunner | None = None,
) -> OpeningPreviewResult:
    root = project.expanduser().resolve()
    try:
        director = compile_director_stage(root)
    except DirectorStageError as error:
        raise RenderStageError(f"director stage is not current: {error}") from error
    tasks = _tasks(root)
    director_sha = sha256_file(director.manifest_path)
    approval, scene_manifest = _scene_approval(root, director_sha, tasks)
    resolved_input = input_path.expanduser().resolve()
    render_input = _render_input(root, resolved_input, approval["release_id"], tasks, require_preview=False)
    preview_relative = render_input["opening"].get("preview_video")
    if not isinstance(preview_relative, str) or not preview_relative:
        raise RenderStageError("opening.preview_video must name the generated preview target")
    preview_path = safe_project_output(root, Path(preview_relative))
    manifest_path = safe_project_output(root, Path("07_render/OPENING_PREVIEW_MANIFEST.json"))
    expected_binding = _opening_preview_binding(root, resolved_input)
    if manifest_path.exists():
        manifest = _verify_opening_preview(root, preview_relative, approval["release_id"], input_path=resolved_input)
        if manifest.get("render_input_sha256") != expected_binding["render_input_sha256"]:
            raise RenderStageError("opening preview belongs to a different render input")
        return OpeningPreviewResult("unchanged", manifest_path, preview_path, "awaiting_opening_mix_calibration")
    if preview_path.exists():
        raise RenderStageError("unmanaged opening preview exists without a manifest")
    staging_output = preview_path.with_name(f".{preview_path.stem}.staging{preview_path.suffix}")
    if staging_output.exists():
        raise RenderStageError("stale opening preview staging file exists")
    preview_manifest = {
        "schema_version": "opening-preview-manifest.v1",
        "release_id": approval["release_id"],
        **expected_binding,
        "preview_path": preview_relative,
        "preview_sha256": "",
        "preview_bytes": 0,
        "renderer": "hbg-hyperframes-opening-preview",
        "hbg_vendor_lock_sha256": expected_binding["hbg_vendor_lock_sha256"],
        "next_stage_status": "awaiting_opening_mix_calibration",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="hbg-opening-preview-", dir=root / "07_render") as temp:
            workspace = Path(temp) / "project"
            workspace.mkdir(parents=True)
            _populate_workspace(root, workspace, render_input, scene_manifest, include_preview=False)
            (preview_runner or _default_preview_runner)(workspace, staging_output, {
                **preview_manifest,
                "minimum_free_gib": render_input["minimum_free_gib"],
            })
        if staging_output.is_symlink() or not staging_output.is_file() or staging_output.stat().st_size == 0:
            raise RenderStageError("opening preview runner did not create a nonempty MP4")
        preview_manifest["preview_sha256"] = sha256_file(staging_output)
        preview_manifest["preview_bytes"] = staging_output.stat().st_size
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_output, preview_path)
        manifest_path.write_bytes(_pretty(preview_manifest))
    except Exception:
        staging_output.unlink(missing_ok=True)
        preview_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return OpeningPreviewResult("created", manifest_path, preview_path, "awaiting_opening_mix_calibration")


def _default_render_runner(workspace: Path, output: Path, manifest: dict[str, Any]) -> None:
    root = repository_root()
    try:
        _verify_vendor(root)
    except RuntimeError as error:
        raise RenderStageError(f"HBG vendor integrity failed: {error}") from error
    scripts = root / "vendor/hbg-life-simulation/scripts"
    def run(command: list[str], env: dict[str, str] | None = None) -> None:
        completed = subprocess.run(
            command, cwd=workspace, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RenderStageError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {command}")
    run(["node", str(scripts / "build_composition.mjs")])
    run(["node", str(scripts / "validate_style_system.mjs"), str(workspace)])
    output.parent.mkdir(parents=True, exist_ok=True)
    if manifest["renderer"] == "streaming_ffmpeg":
        env = os.environ.copy(); env["HBG_VALIDATE_ONLY"] = "1"
        run(["node", str(scripts / "render_streaming_ffmpeg.mjs"), str(workspace), str(output)], env)
        run(["node", str(scripts / "render_streaming_ffmpeg.mjs"), str(workspace), str(output)])
    else:
        env = os.environ.copy(); env["HBG_RENDER_QUALITY"] = manifest["quality"]; env["HBG_MIN_FREE_GIB"] = str(manifest["minimum_free_gib"])
        run(_hbg_bash_command(scripts / "render_long_video.sh", workspace, output), env)


def _default_qa_runner(video: Path, qa_dir: Path, frames: list[tuple[float, str]]) -> None:
    root = repository_root(); script = root / "vendor/hbg-life-simulation/scripts/verify_final_video.sh"
    command = _hbg_bash_command(script, video, qa_dir, *[f"{seconds}:{label}" for seconds, label in frames])
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RenderStageError(completed.stderr.strip() or completed.stdout.strip() or "HBG final QA failed")


def execute_render_stage(
    project: Path,
    input_path: Path,
    *,
    render_runner: RenderRunner | None = None,
    qa_runner: QaRunner | None = None,
) -> RenderStageResult:
    root = project.expanduser().resolve()
    prepared = prepare_render_stage(root, input_path)
    manifest = _load(prepared.render_manifest_path, "render manifest")
    _verify_workspace(prepared.workspace, manifest)
    from book_video_factory.render_stage.preflight import RenderPreflightError, verify_render_preflight
    try:
        render_preflight = verify_render_preflight(root)
    except RenderPreflightError as error:
        raise RenderStageError(f"render preflight is not current and passing: {error}") from error
    from book_video_factory.render_stage.encoded_visual_qa import EncodedVisualQaError, verify_encoded_frame_plan
    try:
        encoded_frame_plan = verify_encoded_frame_plan(root)
    except EncodedVisualQaError as error:
        raise RenderStageError(f"encoded frame plan is not current: {error}") from error
    output = safe_project_output(root, Path(f"08_render_合成/final/{manifest['output_name']}"))
    final_manifest_path = safe_project_output(root, Path("08_render_合成/final/FINAL_RENDER_MANIFEST.json"))
    if final_manifest_path.exists():
        final = _load(final_manifest_path, "final render manifest")
        if final.get("render_manifest_sha256") != sha256_file(prepared.render_manifest_path):
            raise RenderStageError("final render belongs to different inputs")
        if final.get("render_preflight_sha256") != sha256_file(root / "07_render/RENDER_PREFLIGHT.json"):
            raise RenderStageError("final render preflight evidence is stale")
        qa_report = _project_media(root, final.get("qa_report_path"), "final QA report")
        if (
            output.is_file()
            and sha256_file(output) == final.get("video_sha256")
            and sha256_file(qa_report) == final.get("qa_report_sha256")
        ):
            return RenderStageResult(
                "unchanged", prepared.render_manifest_path, prepared.workspace,
                output, str(final.get("next_stage_status", "blocked_by_final_integrity")),
            )
        raise RenderStageError("final render evidence is stale")
    if output.exists():
        raise RenderStageError("unmanaged final output exists without a final render manifest")
    qa_dir = safe_project_output(root, Path("09_qc/final-video"))
    if qa_dir.exists() and any(qa_dir.iterdir()):
        raise RenderStageError("unmanaged final QA evidence exists")
    staging_video = output.with_name(f".{output.stem}.staging{output.suffix}")
    staging_qa = safe_project_output(root, Path("09_qc/.final-video-staging"))
    report_path = safe_project_output(root, Path("09_qc/FINAL_QA_REPORT.json"))
    if staging_video.exists() or staging_qa.exists():
        raise RenderStageError("stale render staging evidence exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_qa.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="hbg-render-execution-", dir=root / "07_render") as execution_temp:
            execution_workspace = Path(execution_temp) / "project"
            shutil.copytree(prepared.workspace, execution_workspace)
            copied = {
                path.relative_to(execution_workspace).as_posix(): sha256_file(path)
                for path in execution_workspace.rglob("*") if path.is_file() and not path.is_symlink()
            }
            if copied != manifest["workspace_file_hashes"]:
                raise RenderStageError("render execution copy differs from locked workspace")
            (render_runner or _default_render_runner)(execution_workspace, staging_video, manifest)
            if staging_video.is_symlink() or not staging_video.is_file() or staging_video.stat().st_size == 0:
                raise RenderStageError("HBG renderer did not create a nonempty MP4")
            audio_meta = _load(execution_workspace / "audio_meta.json", "render audio metadata")
            total = float(audio_meta.get("totalDuration", 0))
        frames = [(float(item["seconds"]), str(item["sample_id"])) for item in encoded_frame_plan["samples"]]
        staging_qa.mkdir(parents=True)
        (qa_runner or _default_qa_runner)(staging_video, staging_qa, frames)
        hbg_style = _load(root / "HBG_STYLE.json", "HBG style")
        canvas = hbg_style.get("canvas") if isinstance(hbg_style.get("canvas"), dict) else {}
        report = evaluate_final_video(
            staging_video,
            staging_qa,
            expected_duration=total,
            expected_width=int(canvas.get("width", 0)),
            expected_height=int(canvas.get("height", 0)),
            expected_fps=float(canvas.get("fps", 0)),
            expected_frame_labels=[label for _seconds, label in frames],
        )
        report["video"] = output.relative_to(root).as_posix()
        report["technical_status"] = "pass"
        report["status"] = "awaiting_encoded_visual_review"
        report["encoded_frame_plan_path"] = "09_qc/ENCODED_FRAME_PLAN.json"
        report["encoded_frame_plan_sha256"] = sha256_file(root / "09_qc/ENCODED_FRAME_PLAN.json")
        report["hbg_encoded_sample_count"] = len(frames)
        os.replace(staging_video, output)
        if qa_dir.exists():
            qa_dir.rmdir()
        os.replace(staging_qa, qa_dir)
        for check in report["checks"]:
            if check.get("id") == "encoded_contact_sheet":
                check["observed"] = str((qa_dir / "contact-sheet.jpg").resolve())
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(_pretty(report))
        final = {
            "schema_version": "final-render-manifest.v1",
            "release_id": manifest["release_id"],
            "render_manifest_sha256": sha256_file(prepared.render_manifest_path),
            "render_preflight_sha256": sha256_file(root / "07_render/RENDER_PREFLIGHT.json"),
            "encoded_frame_plan_sha256": sha256_file(root / "09_qc/ENCODED_FRAME_PLAN.json"),
            "video_path": output.relative_to(root).as_posix(),
            "video_sha256": sha256_file(output),
            "video_bytes": output.stat().st_size,
            "qa_report_path": report_path.relative_to(root).as_posix(),
            "qa_report_sha256": sha256_file(report_path),
            "renderer": manifest["renderer"],
            "next_stage_status": "awaiting_encoded_visual_review",
        }
        final_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        final_manifest_path.write_bytes(_pretty(final))
        return RenderStageResult(
            "created", prepared.render_manifest_path, prepared.workspace,
            output, "awaiting_encoded_visual_review",
        )
    except Exception:
        staging_video.unlink(missing_ok=True)
        if staging_qa.exists():
            shutil.rmtree(staging_qa, ignore_errors=True)
        final_manifest_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        if qa_dir.exists():
            shutil.rmtree(qa_dir, ignore_errors=True)
        raise
