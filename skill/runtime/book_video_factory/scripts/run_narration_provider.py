#!/usr/bin/env python3
"""Synthesize every performance segment through MiniMax (fail closed on key)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.audio_stage.contracts import (
    validate_narration_performance_plan,
    validate_voice_foundation,
)
from book_video_factory.audio_stage.providers import NarrationChunkRequest
from book_video_factory.audio_stage.providers.minimax import MiniMaxNarrationProvider
from book_video_factory.audio_stage.voice_foundation import check_clone_activation_window


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MiniMax narration provider")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--foundation", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify-voice", action="store_true", default=True,
                        help="verify voice_id via Get Voice before synthesis (M4B A1)")
    parser.add_argument("--no-verify-voice", dest="verify_voice", action="store_false")
    args = parser.parse_args()
    try:
        plan = validate_narration_performance_plan(
            json.loads(args.plan.read_text(encoding="utf-8"))
        )
        foundation = validate_voice_foundation(
            json.loads(args.foundation.read_text(encoding="utf-8"))
        )
        # A2: clone retention is advisory (unused-activation window); real
        # availability is confirmed against the provider below.
        check_clone_activation_window(foundation)
        voice_id = foundation["voice_id"]
        args.out.mkdir(parents=True, exist_ok=True)
        with MiniMaxNarrationProvider(
            voice_id=voice_id,
            allow_sentence_fallback=True,
        ) as provider:
            if args.verify_voice:
                # A1: fail closed unless the exact system voice exists on this
                # account; never silently select another voice.
                provider.verify_system_voice(voice_id)
            results = []
            for segment in plan["segments"]:
                request = NarrationChunkRequest(
                    chunk_id=segment["segment_id"],
                    text=segment["spoken_text"],
                    provider=provider.provider_id,
                    model=provider._model,
                    voice_id=voice_id,
                    speed=segment["speed"],
                    volume=segment["volume"],
                    pitch=segment["pitch"],
                    pause_before_ms=segment["pause_before_ms"],
                    pause_after_ms=segment["pause_after_ms"],
                    sound_tags=tuple(segment["sound_tags"]),
                    emotion=segment.get("emotion"),
                    intensity=segment["intensity"],
                )
                results.append(provider.synthesize(request, evidence_dir=args.out))
        summary = [
            {
                "chunk_id": r.chunk_id,
                "provider": r.provider,
                "model": r.model,
                "voice_id": r.voice_id,
                "audio_sha256": r.audio_sha256,
                "duration": r.duration,
                "trace_id": r.trace_id,
                "request_digest": r.request_digest,
            }
            for r in results
        ]
        (args.out / "chunks.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as error:
        # Fail closed: report the exact blocker (e.g. missing MINIMAX_API_KEY)
        # and never fall back to Edge-TTS.
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "chunks": len(results), "path": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
