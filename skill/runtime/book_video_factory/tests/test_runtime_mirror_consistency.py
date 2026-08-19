from __future__ import annotations

import hashlib
from pathlib import Path


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/sync_runtime_mirror.py").is_file():
            return parent
    raise AssertionError("repository root with sync_runtime_mirror.py was not found")


def _hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for managed in ("src", "tests", "scripts", "schemas"):
        base = root / managed
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            relative = path.relative_to(root).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_canonical_runtime_and_execution_mirror_are_byte_identical() -> None:
    repository = _repository_root()
    canonical = repository / "skill/runtime/book_video_factory"
    mirror = repository / "book_video_factory"
    assert _hashes(canonical) == _hashes(mirror), (
        "runtime trees diverged; edit canonical skill/runtime/book_video_factory first, "
        "then run python scripts/sync_runtime_mirror.py"
    )
