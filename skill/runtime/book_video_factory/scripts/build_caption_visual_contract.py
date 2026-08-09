#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError,
    build_caption_visual_contract_from_project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or validate Caption Visual Contract v2 metadata")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        result = build_caption_visual_contract_from_project(
            args.project,
            release_id=args.release_id,
            validate_only=args.validate_only,
        )
    except (CaptionContractError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    if args.validate_only:
        assert isinstance(result, dict)
        print(json.dumps(
            {"status": "valid", "release_id": result["release_id"], "caption_count": result["caption_count"]},
            ensure_ascii=False,
        ))
        return 0
    assert isinstance(result, Path)
    print(json.dumps({"status": "built", "contract_path": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
