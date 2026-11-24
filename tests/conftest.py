"""Suite-wide isolation that must happen before any lab module is imported.

``harness_lab_app.db`` builds its engine at import time from
``HARNESS_LAB_DATABASE_URL`` (default: the live lab database,
``data/app/harness_lab.db``). A test that reached that engine opened the lab's
own database from the host. On the Docker Desktop bind mount SQLite's locks do
not span the Mac and the VM, so the host process's close checkpointed and
*deleted* the ``-wal``/``-shm`` files the running api still held open. From then
on every api commit went to an unlinked WAL no other process could read: a
launch returned 201, and the worker found no such run and silently skipped it
(clinic-slots-t1-K-005, 2026-11-24; the same corruption hit on 2026-11-17 and
2026-11-19). ``test_prune_script::test_all_without_group_is_refused`` was the
test that did it, through ``prune.main`` → ``init_db()``.

This file is the root conftest, loaded before any test module, so the
redirection below is in place before the engine exists. A test that needs a
real database builds its own (see the ``session`` fixture).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_SCRATCH_DB = Path(tempfile.mkdtemp(prefix="harness-lab-tests-")) / "never-the-lab.db"

# Unconditional: an inherited HARNESS_LAB_DATABASE_URL may well point at the lab.
os.environ["HARNESS_LAB_DATABASE_URL"] = f"sqlite:///{_SCRATCH_DB}"

# Read by the regression test that keeps this promise.
SCRATCH_DB = _SCRATCH_DB
