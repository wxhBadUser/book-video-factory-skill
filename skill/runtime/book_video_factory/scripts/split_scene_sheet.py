#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.hbg_bridge.shell import bash_executable, path_for_bash
from book_video_factory.manifests import safe_project_output
from book_video_factory.orientation import OrientationError, validate_orientation_contract
from book_video_factory.production_visuals.sheet_validation import SheetValidationError, validate_scene_sheet


def main() -> int:
    parser = argparse.ArgumentParser(description="Split an approved 2x2 Host ImageGen sheet with the pristine HBG helper.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--sheet-id", required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    source = args.source.expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        parser.error("source sheet must be a real image file")
    mapping_path = root / "05_director/SHEET_MAP.json"
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"sheet map is unreadable: {error}")
    groups = [item for item in mapping.get("sheet_groups", []) if item.get("sheet_id") == args.sheet_id]
    if len(groups) != 1:
        parser.error("sheet_id is not a unique planned 2x2 group")
    group = groups[0]
    targets = group.get("split_targets")
    if not isinstance(targets, list) or len(targets) != 4:
        parser.error("planned sheet group does not contain four split targets")
    task_ids = group.get("task_ids")
    try:
        style = json.loads((root / "HBG_STYLE.json").read_text(encoding="utf-8"))
        orientation = str(style.get("orientation", ""))
        validate_orientation_contract(
            orientation,
            {
                **(style.get("canvas") if isinstance(style.get("canvas"), dict) else {}),
                "orientation": orientation,
            },
        )
    except (OSError, json.JSONDecodeError, OrientationError) as error:
        parser.error(f"HBG orientation is invalid: {error}")
    try:
        validate_scene_sheet(
            source,
            orientation=orientation,
            panel_order=task_ids,
            expected_panel_order=task_ids,
        )
    except SheetValidationError as error:
        parser.error(f"scene sheet validation failed: {error}")
    repo = repository_root()
    try:
        _verify_vendor(repo)
    except RuntimeError as error:
        parser.error(f"HBG vendor integrity failed: {error}")
    script = repo / "vendor/hbg-life-simulation/scripts/split_2x2.sh"
    with tempfile.TemporaryDirectory(prefix="book-video-sheet-") as temp:
        out = Path(temp) / "split"
        completed = subprocess.run(
            [bash_executable(), path_for_bash(script), path_for_bash(source), path_for_bash(out), args.sheet_id, "8", "8", orientation],
            cwd=repo, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            parser.error(completed.stderr.strip() or completed.stdout.strip() or "HBG split failed")
        generated = [out / f"{args.sheet_id}-{index:02d}.png" for index in range(1, 5)]
        if not all(path.is_file() and path.stat().st_size > 0 for path in generated):
            parser.error("HBG split did not create all four PNGs")
        published: list[Path] = []
        for generated_path, relative in zip(generated, targets, strict=True):
            target = safe_project_output(root, Path(relative))
            if target.exists():
                parser.error(f"refusing to overwrite existing scene image: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_suffix(".png.tmp")
            shutil.copy2(generated_path, staged)
            os.replace(staged, target)
            published.append(target)
    for path in published:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
