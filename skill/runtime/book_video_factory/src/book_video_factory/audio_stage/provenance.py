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


def tool_provenance(
    *,
    external_edge_service_exercised: bool | None = None,
    minimax_provider_exercised: bool | None = None,
) -> dict[str, Any]:
    """Return tool provenance for exactly one audio provider."""
    if (external_edge_service_exercised is None) == (minimax_provider_exercised is None):
        raise ValueError("exactly one of external_edge_service_exercised/minimax_provider_exercised is required")
    common = {
        "python": platform.python_version(),
        "node": _version(["node", "--version"]),
        "ffmpeg": _version(["ffmpeg", "-version"]),
        "ffprobe": _version(["ffprobe", "-version"]),
    }
    if minimax_provider_exercised is not None:
        common["minimax_provider_exercised"] = bool(minimax_provider_exercised)
    else:
        common["edge_tts"] = _version([sys.executable, "-m", "edge_tts", "--version"])
        common["external_edge_service_exercised"] = bool(external_edge_service_exercised)
    return common
