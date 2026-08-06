from __future__ import annotations

import os
import shutil
from pathlib import Path


def path_for_bash(path: Path) -> str:
    resolved = path.expanduser().resolve()
    return resolved.as_posix() if os.name == "nt" else str(resolved)


def bash_executable() -> str:
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            git_bash = Path(git).resolve().parent.parent / "bin/bash.exe"
            if git_bash.is_file():
                return str(git_bash)
    return shutil.which("bash") or "bash"

