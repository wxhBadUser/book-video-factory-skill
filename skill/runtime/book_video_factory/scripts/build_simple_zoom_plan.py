#!/usr/bin/env python3
"""Build a SIMPLE_ZOOM_PLAN.json from a hold plan (pan BLOCK, zoom caps)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.director_stage.simple_zoom import (
    SimpleZoomError,
    plan_simple_zoom,
    validate_simple_zoom_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SIMPLE_ZOOM_PLAN.json")
    parser.add_argument("--holds", type=Path, required=True, help="JSON list of holds")
    parser.add_argument("--intents", type=Path, default=None, help="JSON object hold_id -> in/out/hold")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        holds = json.loads(args.holds.read_text(encoding="utf-8"))
        if not isinstance(holds, list) or not holds:
            raise SimpleZoomError("holds must be a nonempty JSON list")
        intents = {}
        if args.intents is not None:
            intents = json.loads(args.intents.read_text(encoding="utf-8"))
            if not isinstance(intents, dict):
                raise SimpleZoomError("intents must be a JSON object")
        plan = plan_simple_zoom(holds, intents=intents)
        validate_simple_zoom_plan(plan)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (SimpleZoomError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "path": str(args.out), "motions": len(plan["motions"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
