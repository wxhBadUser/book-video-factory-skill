from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _version(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    text = (completed.stdout or completed.stderr).strip().splitlines()
    return text[0][:300] if completed.returncode == 0 and text else "unavailable"


def tool_provenance(*, external_edge_service_exercised: bool) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "node": _version(["node", "--version"]),
        "ffmpeg": _version(["ffmpeg", "-version"]),
        "ffprobe": _version(["ffprobe", "-version"]),
        "edge_tts": _version([sys.executable, "-m", "edge_tts", "--version"]),
        "external_edge_service_exercised": external_edge_service_exercised,
    }
