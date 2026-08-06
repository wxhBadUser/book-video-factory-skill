#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.render_stage import (
    RenderStageError, execute_render_stage, generate_opening_preview, prepare_render_stage,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the HBG opening preview, prepare, or execute the Phase 7 render"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "prepare", "execute"):
        command = sub.add_parser(name)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "preview":
            result = generate_opening_preview(args.project, args.input)
            payload = {
                "status": result.status,
                "manifest_path": str(result.manifest_path),
                "output_path": str(result.output_path),
                "next_stage_status": result.next_stage_status,
            }
        else:
            result = (
                prepare_render_stage(args.project, args.input)
                if args.command == "prepare"
                else execute_render_stage(args.project, args.input)
            )
            payload = {
                "status": result.status,
                "render_manifest_path": str(result.render_manifest_path),
                "workspace": str(result.workspace),
                "output_path": str(result.output_path) if result.output_path else None,
                "next_stage_status": result.next_stage_status,
            }
    except (RenderStageError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
