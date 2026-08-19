#!/usr/bin/env python3
"""Assemble provider audio into master WAV + VTT + display captions + evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.audio_stage.contracts import validate_audio_generation_evidence
from book_video_factory.audio_stage.provider_timeline import (
    ProviderTimelineError,
    build_audio_generation_evidence,
    concat_audio_master,
    restore_display_captions_from_provider,
    write_provider_vtt,
)
from book_video_factory.audio_stage.providers import NarrationChunkResult


def main() -> int:
    parser = argparse.ArgumentParser(description="Build master audio + VTT + captions from provider chunks")
    parser.add_argument("--chunks-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--model", default="speech-2.8-hd")
    parser.add_argument("--spoken-text", type=Path, default=None,
                        help="concatenated spoken text file; when given, restores "
                             "display captions and writes subtitles/<variant>.vtt")
    parser.add_argument("--display-vtt-out", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        chunks: list[NarrationChunkResult] = []
        for record in json.loads((args.chunks_dir / "chunks.json").read_text(encoding="utf-8")):
            chunk_path = args.chunks_dir / f"{record['chunk_id']}.json"
            detail = json.loads(chunk_path.read_text(encoding="utf-8"))
            chunks.append(NarrationChunkResult(
                chunk_id=record["chunk_id"],
                provider=record["provider"],
                model=record["model"],
                voice_id=record["voice_id"],
                audio_path=detail["audio_path"] if "audio_path" in detail else f"{record['chunk_id']}.wav",
                audio_sha256=record["audio_sha256"],
                duration=record["duration"],
                subtitle_timestamps=tuple(detail["subtitle_timestamps"]),
                subtitle_granularity=detail.get("subtitle_granularity", "word"),
                trace_id=record.get("trace_id"),
                request_digest=record.get("request_digest", ""),
            ))
        args.out.mkdir(parents=True, exist_ok=True)
        vtt_path, _, _ = write_provider_vtt(chunks, args.out / "provider.vtt")
        master = concat_audio_master(chunks, args.out, audio_dir=args.chunks_dir)
        vtt_sha = hashlib.sha256(vtt_path.read_bytes()).hexdigest()
        evidence = build_audio_generation_evidence(
            chunk_results=chunks,
            master=master,
            vtt_path=vtt_path,
            vtt_sha256=vtt_sha,
            release_id=args.release_id,
            variant=args.variant,
            voice_id=args.voice_id,
            model=args.model,
        )
        validate_audio_generation_evidence(evidence)
        (args.out / "AUDIO_GENERATION_EVIDENCE.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        display_vtt = None
        if args.spoken_text is not None and args.display_vtt_out is not None:
            from book_video_factory.audio_stage.pronunciation import compile_spoken_script
            from book_video_factory.audio_stage.provider_timeline import (
                restore_display_captions_from_provider,
            )
            spoken = args.spoken_text.read_text(encoding="utf-8")
            compilation = compile_spoken_script(
                spoken,
                {"schema_version": "pronunciation-lexicon.v1", "release_id": args.release_id, "entries": []},
            )
            captions = restore_display_captions_from_provider(
                chunks, compilation, min_chars=2, max_chars=30, min_duration=0.1
            )
            args.display_vtt_out.parent.mkdir(parents=True, exist_ok=True)
            lines = ["WEBVTT", ""]
            for caption in captions:
                lines.append(f"{_vtt(caption['start'])} --> {_vtt(caption['end'])}")
                lines.append(caption["text"])
                lines.append("")
            args.display_vtt_out.write_text("\n".join(lines), encoding="utf-8")
            (args.out / "DISPLAY_CAPTIONS.json").write_text(
                json.dumps(captions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            display_vtt = str(args.display_vtt_out)
    except (ProviderTimelineError, OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "master": master, "display_vtt": display_vtt, "path": str(args.out)}, ensure_ascii=False))
    return 0


def _vtt(value: float) -> str:
    millis = int(round(value * 1000))
    hours, rem = divmod(millis, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


if __name__ == "__main__":
    raise SystemExit(main())
