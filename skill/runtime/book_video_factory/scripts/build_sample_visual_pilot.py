#!/usr/bin/env python3
"""Build a hash-bound static visual package for one approved-script sample."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.sample_visual_pilot import SampleVisualPilotError, build_sample_visual_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_sample_visual_pilot(args.project, args.input)
    except (SampleVisualPilotError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "path": str(result.path), "sample_id": result.sample_id, "scene_span_count": result.scene_span_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
