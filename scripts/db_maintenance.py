# ruff: noqa: E402
"""Compact the application database without losing any run.

Two things grow the SQLite file far past what the lab actually stores:

* Artifact rows for `workspace/` — the agent's checkout, indexed file by file
  before `artifact_service.EXCLUDED_ARTIFACT_DIRS` existed. One openlibrary run
  contributed 96,504 of the 723,536 rows on this laptop's database.
* Deleted rows are not returned to the filesystem: SQLite keeps the freed pages
  inside the file, so a 595 MB database can be mostly empty space.

This purges the rows the indexer would no longer write, plus artifacts whose
run is gone, then VACUUMs. Runs are never touched: only the artifact *index*,
which `POST /api/runs/{id}/artifacts/index` rebuilds from disk on demand.

Usage:
    uv run python scripts/db_maintenance.py [--dry-run]
"""

import sys
from pathlib import Path

from sqlalchemy import text

src_dir = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_dir))

from harness_lab_app.artifact_service import (
    EXCLUDED_ARTIFACT_DIR_PREFIXES,
    EXCLUDED_ARTIFACT_DIRS,
)
from harness_lab_app.config import get_settings
from harness_lab_app.db import _sqlite_path, get_engine


def _stale_artifact_clauses() -> tuple[str, dict[str, str]]:
    """SQL matching every artifact row the current indexer would not write."""
    clauses: list[str] = []
    params: dict[str, str] = {}
    for index, directory in enumerate(EXCLUDED_ARTIFACT_DIRS):
        key = f"dir{index}"
        clauses.append(f"name LIKE :{key}")
        params[key] = f"{directory}/%"
    for index, prefix in enumerate(EXCLUDED_ARTIFACT_DIR_PREFIXES):
        key = f"prefix{index}"
        clauses.append(f"name LIKE :{key}")
        params[key] = f"{prefix}%"
    # Rows left behind by a run that was deleted from the UI.
    clauses.append("run_id NOT IN (SELECT run_id FROM run)")
    return " OR ".join(clauses), params


def _size_mb(path: Path | None) -> str:
    if path is None or not path.exists():
        return "n/a"
    return f"{path.stat().st_size / 1_000_000:.1f} MB"


def compact(dry_run: bool = False) -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        print(f"⏭️  Not a SQLite database ({settings.database_url}); nothing to do.")
        return

    db_path = _sqlite_path(settings.database_url)
    engine = get_engine()
    where, params = _stale_artifact_clauses()

    with engine.connect() as connection:
        total = connection.execute(text("SELECT COUNT(*) FROM artifact")).scalar_one()
        stale = connection.execute(
            # nosec B608 - `where` is built from module constants; every value
            # the query matches on travels as a bound parameter.
            text(f"SELECT COUNT(*) FROM artifact WHERE {where}"),  # nosec B608
            params,
        ).scalar_one()
        journal = connection.execute(text("PRAGMA journal_mode")).scalar_one()

    print(f"📊 Database: {db_path} ({_size_mb(db_path)}, journal_mode={journal})")
    print(f"📊 Artifact rows: {total} total, {stale} stale")

    if dry_run:
        print("🔍 Dry run — nothing was deleted.")
        return

    if stale:
        with engine.begin() as connection:
            connection.execute(
                text(f"DELETE FROM artifact WHERE {where}"),  # nosec B608
                params,
            )
        print(f"🗑️  Deleted {stale} stale artifact rows")

    # VACUUM rewrites the whole file, so it cannot run inside a transaction.
    print("🧹 Vacuuming (this rewrites the file; it can take a minute)...")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM"))

    print(f"✅ Done. Database is now {_size_mb(db_path)}")


if __name__ == "__main__":
    compact(dry_run="--dry-run" in sys.argv[1:])
