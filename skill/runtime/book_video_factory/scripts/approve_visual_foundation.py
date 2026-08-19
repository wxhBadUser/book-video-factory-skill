#!/usr/bin/env python3
"""Record the human approval gate `visual_foundation` for the approved Visual Foundation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_foundation import VisualFoundationError, approve_visual_foundation


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve the Visual Foundation (Style Master / Identity / Location)")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        path = approve_visual_foundation(
            args.project, release_id=args.release_id, reviewer=args.reviewer, note=args.note
        )
    except VisualFoundationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "approved", "approval_path": str(path), "next_stage_status": "visual_foundation_approved"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
