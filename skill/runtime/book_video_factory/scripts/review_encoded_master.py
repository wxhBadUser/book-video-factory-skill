#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import EncodedVisualQaError, review_encoded_master


def main() -> int:
    parser = argparse.ArgumentParser(description="Record human semantic/reality/identity review of HBG-extracted encoded frames")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = review_encoded_master(args.project, args.decision)
    except EncodedVisualQaError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False)); return 2
    print(json.dumps({"status": result.status, "report": str(result.report_path), "contact_sheet": str(result.contact_sheet_path), "next_stage_status": result.next_stage_status}, ensure_ascii=False))
    return 0 if result.next_stage_status == "awaiting_final_master_approval" else 2


if __name__ == "__main__":
    raise SystemExit(main())
