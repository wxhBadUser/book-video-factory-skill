#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.delivery_stage import FinalMasterApprovalError, approve_final_master


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve the encoded final master after human review.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()
    try:
        result = approve_final_master(args.project, reviewer=args.reviewer, note=args.note)
    except FinalMasterApprovalError as error:
        parser.error(str(error))
    print(f"status={result.status}")
    print(f"approval={result.approval_path}")
    print(f"next_stage_status={result.next_stage_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
