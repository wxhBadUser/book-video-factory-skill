#!/usr/bin/env python3
"""Compile the Visual Foundation manifests (Style Master / Character Identity / Location Anchor)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_foundation import VisualFoundationError, build_visual_foundation


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the approved Visual Foundation manifests")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--foundation-input", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.foundation_input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise VisualFoundationError("foundation input must be a JSON object")
        result = build_visual_foundation(args.project, payload)
    except (VisualFoundationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", "foundation": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
