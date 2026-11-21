"""Where the lab's disk goes, and the purges that give it back (ADR-0095).

A run directory is mostly one thing: the archived copy of the repository the
agent worked in. Everything a comparison reads afterwards — tokens, cost, time,
scores, the decision — is already in the database. So purging is the deletion
of that copy (and of the sandboxes copied from it), never of the measurement.

Two kinds of run, two retention rules:

* **ad hoc** runs keep only their patch and logs, and lose even those once
  decided or after ``adhoc_retention_days``;
* **benchmark** runs keep everything until a "light mode" purge deletes their
  workspace copy; their diff, logs, steps and usage stay. A purged benchmark
  run can no longer open a sandbox, be re-validated or be continued, which is
  why it is marked ``purged_at`` and the purge is a button, not a schedule.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import AdhocRun, Run, RunState, Sandbox, utc_now

PURGE_SCOPES = (
    "adhoc_expired",
    "adhoc_all_files",
    "benchmark_workspaces",
    "sandboxes",
)
# A benchmark run is purgeable only once nothing is still going to read it.
_PURGEABLE_STATES = {RunState.completed, RunState.failed, RunState.cancelled}
_ACTIVE_SANDBOX = {"starting", "running"}


class StorageError(ValueError):
    """A purge request that cannot be honoured as asked."""


def tree_bytes(path: Path) -> int:
    """Bytes under ``path``, never following symlinks, zero when it is absent."""
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
    return total


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class StorageService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    # ---- report --------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Disk use by category, and what each purge would free right now."""
        from harness_lab_app.adhoc_service import is_adhoc_id

        runs_dir = self.settings.data_runs_dir
        adhoc_bytes = bench_ws = bench_rest = sandbox_bytes = 0
        adhoc_items = bench_items = sandbox_items = 0
        if runs_dir.is_dir():
            for child in runs_dir.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                if is_adhoc_id(child.name):
                    adhoc_bytes += tree_bytes(child)
                    adhoc_items += 1
                    continue
                bench_items += 1
                for part in child.iterdir():
                    size = tree_bytes(part)
                    if part.name == "workspace":
                        bench_ws += size
                    elif part.name.startswith("sandbox-"):
                        sandbox_bytes += size
                        sandbox_items += 1
                    else:
                        bench_rest += size
        live_bytes = tree_bytes(self.settings.workspace_dir)
        checkouts = self.settings.checkouts_dir
        adhoc_copies = bench_copies = 0
        adhoc_copy_items = bench_copy_items = 0
        if checkouts.is_dir():
            for child in checkouts.iterdir():
                if not child.is_dir():
                    continue
                size = tree_bytes(child)
                if is_adhoc_id(child.name):
                    adhoc_copies += size
                    adhoc_copy_items += 1
                else:
                    bench_copies += size
                    bench_copy_items += 1
        mirrors = tree_bytes(self.settings.target_mirrors_dir)
        categories = [
            {
                "key": "benchmark_workspaces",
                "label": "Benchmark run workspaces",
                "bytes": bench_ws,
                "items": bench_items,
            },
            {
                "key": "benchmark_artifacts",
                "label": "Benchmark run artifacts (diff, logs, steps)",
                "bytes": bench_rest,
                "items": bench_items,
            },
            {
                "key": "sandboxes",
                "label": "Sandbox copies",
                "bytes": sandbox_bytes,
                "items": sandbox_items,
            },
            {
                "key": "adhoc_runs",
                "label": "Ad hoc run files",
                "bytes": adhoc_bytes,
                "items": adhoc_items,
            },
            {
                "key": "adhoc_copies",
                "label": "Ad hoc repository copies",
                "bytes": adhoc_copies,
                "items": adhoc_copy_items,
            },
            {
                "key": "checkouts",
                "label": "Audited repository checkouts",
                "bytes": bench_copies,
                "items": bench_copy_items,
            },
            {
                "key": "live_workspaces",
                "label": "Workspaces of runs in progress",
                "bytes": live_bytes,
                "items": 0,
            },
            {
                "key": "mirrors",
                "label": "Repository mirrors (rebuilt on demand)",
                "bytes": mirrors,
                "items": 0,
            },
        ]
        light = self._benchmark_light_candidates(older_than_days=0)
        expired = self._adhoc_expired()
        return {
            "total_bytes": sum(item["bytes"] for item in categories),
            "categories": categories,
            "benchmark_light_candidates": {
                "runs": len(light),
                "bytes": sum(
                    tree_bytes(runs_dir / run_id / "workspace") for run_id in light
                ),
            },
            "adhoc_expired": {
                "runs": len(expired),
                "bytes": sum(tree_bytes(runs_dir / run_id) for run_id in expired),
            },
            "adhoc_retention_days": self.settings.adhoc_retention_days,
        }

    # ---- purges --------------------------------------------------------

    def purge(self, scope: str, older_than_days: int | None = None) -> dict[str, Any]:
        if scope not in PURGE_SCOPES:
            raise StorageError(f"scope must be one of {list(PURGE_SCOPES)}")
        if scope == "adhoc_expired":
            return self._purge_adhoc(self._adhoc_expired())
        if scope == "adhoc_all_files":
            return self._purge_adhoc(self._adhoc_purgeable())
        if scope == "benchmark_workspaces":
            days = 14 if older_than_days is None else max(0, int(older_than_days))
            return self._purge_benchmark_workspaces(days)
        return self._purge_sandboxes()

    def _adhoc_purgeable(self) -> list[str]:
        from harness_lab_app.adhoc_service import LIVE_APPLY_STATES, LIVE_RUN_STATES

        states = {run.run_id: run.state for run in self.session.exec(select(Run)).all()}
        return [
            row.run_id
            for row in self.session.exec(select(AdhocRun)).all()
            if not row.files_purged
            and row.apply_state not in LIVE_APPLY_STATES
            and states.get(row.run_id) not in LIVE_RUN_STATES
        ]

    def _adhoc_expired(self) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.adhoc_retention_days)
        candidates = set(self._adhoc_purgeable())
        expired = []
        for row in self.session.exec(select(AdhocRun)).all():
            if row.run_id not in candidates:
                continue
            created = _aware(row.created_at)
            if row.decision in {"rejected", "discarded"} or (
                created and created < cutoff
            ):
                expired.append(row.run_id)
        return expired

    def _purge_adhoc(self, run_ids: list[str]) -> dict[str, Any]:
        from harness_lab_app.adhoc_service import AdhocService

        service = AdhocService(self.session)
        freed = 0
        for run_id in run_ids:
            freed += service.purge_run_files(run_id)
        return {"freed_bytes": freed, "runs": len(run_ids)}

    def _benchmark_light_candidates(self, older_than_days: int) -> list[str]:
        from harness_lab_app.adhoc_service import is_adhoc_id

        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        live_sandboxes = {
            row.run_id
            for row in self.session.exec(select(Sandbox)).all()
            if row.status in _ACTIVE_SANDBOX
        }
        runs_dir = self.settings.data_runs_dir
        candidates = []
        for run in self.session.exec(select(Run)).all():
            if is_adhoc_id(run.run_id) or run.state not in _PURGEABLE_STATES:
                continue
            if run.run_id in live_sandboxes:
                continue
            ended = _aware(run.ended_at) or _aware(run.created_at)
            if older_than_days and ended and ended > cutoff:
                continue
            if (runs_dir / run.run_id / "workspace").is_dir():
                candidates.append(run.run_id)
        return candidates

    def _purge_benchmark_workspaces(self, older_than_days: int) -> dict[str, Any]:
        runs_dir = self.settings.data_runs_dir
        freed = 0
        purged = 0
        for run_id in self._benchmark_light_candidates(older_than_days):
            directory = runs_dir / run_id
            if directory.resolve().parent != runs_dir.resolve():
                continue
            for part in directory.iterdir():
                if part.name == "workspace" or part.name.startswith("sandbox-"):
                    freed += tree_bytes(part)
                    shutil.rmtree(part, ignore_errors=True)
            run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
            if run is not None:
                run.purged_at = utc_now()
                self.session.add(run)
            purged += 1
        self.session.commit()
        return {"freed_bytes": freed, "runs": purged}

    def _purge_sandboxes(self) -> dict[str, Any]:
        """Copies left by stopped sandboxes; a running one is never touched."""
        runs_dir = self.settings.data_runs_dir
        active = {
            (row.run_id, row.kind)
            for row in self.session.exec(select(Sandbox)).all()
            if row.status in _ACTIVE_SANDBOX
        }
        freed = 0
        count = 0
        if runs_dir.is_dir():
            for child in runs_dir.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                for part in child.glob("sandbox-*"):
                    kind = part.name.removeprefix("sandbox-")
                    if (child.name, kind) in active:
                        continue
                    freed += tree_bytes(part)
                    shutil.rmtree(part, ignore_errors=True)
                    count += 1
        return {"freed_bytes": freed, "runs": count}
