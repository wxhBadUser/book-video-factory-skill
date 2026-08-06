#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import RenderPreflightError, preflight_render


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pristine HBG style and long-render preflight gates")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = preflight_render(args.project, args.input)
    except RenderPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "report": str(result.report_path),
        "render_job_id": result.render_job_id,
        "next_stage_status": result.next_stage_status,
    }, ensure_ascii=False))
    return 0 if result.next_stage_status == "ready_for_hbg_render" else 2


if __name__ == "__main__":
    raise SystemExit(main())
