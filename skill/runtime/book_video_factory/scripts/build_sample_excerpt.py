#!/usr/bin/env python3
"""Create a hash-bound 45-90 second no-rewrite representative excerpt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.sample_excerpt import SampleExcerptError, build_sample_excerpt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = build_sample_excerpt(args.project, args.input, args.out)
    except (SampleExcerptError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "path": str(result.path),
        "estimated_duration_seconds": result.duration_seconds,
        "script_approval_event_id": result.source_approval_event_id,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
