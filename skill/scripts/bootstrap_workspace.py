#!/usr/bin/env python3
"""Install the cleaned runtime and HBG engine, then create one book project shell."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parent
BUNDLED_FACTORY = SKILL_ROOT / "runtime" / "book_video_factory"
BUNDLED_HBG = REPO_ROOT / "vendor" / "hbg-life-simulation"
BUNDLED_SCANNERS = REPO_ROOT / "scripts"
ROOT_CONTRACT_FILES = (
    "AGENTS.md",
    "CODEX_AGENT.md",
    "FULL_PIPELINE.md",
    "ACCEPTANCE.md",
    "INSTALL.md",
    "TROUBLESHOOTING.md",
)
DEFAULT_STYLE_PROFILE_ID = "classic-narrator-hbg-v1"
DEFAULT_RELEASE_PROFILE_ID = "book-classic-narrator-hbg-16x9-v1"
DEFAULT_GENERATION_LANE = "host-imagegen"


def valid_slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise argparse.ArgumentTypeError(
            "slug must use lowercase letters, digits, and single hyphens"
        )
    return value


IGNORED_MANAGED_PARTS = {"__pycache__", ".pytest_cache"}
IGNORED_MANAGED_SUFFIXES = {".pyc", ".pyo"}


def _is_managed_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not (
        any(part in IGNORED_MANAGED_PARTS for part in relative.parts)
        or path.suffix in IGNORED_MANAGED_SUFFIXES
    )


def _managed_files(root: Path) -> dict[Path, Path]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if path.is_file() and _is_managed_file(path, root)
    }


def _same_file(first: Path, second: Path) -> bool:
    return first.stat().st_size == second.stat().st_size and first.read_bytes() == second.read_bytes()


def _sync_managed_tree(source_root: Path, destination_root: Path) -> list[Path]:
    """Mirror one bundled managed tree without touching files outside that tree."""
    if not source_root.is_dir():
        raise FileNotFoundError(f"bundled source is missing: {source_root}")

    changed: list[Path] = []
    source_files = _managed_files(source_root)
    destination_files = _managed_files(destination_root)

    for relative, destination in sorted(destination_files.items()):
        if relative not in source_files:
            destination.unlink()
            changed.append(destination)

    for relative, source in sorted(source_files.items()):
        destination = destination_root / relative
        if destination.is_file() and _same_file(source, destination):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        changed.append(destination)

    if destination_root.exists():
        for directory in sorted(
            (path for path in destination_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
    return changed


def _write_text_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def bootstrap_workspace(workspace: Path) -> list[Path]:
    root = workspace.expanduser().resolve()
    created = _sync_managed_tree(BUNDLED_FACTORY, root / "book_video_factory")
    created.extend(_sync_managed_tree(BUNDLED_HBG, root / "vendor/hbg-life-simulation"))
    # Architecture scanners live in the repo-root /scripts directory in a
    # development checkout. In a fresh tracked-files clone (git archive) that
    # directory may be absent; the runtime does not require the scanners to
    # function, so skip them gracefully when not bundled.
    if BUNDLED_SCANNERS.is_dir():
        created.extend(_sync_managed_tree(BUNDLED_SCANNERS, root / "scripts"))
    created.extend(_sync_managed_tree(SKILL_ROOT, root / "skill"))
    for relative in ROOT_CONTRACT_FILES:
        source = REPO_ROOT / relative
        destination = root / relative
        if destination.is_file() and _same_file(source, destination):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        created.append(destination)

    warehouse = root / "book_video_warehouse"
    for directory in (warehouse / "projects", warehouse / "operations", warehouse / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    readme = warehouse / "README.md"
    if _write_text_if_missing(
        readme,
        "# Book video warehouse\n\n"
        "This directory is local-only. It may contain source evidence, generated media, "
        "provider records and review decisions. Do not publish it without rights and "
        "privacy review.\n",
    ):
        created.append(readme)
    return created


def _project_files(project: Path) -> set[Path]:
    if not project.exists():
        return set()
    return {path for path in project.rglob("*") if path.is_file()}


def create_project(
    workspace: Path,
    slug: str,
    title: str,
    author: str,
    mode: str = "single-book",
    style_profile_id: str = DEFAULT_STYLE_PROFILE_ID,
    generation_lane: str | None = None,
) -> tuple[Path, list[Path]]:
    if mode != "single-book":
        raise ValueError("the active pipeline supports only single-book projects")
    if style_profile_id != DEFAULT_STYLE_PROFILE_ID:
        raise ValueError(f"the active pipeline requires style {DEFAULT_STYLE_PROFILE_ID}")
    resolved_lane = generation_lane or DEFAULT_GENERATION_LANE
    if resolved_lane != DEFAULT_GENERATION_LANE:
        raise ValueError(f"the active pipeline requires generation lane {DEFAULT_GENERATION_LANE}")

    root = workspace.expanduser().resolve()
    bootstrap_workspace(root)
    project = root / "book_video_warehouse/projects" / slug
    before = _project_files(project)
    command = [
        sys.executable,
        str(root / "book_video_factory/scripts/init_project.py"),
        "--warehouse",
        str(root / "book_video_warehouse"),
        "--slug",
        slug,
        "--book-title",
        title,
        "--author",
        author,
        "--mode",
        "single-book",
        "--style-profile",
        DEFAULT_STYLE_PROFILE_ID,
        "--release-profile",
        DEFAULT_RELEASE_PROFILE_ID,
        "--generation-lane",
        DEFAULT_GENERATION_LANE,
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip())
    after = _project_files(project)
    return project, sorted(after - before)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--slug", type=valid_slug)
    parser.add_argument("--book-title")
    parser.add_argument("--author")
    parser.add_argument("--mode", choices=("single-book",), default="single-book")
    parser.add_argument(
        "--style-profile",
        choices=(DEFAULT_STYLE_PROFILE_ID,),
        default=DEFAULT_STYLE_PROFILE_ID,
    )
    parser.add_argument(
        "--generation-lane",
        choices=(DEFAULT_GENERATION_LANE,),
        default=DEFAULT_GENERATION_LANE,
    )
    args = parser.parse_args()

    created = bootstrap_workspace(args.workspace)
    project = None
    if any(value is not None for value in (args.slug, args.book_title, args.author)):
        if not all((args.slug, args.book_title, args.author)):
            parser.error("--slug, --book-title, and --author must be supplied together")
        project, project_created = create_project(
            args.workspace,
            args.slug,
            args.book_title,
            args.author,
            args.mode,
            args.style_profile,
            args.generation_lane,
        )
        created.extend(project_created)

    print(
        json.dumps(
            {
                "workspace": str(args.workspace.expanduser().resolve()),
                "project": str(project) if project else None,
                "created_count": len(created),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
