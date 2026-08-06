#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_stage.compiler import (
    VisualStageCompileError,
    compile_visual_stage,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile an approved HBG bridge into a per-book visual profile and host-ImageGen task queue"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--visual-input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compile_visual_stage(args.project, args.visual_input)
    except VisualStageCompileError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "visual_stage_digest": result.visual_stage_digest,
        "manifest_path": str(result.manifest_path),
        "stage_manifest_path": str(result.stage_manifest_path),
        "next_stage_status": "waiting_for_host_imagegen",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
