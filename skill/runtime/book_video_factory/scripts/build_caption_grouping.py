#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.semantic_alignment.caption_grouping import (
    CaptionGroupingError,
    build_caption_grouping_from_project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or validate caption image grouping metadata")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        result = build_caption_grouping_from_project(args.project, validate_only=args.validate_only)
    except (CaptionGroupingError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    if args.validate_only:
        assert isinstance(result, dict)
        print(json.dumps({
            "status": "valid",
            "release_id": result["release_id"],
            "caption_count": result["caption_count"],
            "boundary_count": result["boundary_count"],
            "required_split_count": result["required_split_count"],
        }, ensure_ascii=False))
        return 0
    assert isinstance(result, Path)
    print(json.dumps({"status": "built", "audit_path": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
