"""P0-1 CLI: CAPTION_MEANING_BLOCKS.json + CAPTION_BINDINGS.json from evidence.

Reads a narration word-cue source:
  --evidence 04_audio/provider_evidence/AUDIO_GENERATION_EVIDENCE.json  (runtime layout)
  --evidence 04_audio/evidence/<chunk>.json                            (sample layout, glob)
Offsets chunk-relative timestamps to the master timeline (audio-stage offset
logic), splits meaning blocks, writes both chain artifacts. Uses
book_video_factory.audio_stage.provider_timeline.offset_chunk_timestamps for
the master-time offset so the grid is byte-identical to the real audio master.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from book_video_factory.audio_stage.meaning_blocks import (  # noqa: E402
    MeaningBlock, blocks_to_caption_bindings, build_meaning_blocks, load_cues_from_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CaptionMeaningBlocks from narration evidence")
    parser.add_argument("--evidence", required=True, type=Path, help="evidence JSON with subtitle_timestamps")
    parser.add_argument("--out", required=True, type=Path, help="directory to write chain artifacts (04_audio/)")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--body-start", type=float, default=0.0)
    args = parser.parse_args()

    cues = load_cues_from_evidence(args.evidence, body_start=args.body_start)
    blocks = build_meaning_blocks(cues)
    if args.evidence.is_dir():
        hasher = __import__("hashlib").sha256()
        for ev_file in sorted(args.evidence.glob("*.json")):
            hasher.update(ev_file.read_bytes())
        evidence_sha = hasher.hexdigest()
    else:
        evidence_sha = __import__("hashlib").sha256(args.evidence.read_bytes()).hexdigest()
    stats = {
        "median_duration": round(sorted(b.duration for b in blocks)[len(blocks) // 2], 3),
        "p95_duration": round(sorted(b.duration for b in blocks)[max(0, int(len(blocks) * 0.95) - 1)], 3),
        "max_duration": round(max(b.duration for b in blocks), 3),
        "max_chars_any_block": max(len("".join(c for c in b.text if not c.isspace())) for b in blocks),
    }
    document = {
        "schema_version": "caption-meaning-block.v1",
        "release_id": args.release_id,
        "block_count": len(blocks),
        "source_evidence_sha256": evidence_sha,
        "stats": stats,
        "blocks": [b.to_dict() for b in blocks],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "CAPTION_MEANING_BLOCKS.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    bindings = blocks_to_caption_bindings(
        blocks=[MeaningBlock(**b) for b in document["blocks"]],
        release_id=args.release_id,
        body_start=args.body_start,
    )
    (args.out / "CAPTION_BINDINGS.json").write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"blocks": len(blocks), "max_duration": stats["max_duration"], "stats": stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
