#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.hbg_bridge.compiler import (
    HbgBridgeCompileError,
    compile_hbg_bridge,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile one approved Phase 1 content package into native HBG artifacts"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--bridge-input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compile_hbg_bridge(args.project, args.bridge_input)
    except HbgBridgeCompileError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "bridge_digest": result.bridge_digest,
        "manifest_path": str(result.manifest_path),
        "stage_manifest_path": str(result.stage_manifest_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
