"""Advisory file locking for mutating lifecycle commands.

Sync / adopt / rollback acquire an exclusive lock so two operators (or a retry)
never mutate candidate state concurrently. Uses ``fcntl.flock`` on a lock file;
the lock is released and the descriptor closed on exit. A stale lock left by a
crashed process is reclaimed automatically (``flock`` is tied to the open fd, so
it vanishes when that process dies).
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle.errors import EXIT_PRECONDITION, LifecycleError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def file_lock(
    lock_path: Path, *, timeout: float = 10.0, poll: float = 0.1
) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``lock_path`` for the block's duration.

    Raises ``LifecycleError`` (exit 3) if the lock cannot be acquired within
    ``timeout`` seconds — another lifecycle operation is already running.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise LifecycleError(
                        f"Could not acquire lock {lock_path.name}; another lifecycle "
                        "operation is running.",
                        EXIT_PRECONDITION,
                    ) from exc
                time.sleep(poll)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
