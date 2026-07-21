"""The SQLite compat migrations must upgrade legacy DBs in place."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text
from sqlmodel import create_engine

from harness_lab_app import db as db_module


def _legacy_engine(tmp_path):
    """A DB whose audit table predates the findings_json column."""
    engine = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE audit ("
                "id INTEGER PRIMARY KEY, run_id VARCHAR, auditor_model VARCHAR, "
                "status VARCHAR, markdown VARCHAR, error VARCHAR, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE run (id INTEGER PRIMARY KEY, run_id VARCHAR, "
                "state VARCHAR, notes VARCHAR, cancel_requested BOOLEAN)"
            )
        )
    return engine


def test_migration_adds_findings_json_to_legacy_audit(tmp_path, monkeypatch):
    engine = _legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{tmp_path}/legacy.db"),
    )

    db_module._apply_sqlite_compat_migrations()

    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info('audit')")).fetchall()
        }
    assert "findings_json" in columns

    # Idempotent: running again must not fail on the existing column.
    db_module._apply_sqlite_compat_migrations()
