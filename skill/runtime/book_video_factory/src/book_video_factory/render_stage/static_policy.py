from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class StaticRenderPolicyError(RuntimeError):
    """A workspace cannot be rendered as a static-still production."""


def _object(workspace: Path, relative: str, label: str) -> dict[str, Any]:
    path = workspace / relative
    if path.is_symlink() or not path.is_file():
        raise StaticRenderPolicyError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StaticRenderPolicyError(f"{label} is unreadable: {relative}") from error
    if not isinstance(value, dict):
        raise StaticRenderPolicyError(f"{label} must be an object: {relative}")
    return value


def _workspace_file(workspace: Path, relative: Any, label: str) -> None:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise StaticRenderPolicyError(f"{label} path is invalid")
    candidate = workspace / relative
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size == 0:
        raise StaticRenderPolicyError(f"{label} is missing: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(workspace.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise StaticRenderPolicyError(f"{label} escapes the static workspace: {relative}") from error


def validate_static_workspace(workspace: Path) -> None:
    """Validate the local inputs for the no-camera-motion HBG adapter.

    The locked vendor's animated style validator deliberately requires motion,
    so it cannot be the gate for a static still-image render.  This replacement
    validates only the inputs the adapter actually consumes; encoded HBG QA
    remains required after rendering.
    """

    root = workspace.expanduser().resolve()
    style = _object(root, "HBG_STYLE.json", "static HBG style")
    canvas = style.get("canvas")
    if not isinstance(canvas, Mapping) or any(
        isinstance(canvas.get(field), bool) or not isinstance(canvas.get(field), (int, float))
        or float(canvas[field]) <= 0
        for field in ("width", "height", "fps")
    ):
        raise StaticRenderPolicyError("static HBG canvas is invalid")
    _object(root, "PROJECT_SPEC.json", "static project spec")
    audio = _object(root, "audio_meta.json", "static audio metadata")
    body = audio.get("body")
    if not isinstance(body, Mapping):
        raise StaticRenderPolicyError("static audio metadata has no body")
    _workspace_file(root, body.get("path"), "body audio")
    storyboard_path = root / "STORYBOARD.json"
    if storyboard_path.is_symlink() or not storyboard_path.is_file():
        raise StaticRenderPolicyError("static storyboard is missing or symlinked")
    try:
        storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StaticRenderPolicyError("static storyboard is unreadable") from error
    if not isinstance(storyboard, list) or not storyboard:
        raise StaticRenderPolicyError("static storyboard must be a nonempty list")
    for index, scene in enumerate(storyboard, start=1):
        if not isinstance(scene, Mapping):
            raise StaticRenderPolicyError(f"static storyboard scene {index} is invalid")
        _workspace_file(root, scene.get("asset"), "storyboard asset")

    # P0-9: fail closed without current human scene approval (mirrors
    # prepare_render_stage._scene_approval semantics for the static gate).
    approval_path = root / "06_visual_production" / "SCENE_ASSET_APPROVAL.json"
    if approval_path.is_symlink() or not approval_path.is_file():
        raise StaticRenderPolicyError("scene asset approval is missing; refusing to render")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StaticRenderPolicyError("scene asset approval is unreadable") from error
    if approval.get("schema_version") != "scene-asset-approval.v1" or not approval.get("human_approved") \
            or approval.get("next_stage_status") != "ready_for_render":
        raise StaticRenderPolicyError("scene assets do not have current human approval")

    # P0-9: the narration body must be real speech, not silence/placeholder.
    from book_video_factory.render_stage.audio_content import PlaceholderAudioError, assert_body_is_real_speech
    body_path = body.get("path")
    try:
        assert_body_is_real_speech(root, Path(body_path))
    except PlaceholderAudioError as error:
        raise StaticRenderPolicyError(str(error)) from error
