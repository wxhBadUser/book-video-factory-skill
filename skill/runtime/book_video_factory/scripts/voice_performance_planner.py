"""CLI: generate a Voice Performance Plan from narration captions.

Reads an ordered list of caption objects and emits a ``voice-performance-plan.v1``
JSON. Optional ``--modes`` (caption_id -> Literal/Symbolic/Abstract) and
``--overrides`` (manual per-caption overrides) are merged on top of the
heuristic plan.

Usage:
    python scripts/voice_performance_planner.py \
        --captions captions.json \
        --audio-meta-sha <hex> --release-id huozhe-r1 \
        --out 04_audio/VOICE_PERFORMANCE_PLAN.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from book_video_factory.audio_stage.voice_performance import plan_voice_performance  # noqa: E402


def _load_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Voice Performance Plan")
    parser.add_argument("--captions", required=True, help="JSON file: list of {caption_id, text, ...}")
    parser.add_argument("--audio-meta-sha", required=True, help="SHA-256 of audio_meta.json")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--modes", help="optional JSON: {caption_id: mode}")
    parser.add_argument("--overrides", help="optional JSON: {caption_id: {field: value}}")
    parser.add_argument("--out", required=True, help="output plan JSON path")
    args = parser.parse_args(argv)

    captions = _load_json(args.captions)
    if not isinstance(captions, list):
        print("captions file must contain a JSON list", file=sys.stderr)
        return 2
    modes = _load_json(args.modes) if args.modes else None
    overrides = _load_json(args.overrides) if args.overrides else None

    plan = plan_voice_performance(
        captions,
        audio_meta_sha256=args.audio_meta_sha,
        release_id=args.release_id,
        modes=modes,
        manual_overrides=overrides,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path} with {len(plan['captions'])} caption plans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
