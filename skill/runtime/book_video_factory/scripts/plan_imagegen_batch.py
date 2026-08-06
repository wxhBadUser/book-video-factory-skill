#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_assets import (
    build_imagegen_prompt,
    select_shot_characters,
    validate_painting_light_cadence,
)


def read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize deterministic ImageGen prompts from locked cinematic contracts"
    )
    parser.add_argument("--art-direction", type=Path, required=True)
    parser.add_argument("--character-bible", type=Path, required=True)
    parser.add_argument("--shot-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    art = read_object(args.art_direction.expanduser().resolve())
    bible = read_object(args.character_bible.expanduser().resolve())
    plan = read_object(args.shot_plan.expanduser().resolve())
    characters = bible.get("characters")
    shots = plan.get("shots")
    if not isinstance(characters, list) or not isinstance(shots, list) or not shots:
        raise ValueError("character bible and shot plan require arrays")
    if str(art.get("painting_identity", "")).strip():
        validate_painting_light_cadence(shots)

    tasks: list[dict[str, object]] = []
    for shot in shots:
        if not isinstance(shot, dict):
            raise ValueError("shot plan entry must be an object")
        selected = select_shot_characters(shot, characters)
        tasks.append(
            {
                "shot_id": shot["shot_id"],
                "asset_tier": shot["asset_tier"],
                "prompt": build_imagegen_prompt(art, shot, selected),
                "character_ids": [
                    character["character_id"] for character in selected
                ],
                "provider": "codex-imagegen",
                "execution": (
                    "Call the installed imagegen Skill/tool, include only the approved "
                    "reference images for character_ids, then ingest the returned original "
                    "with ingest_imagegen_asset."
                ),
            }
        )
    result = {
        "schema_version": "1.0",
        "schema_id": "imagegen-batch-plan.v1",
        "release_id": plan.get("release_id"),
        "book": plan.get("book"),
        "status": "draft_pending_provider_calls_and_human_review",
        "tasks": tasks,
    }
    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(target), "task_count": len(tasks)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
