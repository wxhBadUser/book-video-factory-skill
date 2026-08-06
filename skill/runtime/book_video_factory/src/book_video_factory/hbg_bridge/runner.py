from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from .provenance import _verify_vendor


class HbgRunnerError(RuntimeError):
    """A pristine vendored HBG command failed."""


_ALLOWED_SCRIPTS = {
    "phase2": {"init_project_style.mjs", "build_storyboard_base.mjs"},
    "phase4": {
        "build_narration.mjs",
        "audit_caption_semantics.mjs",
        "audit_storyboard_density.mjs",
    },
}


def repository_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json").is_file():
            return parent
    raise HbgRunnerError("cannot locate vendored HBG repository")


def run_hbg_node(
    script_name: str,
    project: Path,
    args: Sequence[str] = (),
    *,
    capability: str = "phase2",
) -> subprocess.CompletedProcess[str]:
    allowed = _ALLOWED_SCRIPTS.get(capability)
    if allowed is None:
        raise HbgRunnerError(f"unknown HBG stage capability: {capability}")
    if script_name not in allowed:
        label = "Phase 2" if capability == "phase2" else "Phase 4"
        raise HbgRunnerError(f"HBG script is not permitted for {label}: {script_name}")
    root = repository_root()
    try:
        _verify_vendor(root)
    except RuntimeError as error:
        raise HbgRunnerError(f"HBG vendor integrity failed: {error}") from error
    scripts = (root / "vendor/hbg-life-simulation/scripts").resolve()
    script = (scripts / script_name).resolve()
    try:
        script.relative_to(scripts)
    except ValueError as error:
        raise HbgRunnerError("HBG script path escapes vendor scripts") from error
    if not script.is_file():
        raise HbgRunnerError(f"vendored HBG script is missing: {script_name}")
    completed = subprocess.run(
        ["node", str(script), *map(str, args)],
        cwd=project.expanduser().resolve(),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise HbgRunnerError(
            f"HBG {script_name} failed with {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def initialize_style(project: Path, orientation: str) -> None:
    run_hbg_node(
        "init_project_style.mjs",
        project,
        (str(project.resolve()), "--orientation", orientation),
        capability="phase2",
    )


def validate_storyboard(project: Path) -> None:
    run_hbg_node("build_storyboard_base.mjs", project, capability="phase2")
