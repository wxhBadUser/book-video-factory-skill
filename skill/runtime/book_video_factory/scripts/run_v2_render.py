#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.v2_render import (
    V2RenderError,
    build_render_decision,
    render_static,
    render_delivery_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 static render status/execute CLI")
    parser.add_argument("command", nargs="?", choices=("status", "execute"), default="status")
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    if not project.is_dir():
        print(json.dumps({"status": "failed", "error": f"project path is not a directory: {project}"}, ensure_ascii=False))
        return 2
    try:
        if args.command == "execute":
            rendered = render_static(project)
            print(json.dumps({"status": "executed", "render": rendered, "delivery": render_delivery_status(project)}, ensure_ascii=False, indent=2))
            return 0
        payload = {
            "status": render_delivery_status(project),
            "render_decision": build_render_decision(project),
        }
    except V2RenderError as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
