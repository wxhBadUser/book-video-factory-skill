"""V2 static render + encoded QA + auto delivery (Stage 6, fail-closed).

The Python Runtime owns rendering state, manifest computation, and delivery
finalization. It never asks a human to approve scenes, BGM, opening mix, or the
final master, and it never fabricates a rendered video or QA result. It only
verifies real video/QA evidence and advances to ``delivered`` when every bound
hash (edit timeline, asset catalog, audio timeline, video, QA report) is intact.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from book_video_factory.director_stage.contracts import (
    DirectorStageV2Error,
    EDIT_TIMELINE_REL,
    resolve_edit_timeline,
)
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.visual_covenant import (
    VisualCovenantError,
    load_asset_catalog,
    verify_asset_catalog,
)


class V2RenderError(RuntimeError):
    """V2 render/delivery evidence is missing, tampered, or out of bounds."""


DELIVERY_MANIFEST_REL = "08_render_合成/final/DELIVERY_MANIFEST.v2.json"
ENCODED_QA_REL = "08_render_合成/final/ENCODED_QA.v2.json"
DEFAULT_VIDEO_REL = "08_render_合成/final/v2_master.mp4"
DELIVERY_SCHEMA_VERSION = "render-delivery.v2"
DECISION_SCHEMA_VERSION = "render-decision.v2"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise V2RenderError(f"{name} is unavailable")
    return path


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _sha_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    bytes_value = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    staging.write_bytes(bytes_value)
    os.replace(staging, path)


def _load_object(root: Path, relative: str, label: str) -> dict[str, Any]:
    try:
        path = safe_project_output(root, Path(relative))
    except (ValueError, OSError) as error:
        raise V2RenderError(f"{label} path is out of bounds: {error}") from error
    if path.is_symlink() or not path.is_file():
        raise V2RenderError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V2RenderError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise V2RenderError(f"{label} must be a JSON object")
    return value


def _media(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise V2RenderError(f"{label} path is invalid")
    try:
        path = safe_project_output(root, Path(relative))
    except (ValueError, OSError) as error:
        raise V2RenderError(f"{label} path is out of bounds: {error}") from error
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise V2RenderError(f"{label} is missing, empty, or symlinked: {relative}")
    return path


def _timeline(root: Path) -> tuple[dict[str, Any], str]:
    resolve_edit_timeline(root)
    timeline = _load_object(root, EDIT_TIMELINE_REL, "edit timeline")
    if timeline.get("schema_version") != "edit-timeline.v2":
        raise V2RenderError("edit timeline schema_version is invalid")
    path = safe_project_output(root, Path(EDIT_TIMELINE_REL))
    return timeline, sha256_file(path)


def _catalog_sha(root: Path) -> str:
    try:
        status = verify_asset_catalog(root)
    except VisualCovenantError as error:
        raise V2RenderError(f"asset catalog is not verified: {error}") from error
    if status.get("status") != "asset_catalog_verified":
        raise V2RenderError(
            f"asset catalog is not verified: {status.get('status')}"
        )
    return str(status.get("catalog_sha256", ""))


def build_render_decision(project: Path) -> dict[str, Any]:
    """Deterministically project the resolved edit timeline + audio into render input."""
    root = project.expanduser().resolve()
    timeline, timeline_sha = _timeline(root)
    if not _sha_hex(timeline_sha):
        raise V2RenderError("edit timeline sha256 is invalid")
    bound_catalog = str(timeline.get("asset_catalog_sha256", ""))
    bound_audio = str(timeline.get("audio_timeline_sha256", ""))
    if not _sha_hex(bound_catalog) or not _sha_hex(bound_audio):
        raise V2RenderError("edit timeline binds an invalid catalog/audio sha256")
    actual_catalog = _catalog_sha(root)
    if actual_catalog != bound_catalog:
        raise V2RenderError("edit timeline asset_catalog_sha256 is stale")

    audio = _load_object(root, "04_audio/AUDIO_TIMELINE.v2.json", "audio timeline")
    if str(audio.get("schema_version", "")) != "audio-timeline.v2":
        raise V2RenderError("audio timeline schema_version is invalid")
    audio_path = safe_project_output(root, Path("04_audio/AUDIO_TIMELINE.v2.json"))
    actual_audio = sha256_file(audio_path)
    if actual_audio != bound_audio:
        raise V2RenderError("edit timeline audio_timeline_sha256 is stale")

    narration_master = audio.get("narration_master_path")
    narration_sha = audio.get("narration_master_sha256")
    if not isinstance(narration_master, str) or not isinstance(narration_sha, str):
        raise V2RenderError("audio timeline narration master evidence is missing")
    narration_path = _media(root, narration_master, "narration master")
    if sha256_file(narration_path) != narration_sha:
        raise V2RenderError("narration master sha256 is stale")

    bgm = audio.get("bgm")
    if not isinstance(bgm, dict) or bgm.get("bgm_mode") not in {"library", "none"}:
        raise V2RenderError("audio timeline bgm is invalid")
    if bgm.get("bgm_mode") == "library":
        source_path = bgm.get("source_path")
        source_sha = bgm.get("source_sha256")
        if not isinstance(source_path, str) or not isinstance(source_sha, str):
            raise V2RenderError("audio timeline BGM rights evidence is missing")
        bgm_source = _media(root, source_path, "BGM track")
        if sha256_file(bgm_source) != source_sha:
            raise V2RenderError("BGM source sha256 is stale (rights evidence)")

    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "release_id": timeline["release_id"],
        "project_id": timeline["project_id"],
        "edit_timeline_sha256": timeline_sha,
        "asset_catalog_sha256": actual_catalog,
        "audio_timeline_sha256": actual_audio,
        "narration_master": {
            "path": narration_master,
            "sha256": narration_sha,
        },
        "bgm": bgm,
        "events": timeline["events"],
    }


def _render_decision_sha(decision: dict[str, Any]) -> str:
    return _sha_bytes(_canonical(decision))


def _extract_decision_shas(root: Path) -> tuple[str, str, str]:
    decision = build_render_decision(root)
    return (
        str(decision["edit_timeline_sha256"]),
        str(decision["asset_catalog_sha256"]),
        str(decision["audio_timeline_sha256"]),
    )


def verify_delivery_manifest(project: Path) -> dict[str, Any]:
    """Fail-closed verification of the V2 delivery evidence (raises on any break)."""
    root = project.expanduser().resolve()
    manifest = _load_object(root, DELIVERY_MANIFEST_REL, "delivery manifest")
    if manifest.get("schema_version") != DELIVERY_SCHEMA_VERSION:
        raise V2RenderError("delivery manifest schema_version is invalid")
    if manifest.get("human_approved") is not False:
        raise V2RenderError("delivery manifest must not require human approval")
    if manifest.get("auto_delivered") is not True:
        raise V2RenderError("delivery manifest is not marked auto-delivered")

    edit_sha, catalog_sha, audio_sha = _extract_decision_shas(root)
    expected_edit = str(manifest.get("edit_timeline_sha256", ""))
    expected_catalog = str(manifest.get("asset_catalog_sha256", ""))
    expected_audio = str(manifest.get("audio_timeline_sha256", ""))
    if expected_edit != edit_sha:
        raise V2RenderError("delivery manifest edit_timeline_sha256 does not match the current timeline")
    if expected_catalog != catalog_sha:
        raise V2RenderError("delivery manifest asset_catalog_sha256 does not match the current catalog")
    if expected_audio != audio_sha:
        raise V2RenderError("delivery manifest audio_timeline_sha256 does not match the current audio timeline")

    video_relative = str(manifest.get("video_path", ""))
    video_sha = str(manifest.get("video_sha256", ""))
    video = _media(root, video_relative, "final video")
    if sha256_file(video) != video_sha:
        raise V2RenderError("final video sha256 is stale")

    qa_relative = str(manifest.get("qa_report_path", ""))
    qa_sha = str(manifest.get("qa_report_sha256", ""))
    qa_path = _media(root, qa_relative, "encoded QA report")
    if sha256_file(qa_path) != qa_sha:
        raise V2RenderError("encoded QA report sha256 is stale")
    qa = _load_object(root, qa_relative, "encoded QA report")
    if qa.get("status") != "pass":
        raise V2RenderError("encoded QA report is not passing")
    if str(qa.get("video_sha256", "")) != video_sha:
        raise V2RenderError("encoded QA report does not bind the final video sha256")
    return {
        "status": "delivery_verified",
        "release_id": manifest["release_id"],
        "video_path": video_relative,
        "video_sha256": video_sha,
        "qa_report_path": qa_relative,
    }


def _encoded_qa_payload(
    root: Path,
    *,
    video_relative: str,
    video_sha: str,
    qa_payload: dict[str, Any],
) -> dict[str, Any]:
    if qa_payload.get("status") != "pass":
        raise V2RenderError("encoded QA must report status=pass before delivery")
    video = _media(root, video_relative, "final video")
    if sha256_file(video) != video_sha:
        raise V2RenderError("final video sha256 is stale before delivery")
    payload = dict(qa_payload)
    payload["schema_version"] = "encoded-visual-qa.v2"
    payload["video_path"] = video_relative
    payload["video_sha256"] = video_sha
    payload["status"] = "pass"
    return payload


def _probe_encoded_qa(root: Path, video_relative: str) -> dict[str, Any]:
    video = _media(root, video_relative, "final video")
    try:
        completed = subprocess.run(
            [_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        probe = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V2RenderError(f"ffprobe failed for final video: {error}") from error
    streams = probe.get("streams") if isinstance(probe, dict) else None
    if not isinstance(streams, list):
        raise V2RenderError("ffprobe returned no streams")
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not isinstance(video_stream, dict) or not isinstance(audio_stream, dict):
        raise V2RenderError("encoded master must contain video and audio streams")
    if video_stream.get("codec_name") != "h264" or audio_stream.get("codec_name") != "aac":
        raise V2RenderError("encoded master codecs must be H.264 video and AAC audio")
    if video_stream.get("width") != 1280 or video_stream.get("height") != 720:
        raise V2RenderError("encoded master resolution must be 1280x720")
    fps = str(video_stream.get("r_frame_rate", ""))
    if fps not in {"30/1", "30000/1001"}:
        raise V2RenderError("encoded master frame rate must be 30fps")
    format_payload = probe.get("format") if isinstance(probe, dict) else None
    duration = float(format_payload.get("duration", 0.0)) if isinstance(format_payload, dict) else 0.0
    if duration <= 0.0:
        raise V2RenderError("encoded master duration is empty")
    timeline = _load_object(root, "04_audio/AUDIO_TIMELINE.v2.json", "audio timeline")
    expected_duration = float(timeline.get("narration_duration_seconds", 0.0))
    if abs(duration - expected_duration) > 0.5:
        raise V2RenderError("encoded master duration differs from narration duration")
    return {
        "schema_version": "encoded-visual-qa.v2",
        "status": "pass",
        "video_path": video_relative,
        "video_sha256": sha256_file(video),
        "probe": {
            "format_name": format_payload.get("format_name") if isinstance(format_payload, dict) else None,
            "duration": duration,
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name"),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "frame_rate": fps,
            "video_frames": video_stream.get("nb_frames"),
        },
        "checks_passed": ["stream_presence", "resolution", "frame_rate", "duration", "codec_container"],
    }


def render_static(project: Path) -> dict[str, Any]:
    """Render the resolved V2 timeline through the local FFmpeg adapter."""
    root = project.expanduser().resolve()
    decision = build_render_decision(root)
    video_relative = DEFAULT_VIDEO_REL
    video = safe_project_output(root, Path(video_relative))
    video.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v2-render-") as temp:
        temp_root = Path(temp)
        catalog_assets = load_asset_catalog(root).get("assets", [])
        segments: list[Path] = []
        for index, event in enumerate(decision["events"], start=1):
            try:
                asset_relative = next(
                    item["path"] for item in catalog_assets
                    if item.get("asset_id") == event["asset_id"]
                )
            except StopIteration as error:
                raise V2RenderError(f"scene asset is missing from catalog: {event['asset_id']}") from error
            image = _media(root, str(asset_relative), "scene asset")
            segment = temp_root / f"segment-{index:04d}.mp4"
            duration = float(event["end"]) - float(event["start"])
            try:
                subprocess.run(
                    [
                        _tool("ffmpeg"), "-y", "-v", "error", "-loop", "1", "-i", str(image),
                        "-t", f"{duration:.6f}", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(segment),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
            except (OSError, subprocess.CalledProcessError) as error:
                detail = getattr(error, "stderr", "")
                raise V2RenderError(f"static image segment render failed: {detail or error}") from error
            segments.append(segment)
        if not segments:
            raise V2RenderError("edit timeline contains no render events")
        concat = temp_root / "segments.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in segments) + "\n", encoding="utf-8")
        video_only = temp_root / "video-only.mp4"
        command = [
            _tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-c", "copy", str(video_only),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
            command = [
                _tool("ffmpeg"), "-y", "-v", "error", "-i", str(video_only),
                "-i", str(_media(root, str(decision["narration_master"]["path"]), "narration master")),
                "-map", "0:v:0", "-map", "1:a:0",
                "-vf", f"subtitles=filename='{str(_media(root, str(_load_object(root, '04_audio/AUDIO_TIMELINE.v2.json', 'audio timeline').get('provider_vtt_path')), 'provider VTT')).replace(chr(92), '/').replace(':', '\\:')}'",
                "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", "-t", f"{float(decision['events'][-1]['end']):.6f}", str(video),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", "")
            raise V2RenderError(f"static FFmpeg render failed: {detail or error}") from error
    qa = _probe_encoded_qa(root, video_relative)
    return {"status": "rendered", "video_path": video_relative, "video_sha256": qa["video_sha256"], "qa": qa}


def finalize_delivery(
    project: Path,
    *,
    video_relative: str,
    video_sha: str,
) -> dict[str, Any]:
    """Write probe-derived encoded QA + delivery manifest."""
    root = project.expanduser().resolve()
    edit_sha, catalog_sha, audio_sha = _extract_decision_shas(root)
    decision_sha = _render_decision_sha(build_render_decision(root))
    video = _media(root, video_relative, "final video")
    if sha256_file(video) != video_sha:
        raise V2RenderError("final video sha256 is stale before delivery")
    encoded = _probe_encoded_qa(root, video_relative)
    qa_path = safe_project_output(root, Path(ENCODED_QA_REL))
    _atomic_json(qa_path, encoded)
    qa_sha = sha256_file(qa_path)

    manifest = {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "release_id": _load_object(root, EDIT_TIMELINE_REL, "edit timeline")["release_id"],
        "project_id": _load_object(root, EDIT_TIMELINE_REL, "edit timeline")["project_id"],
        "edit_timeline_sha256": edit_sha,
        "asset_catalog_sha256": catalog_sha,
        "audio_timeline_sha256": audio_sha,
        "render_input_sha256": decision_sha,
        "video_path": video_relative,
        "video_sha256": video_sha,
        "qa_report_path": ENCODED_QA_REL,
        "qa_report_sha256": qa_sha,
        "auto_delivered": True,
        "human_approved": False,
    }
    manifest_path = safe_project_output(root, Path(DELIVERY_MANIFEST_REL))
    _atomic_json(manifest_path, manifest)
    return {
        "status": "delivered",
        "release_id": manifest["release_id"],
        "video_path": video_relative,
        "qa_report_path": ENCODED_QA_REL,
    }


def _status_blocked(project: Path, *, status: str, next_action: str, stage: str = "render") -> dict[str, Any]:
    root = project.expanduser().resolve()
    try:
        release_id = _load_object(root, EDIT_TIMELINE_REL, "edit timeline").get("release_id", "")
    except V2RenderError:
        release_id = ""
    return {
        "release_id": release_id,
        "stage": stage,
        "status": status,
        "human_review_required": False,
        "next_action": next_action,
        "command": None,
    }


def render_delivery_status(project: Path) -> dict[str, Any]:
    """Advance and report the V2 static render / encoded QA / auto delivery state."""
    root = project.expanduser().resolve()
    try:
        timeline, _ = _timeline(root)
    except (V2RenderError, DirectorStageV2Error) as error:
        return _status_blocked(
            root,
            status="blocked_by_render_integrity",
            next_action=(
                "resolve a contiguous EDIT_TIMELINE with matching asset/catalog/audio "
                f"hashes before rendering: {error}"
            ),
        )

    try:
        verified = verify_delivery_manifest(root)
        return {
            "release_id": verified["release_id"],
            "stage": "delivery",
            "status": "delivered",
            "human_review_required": False,
            "next_action": "auto-delivered; publish or archive the verified master",
            "command": None,
            "delivery": {
                "video_path": verified["video_path"],
                "video_sha256": verified["video_sha256"],
            },
        }
    except V2RenderError:
        pass

    video_relative = DEFAULT_VIDEO_REL
    video_path = safe_project_output(root, Path(video_relative))
    video_present = (
        not video_path.is_symlink()
        and video_path.is_file()
        and video_path.stat().st_size >= 1
    )
    qa_path = safe_project_output(root, Path(ENCODED_QA_REL))
    qa_payload: dict[str, Any] | None = None
    if not qa_path.is_symlink() and qa_path.is_file():
        try:
            qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            qa_payload = None

    if video_present:
        video_sha = sha256_file(video_path)
        try:
            return finalize_delivery(root, video_relative=video_relative, video_sha=video_sha)
        except V2RenderError:
            return _status_blocked(root, status="blocked_by_encoded_qa", next_action="repair the rendered master and regenerate probe-derived encoded QA before delivery", stage="encoded_qa")

    return {
        "release_id": str(timeline.get("release_id", "")),
        "stage": "render",
        "status": "awaiting_static_render",
        "human_review_required": False,
        "next_action": "render the resolved EDIT_TIMELINE, then run machine encoded QA",
        "command": (
            f"python book_video_factory/scripts/run_v2_render.py "
            f"--project '{root}'"
        ),
    }
