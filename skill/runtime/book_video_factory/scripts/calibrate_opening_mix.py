#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import MixCalibrationError, calibrate_opening_mix


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe and record one fixed-gain 15-20 second HBG opening mix candidate")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = calibrate_opening_mix(args.project, args.input)
    except MixCalibrationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "calibration": str(result.calibration_path), "next_stage_status": result.next_stage_status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
