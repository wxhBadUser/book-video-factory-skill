#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import EncodedVisualQaError, build_encoded_frame_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic semantic timestamp plan for HBG encoded verification")
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_encoded_frame_plan(args.project)
    except EncodedVisualQaError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False)); return 2
    print(json.dumps({"status": result.status, "plan": str(result.plan_path), "sample_count": result.sample_count}, ensure_ascii=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
