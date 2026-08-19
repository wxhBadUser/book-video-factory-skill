#!/usr/bin/env python3
"""Build a NARRATION_PERFORMANCE_PLAN.json for one A/B/C variant."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.audio_stage.contracts import validate_narration_performance_plan
from book_video_factory.audio_stage.performance import (
    NarrationPerformanceError,
    NarrationSourceUnit,
    build_narration_performance_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build NARRATION_PERFORMANCE_PLAN.json")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--variant", choices=["A", "B", "C"], required=True)
    parser.add_argument("--units", type=Path, required=True, help="JSON list of source units")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw_units = json.loads(args.units.read_text(encoding="utf-8"))
        if not isinstance(raw_units, list) or not raw_units:
            raise NarrationPerformanceError("units must be a nonempty JSON list")
        units = [
            NarrationSourceUnit(
                unit_id=str(item["unit_id"]),
                text=str(item["text"]),
                spoken_text=str(item.get("spoken_text", item["text"])),
                chapter_id=str(item.get("chapter_id", "ch1")),
                narrative_function=str(item.get("narrative_function", "plot")),
                emotion_hint=item.get("emotion_hint"),
                is_dialogue_start=bool(item.get("is_dialogue_start", False)),
                is_dialogue_end=bool(item.get("is_dialogue_end", False)),
                is_impact=bool(item.get("is_impact", False)),
                is_reflection=bool(item.get("is_reflection", False)),
                is_paragraph_start=bool(item.get("is_paragraph_start", False)),
            )
            for item in raw_units
        ]
        plan = build_narration_performance_plan(units, release_id=args.release_id, variant=args.variant)
        validate_narration_performance_plan(plan)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (NarrationPerformanceError, KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "path": str(args.out), "segments": len(plan["segments"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
