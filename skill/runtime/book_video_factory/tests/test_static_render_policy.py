from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from book_video_factory.render_stage.static_policy import StaticRenderPolicyError, validate_static_workspace


class StaticRenderPolicyTests(unittest.TestCase):
    def test_rejects_storyboard_asset_missing_from_static_workspace(self) -> None:
        """A static render cannot start when a declared still is absent."""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "HBG_STYLE.json").write_text(json.dumps({
                "canvas": {"width": 1920, "height": 1080, "fps": 30},
            }), encoding="utf-8")
            (workspace / "PROJECT_SPEC.json").write_text("{}", encoding="utf-8")
            (workspace / "STORYBOARD.json").write_text(json.dumps([
                {"asset": "assets/generated/missing.png"},
            ]), encoding="utf-8")
            body = workspace / "assets/audio/body.m4a"
            body.parent.mkdir(parents=True)
            body.write_bytes(b"audio")
            (workspace / "audio_meta.json").write_text(json.dumps({
                "body": {"path": "assets/audio/body.m4a"},
            }), encoding="utf-8")

            with self.assertRaisesRegex(StaticRenderPolicyError, "storyboard asset is missing"):
                validate_static_workspace(workspace)


def _write_silence_wav(path, seconds=3.0):
    import wave
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * int(16000 * seconds))


def _minimal_workspace(tmp_path, *, with_approval=True, silent_body=False):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "HBG_STYLE.json").write_text(json.dumps(
        {"canvas": {"width": 1920, "height": 1080, "fps": 30}}), encoding="utf-8")
    (ws / "PROJECT_SPEC.json").write_text(json.dumps({"projectType": "book", "title": "t"}), encoding="utf-8")
    (ws / "STORYBOARD.json").write_text(json.dumps(
        [{"id": "VB_001", "start": 0.0, "end": 4.0, "motion": "hold", "asset": "assets/scenes/VB_001.png", "chapter": 1}]), encoding="utf-8")
    img = ws / "assets" / "scenes"
    img.mkdir(parents=True)
    (img / "VB_001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    body = ws / "assets" / "audio" / "narration.wav"
    body.parent.mkdir(parents=True)
    _write_silence_wav(body)
    (ws / "audio_meta.json").write_text(json.dumps(
        {"opening": {"bodyStart": 1.5}, "body": {"path": "assets/audio/narration.wav", "duration": 3.0}}), encoding="utf-8")
    if with_approval:
        (ws / "06_visual_production").mkdir(parents=True)
        (ws / "06_visual_production" / "SCENE_ASSET_APPROVAL.json").write_text(json.dumps(
            {"schema_version": "scene-asset-approval.v1", "human_approved": True,
             "next_stage_status": "ready_for_render", "release_id": "r",
             "scene_asset_manifest_sha256": "0" * 64, "asset_hashes": {"VB_001": "0" * 64},
             "approval_event_path": "", "approval_event_sha256": ""}), encoding="utf-8")
    return ws


def test_missing_approval_blocks_static_workspace(tmp_path):
    ws = _minimal_workspace(tmp_path, with_approval=False)
    with pytest.raises(StaticRenderPolicyError, match="approval"):
        validate_static_workspace(ws)


def test_silent_narration_body_blocks_static_workspace(tmp_path):
    ws = _minimal_workspace(tmp_path, with_approval=True)
    with pytest.raises(StaticRenderPolicyError, match="silence|placeholder|narration"):
        validate_static_workspace(ws)
