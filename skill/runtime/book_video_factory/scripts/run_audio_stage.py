#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.audio_stage.compiler import (
    AudioStageConflict,
    AudioStageError,
    finalize_audio_stage,
    generate_audio_stage,
)
from book_video_factory.audio_stage.status import audio_stage_status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 4 provider narration, VTT, and real-audio storyboard stages")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="Generate provider audio, VTT, evidence, and preliminary timing")
    generate.add_argument("--project", type=Path, required=True)
    generate.add_argument("--input", type=Path, required=True)
    generate.add_argument("--lexicon", type=Path, required=True)
    generate.add_argument("--provider", choices=("edge-tts", "minimax"), required=True)
    finalize = sub.add_parser("finalize", help="Finalize an Agent-authored real-audio storyboard plan")
    finalize.add_argument("--project", type=Path, required=True)
    finalize.add_argument("--plan", type=Path, required=True)
    finalize.add_argument("--provider", choices=("edge-tts", "minimax"))
    status = sub.add_parser("status", help="Derive current Phase 4 status from evidence")
    status.add_argument("--project", type=Path, required=True)
    status.add_argument("--release-id", required=True)
    return parser


def _declared_provider(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AudioStageError(f"audio stage input is unreadable: {error}") from error
    provider = payload.get("provider") if isinstance(payload, dict) else None
    if provider not in {"edge-tts", "minimax"}:
        raise AudioStageError("audio stage input provider is invalid")
    return provider


def _require_provider_match(selected: str | None, input_path: Path) -> str:
    declared = _declared_provider(input_path)
    if selected is not None and selected != declared:
        raise AudioStageError(
            f"--provider {selected} disagrees with AUDIO_STAGE_INPUT.json provider {declared}"
        )
    return declared


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "generate":
            provider = _require_provider_match(args.provider, args.input)
            if provider == "edge-tts" and os.environ.get("BOOK_VIDEO_FACTORY_FORCE_MISSING_EDGE_TTS") == "1":
                import book_video_factory.audio_stage.compiler as compiler
                compiler._edge_tts_available = lambda: False  # process-local diagnostic test hook
            result = generate_audio_stage(args.project, args.input, args.lexicon)
            payload = {
                "status": result.status,
                "manifest_path": str(result.manifest_path),
                "stage_manifest_path": str(result.stage_manifest_path),
                "next_stage_status": result.next_stage_status,
            }
        elif args.command == "finalize":
            _require_provider_match(
                args.provider,
                args.project.expanduser().resolve() / "04_audio/AUDIO_STAGE_INPUT.json",
            )
            result = finalize_audio_stage(args.project, args.plan)
            payload = {
                "status": result.status,
                "manifest_path": str(result.manifest_path),
                "stage_manifest_path": str(result.stage_manifest_path),
                "next_stage_status": result.next_stage_status,
            }
        else:
            payload = {"status": audio_stage_status(args.project, args.release_id), "release_id": args.release_id}
    except (AudioStageError, AudioStageConflict, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
