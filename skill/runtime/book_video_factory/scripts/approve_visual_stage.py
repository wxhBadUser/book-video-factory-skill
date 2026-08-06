#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_stage.approval import VisualApprovalError, approve_visual_stage


def main() -> int:
    parser=argparse.ArgumentParser(description="Record an explicit hash-bound human decision for Phase 3 anchors and twelve LookDev images")
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--release-id",required=True)
    parser.add_argument("--reviewer",required=True)
    parser.add_argument("--decision-file",type=Path,required=True)
    parser.add_argument("--note",default="")
    args=parser.parse_args()
    try:
        result=approve_visual_stage(args.project,release_id=args.release_id,reviewer=args.reviewer,decision_path=args.decision_file,note=args.note)
    except VisualApprovalError as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":result.status,"decision":result.decision,"approval_path":str(result.approval_path),
        "event_path":str(result.event_path),"next_stage_status":result.next_stage_status},ensure_ascii=False)); return 0


if __name__=="__main__": raise SystemExit(main())
