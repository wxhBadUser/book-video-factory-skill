#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from book_video_factory.semantic_alignment.scene_continuity import (  # noqa: E402
    SceneContinuityError,
    build_scene_continuity_from_project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build current Scene Continuity Spans")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        path = build_scene_continuity_from_project(args.project)
    except (SceneContinuityError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "built", "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

