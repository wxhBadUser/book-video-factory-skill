#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the Visual Hold Plan document (M3.1).

Usage:
    py scripts/build_visual_hold_plan.py \
        --groups <groups.json> \
        --contracts <caption_visual_contract.json> \
        [--approved-plan <golden_holds.json>] \
        [--character-register <register.json>] \
        [--release-id r2] \
        [--out <VISUAL_HOLD_PLAN.json>]

``groups`` is a list of caption-group records (Group Visual Contract style, or
the pilot SEGMENT_TASKS style with environment/character_ids).  ``contracts``
is the on-disk Caption Visual Contract (v2, dict keyed by caption_id).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from book_video_factory.semantic_alignment.visual_hold_planner import plan_visual_holds


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Visual Hold Plan document")
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--approved-plan", type=Path)
    parser.add_argument("--character-register", type=Path)
    parser.add_argument("--release-id", default="r2")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    groups_raw = json.loads(args.groups.read_text(encoding="utf-8"))
    if isinstance(groups_raw, dict):
        groups_raw = groups_raw.get("groups") or groups_raw.get("tasks") or []
    contracts_raw = json.loads(args.contracts.read_text(encoding="utf-8"))
    contracts = contracts_raw.get("contracts", contracts_raw)
    if not isinstance(contracts, dict):
        print(json.dumps({"status": "failed", "error": "contracts must be a mapping keyed by caption_id"}, ensure_ascii=False))
        return 2

    approved = None
    if args.approved_plan:
        approved = json.loads(args.approved_plan.read_text(encoding="utf-8"))
        if isinstance(approved, dict):
            approved = approved.get("holds", [])

    register = None
    if args.character_register:
        register = json.loads(args.character_register.read_text(encoding="utf-8"))

    try:
        document, findings = plan_visual_holds(
            groups=groups_raw,
            captions=contracts,
            approved_holds=approved,
            character_register=register,
            release_id=args.release_id,
        )
    except (ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "built" if not findings else "built_with_findings",
        "out": str(args.out),
        "hold_count": document["hold_count"],
        "group_count": document["group_count"],
        "findings": findings,
    }, ensure_ascii=False))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
