#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.v2_render import (
    V2RenderError,
    build_render_decision,
    render_delivery_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report the V2 static render state and the deterministic render input "
        "(Stage 6, fail-closed). The Python Runtime never renders the video itself."
    )
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    if not project.is_dir():
        print(json.dumps({"status": "failed", "error": f"project path is not a directory: {project}"}, ensure_ascii=False))
        return 2
    try:
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
