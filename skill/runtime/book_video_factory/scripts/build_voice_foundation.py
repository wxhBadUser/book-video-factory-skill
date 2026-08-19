#!/usr/bin/env python3
"""Build the MiniMax VOICE_FOUNDATION.json (cloned_voice or system_voice)."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.audio_stage.contracts import validate_voice_foundation
from book_video_factory.audio_stage.voice_foundation import (
    VoiceFoundationError,
    build_voice_foundation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build VOICE_FOUNDATION.json")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy", choices=["cloned_voice", "system_voice"], required=True)
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--source-audio", type=Path, default=None)
    parser.add_argument("--prompt-audio", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify", action="store_true",
                        help="verify voice_id via MiniMax Get Voice before freezing (M4B A1)")
    args = parser.parse_args()
    try:
        if args.verify:
            from book_video_factory.audio_stage.providers.base import NarrationProviderError
            from book_video_factory.audio_stage.providers.minimax import MiniMaxNarrationProvider
            with MiniMaxNarrationProvider(voice_id=args.voice_id) as provider:
                # Fail closed: exact voice must exist; never silently swap.
                provider.verify_system_voice(args.voice_id)
        foundation = build_voice_foundation(
            release_id=args.release_id,
            voice_strategy=args.strategy,
            voice_id=args.voice_id,
            source_audio=args.source_audio,
            prompt_audio=args.prompt_audio,
            cloned_at=datetime.now(timezone.utc) if args.strategy == "cloned_voice" else None,
        )
        document = foundation.to_dict()
        validate_voice_foundation(document)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (VoiceFoundationError, NarrationProviderError, OSError, ValueError, json.JSONDecodeError) as error:
        status = "blocked" if isinstance(error, NarrationProviderError) else "failed"
        print(json.dumps({"status": status, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "path": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
