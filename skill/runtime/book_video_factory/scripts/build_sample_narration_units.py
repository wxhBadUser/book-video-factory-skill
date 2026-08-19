#!/usr/bin/env python3
"""Derive exact MiniMax narration units from an approved sample excerpt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.sample_excerpt import SampleExcerptError, build_sample_narration_units


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--excerpt", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = build_sample_narration_units(args.project, args.excerpt, args.out)
    except (SampleExcerptError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "path": str(result.path), "units": result.unit_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
