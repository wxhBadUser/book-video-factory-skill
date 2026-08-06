#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_stage.review import VisualReviewError, build_visual_review


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the twelve-image LookDev contact sheet with the pristine HBG script and record pending human review evidence"
    )
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_visual_review(args.project)
    except VisualReviewError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "review_digest": result.review_digest,
        "contact_sheet_path": str(result.contact_sheet_path),
        "report_path": str(result.report_path),
        "stage_manifest_path": str(result.stage_manifest_path),
        "human_review_status": "pending",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
