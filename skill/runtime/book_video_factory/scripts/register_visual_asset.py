#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    register_visual_asset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Register one real host-ImageGen PNG against a current Phase 3 visual task. "
            "This command never generates images or invents host call identifiers."
        )
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--prompt-sha256", required=True)
    parser.add_argument("--tool-call-id", required=True)
    parser.add_argument(
        "--style-reference-id", action="append", default=[],
        help="Repeat in the exact task order for every style reference used by host ImageGen.",
    )
    parser.add_argument(
        "--identity-reference-task-id", action="append", default=[],
        help="Repeat in the exact task order for every registered identity anchor used by host ImageGen.",
    )
    args = parser.parse_args()
    try:
        result = register_visual_asset(
            args.project,
            task_id=args.task_id,
            source=args.source,
            prompt_sha256=args.prompt_sha256,
            tool_call_id=args.tool_call_id,
            style_reference_ids=args.style_reference_id,
            identity_reference_task_ids=args.identity_reference_task_id,
        )
    except VisualAssetRegistrationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "task_id": result.asset["task_id"],
        "asset_path": result.asset["path"],
        "asset_sha256": result.asset["sha256"],
        "machine_diagnostic_status": result.asset["machine_diagnostic"]["status"],
        "human_review_status": result.asset["human_review_status"],
        "manifest_path": str(result.manifest_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
