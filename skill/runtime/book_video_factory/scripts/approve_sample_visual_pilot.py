#!/usr/bin/env python3
"""Record an explicit human decision for a hash-bound sample visual pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.sample_visual_approval import SampleVisualApprovalError, approve_sample_visual_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision-file", type=Path, required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        result = approve_sample_visual_pilot(args.project, release_id=args.release_id, reviewer=args.reviewer, decision_path=args.decision_file, note=args.note)
    except SampleVisualApprovalError as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "approval_path": str(result.approval_path), "event_path": str(result.event_path), "next_stage_status": result.next_stage_status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
