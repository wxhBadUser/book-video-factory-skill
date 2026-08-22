"""Project-scoped locking for V2 read-modify-write transactions."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_LOCK_REL = Path("manifests/host_orchestration/.PROJECT_TRANSACTION.lock")
_STATE_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DEPTH: dict[tuple[int, str], int] = {}
_HANDLES: dict[tuple[int, str], object] = {}


def transaction_lock_path(project: Path) -> Path:
    """Return the one lock path shared by all V2 project transactions."""
    root = project.expanduser().resolve()
    return root / _LOCK_REL


def _thread_lock(root: Path) -> threading.RLock:
    key = str(root)
    with _STATE_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _acquire_file_lock(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        handle.write(b"0")  # type: ignore[attr-defined]
        handle.flush()  # type: ignore[attr-defined]
        handle.seek(0)  # type: ignore[attr-defined]
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]


def _release_file_lock(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def project_transaction_lock(project: Path) -> Iterator[Path]:
    """Acquire the project lock before any authoritative state is re-read.

    The in-process lock makes nested calls and threaded completions safe.  The
    OS lock extends the same transaction boundary to separate Python workers.
    """
    root = project.expanduser().resolve()
    lock = _thread_lock(root)
    lock.acquire()
    thread_key = (threading.get_ident(), str(root))
    try:
        with _STATE_GUARD:
            depth = _DEPTH.get(thread_key, 0)
            _DEPTH[thread_key] = depth + 1
        if depth == 0:
            path = transaction_lock_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            _acquire_file_lock(handle)
            with _STATE_GUARD:
                _HANDLES[thread_key] = handle
        yield root
    finally:
        with _STATE_GUARD:
            depth = _DEPTH.get(thread_key, 1) - 1
            if depth <= 0:
                _DEPTH.pop(thread_key, None)
                handle = _HANDLES.pop(thread_key, None)
            else:
                _DEPTH[thread_key] = depth
                handle = None
        if handle is not None:
            try:
                _release_file_lock(handle)
            finally:
                handle.close()  # type: ignore[attr-defined]
        lock.release()
