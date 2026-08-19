#!/usr/bin/env python3
"""Register one real static Host ImageGen image as a sample-only scene candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.sample_scene_asset import SampleSceneAssetError, register_sample_scene_asset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = register_sample_scene_asset(args.project, args.input)
    except (SampleSceneAssetError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.status, "path": str(result.path), "asset_path": str(result.asset_path), "scene_id": result.scene_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
