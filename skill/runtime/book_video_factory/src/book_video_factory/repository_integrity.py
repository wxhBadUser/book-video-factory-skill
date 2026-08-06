from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def repository_root(start: Path | None = None) -> Path:
    origin = (start or Path(__file__)).resolve()
    for parent in origin.parents:
        if (
            (parent / "scripts/verify_vfinal_architecture.py").is_file()
            and (parent / "vendor/hbg-life-simulation").is_dir()
        ):
            return parent
    raise RuntimeError("repository root could not be located")


def repository_integrity_report(root: Path | None = None) -> dict[str, Any]:
    repository = (root or repository_root()).expanduser().resolve()
    scanner_path = repository / "scripts/verify_vfinal_architecture.py"
    spec = importlib.util.spec_from_file_location("book_video_factory_integrity", scanner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"repository integrity scanner is unavailable: {scanner_path}")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    return scanner.scan_repository(repository)


def repository_integrity_block(root: Path | None = None) -> dict[str, Any] | None:
    report = repository_integrity_report(root)
    if not report.get("critical_count", 0):
        return None
    return {
        "stage": "repository_integrity",
        "status": "blocked",
        "next_action": "resolve repository integrity findings",
        "critical_count": report.get("critical_count", 0),
        "findings": report.get("findings", []),
    }
