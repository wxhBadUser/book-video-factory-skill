#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import MixCalibrationError, approve_opening_mix


def main() -> int:
    parser = argparse.ArgumentParser(description="Record explicit human approval of an exact HBG opening preview and fixed BGM gain")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()
    try:
        result = approve_opening_mix(args.project, args.calibration, reviewer=args.reviewer, note=args.note)
    except MixCalibrationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "approval": str(result.approval_path), "next_stage_status": result.next_stage_status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
