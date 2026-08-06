from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (
            (parent / "scripts/verify_vfinal_architecture.py").is_file()
            and (parent / "vendor/hbg-life-simulation").is_dir()
        ):
            return parent
    raise RuntimeError("repository root could not be located")


def repository_integrity_report() -> dict:
    root = repository_root()
    path = root / "scripts/verify_vfinal_architecture.py"
    spec = importlib.util.spec_from_file_location("book_video_factory_integrity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"repository integrity scanner is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan_repository(root)


def enforce_repository_integrity() -> None:
    """Fail closed before any managed status, run, or approval command."""
    entry = Path(sys.argv[0]).resolve()
    arguments = sys.argv[1:]
    if entry.parent.name != "scripts" or not arguments:
        return
    if "--help" in arguments or arguments[0] == "verify-repository":
        return
    report = repository_integrity_report()
    if report.get("critical_count", 0):
        print(
            json.dumps(
                {
                    "stage": "repository_integrity",
                    "status": "blocked",
                    "next_action": "resolve repository integrity findings",
                    "critical_count": report.get("critical_count", 0),
                    "findings": report.get("findings", []),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)


enforce_repository_integrity()
