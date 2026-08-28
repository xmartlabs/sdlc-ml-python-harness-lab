"""The SQLite compat migrations must upgrade legacy DBs in place."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from harness_lab_app import (
    db as db_module,
    models as _models,  # noqa: F401  (registers the tables)
)


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


def _legacy_staged_skill_engine(tmp_path):
    """A DB whose stagedskill table predates multi-file trees (one SKILL.md)."""
    engine = create_engine(f"sqlite:///{tmp_path}/staged.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE stagedskill ("
                "id INTEGER PRIMARY KEY, name VARCHAR, description VARCHAR, "
                "content VARCHAR, status VARCHAR, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO stagedskill "
                "  (name, description, content, status, created_at, updated_at) "
                "VALUES ('legacy', 'old one', '# Legacy', 'draft', "
                "        '2026-01-01 00:00:00', '2026-01-02 00:00:00')"
            )
        )
    return engine


def test_migration_widens_staged_skills_and_backfills_the_file(tmp_path, monkeypatch):
    engine = _legacy_staged_skill_engine(tmp_path)
    # Mirrors init_db: create_all first (adds stagedskillfile), then the migration.
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{tmp_path}/staged.db"),
    )

    db_module._apply_sqlite_compat_migrations()

    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info('stagedskill')")
            ).fetchall()
        }
        files = connection.execute(
            text(
                "SELECT path, content, encoding, sha256, size, created_at "
                "FROM stagedskillfile"
            )
        ).fetchall()

    assert {"content_hash", "source_url", "source_ref", "source_kind"} <= columns
    assert "imported_at" in columns
    assert len(files) == 1
    path, content, encoding, sha256, size, created_at = files[0]
    assert (path, content, encoding, size) == ("SKILL.md", "# Legacy", "utf-8", 8)
    assert sha256 == hashlib.sha256(b"# Legacy").hexdigest()
    # Timestamps come from the parent row, not "now" — the file is not new work.
    assert str(created_at).startswith("2026-01-01")

    # Idempotent: a second pass must not duplicate the backfilled file.
    db_module._apply_sqlite_compat_migrations()
    with engine.begin() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM stagedskillfile")
        ).scalar()
    assert count == 1


def _legacy_continuation_engine(tmp_path):
    """A DB whose run/runconfig tables predate continuations (ADR-0026)."""
    engine = create_engine(f"sqlite:///{tmp_path}/continuation.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE run (id INTEGER PRIMARY KEY, run_id VARCHAR, "
                "state VARCHAR, notes VARCHAR, cancel_requested BOOLEAN, "
                "batch_id VARCHAR)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE runconfig (id INTEGER PRIMARY KEY, case_id VARCHAR, "
                "prompt_variant VARCHAR, skills_disabled JSON, skills_extra JSON)"
            )
        )
        connection.execute(
            text("INSERT INTO run (run_id, state) VALUES ('old-run-1', 'completed')")
        )
    return engine


def test_migration_adds_continuation_columns_to_a_legacy_db(tmp_path, monkeypatch):
    engine = _legacy_continuation_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{tmp_path}/continuation.db"),
    )

    db_module._apply_sqlite_compat_migrations()

    with engine.begin() as connection:
        run_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info('run')")).fetchall()
        }
        config_columns = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info('runconfig')")
            ).fetchall()
        }
        # A run that predates continuations has no parent, and none is invented:
        # a fabricated one would be reported as a human intervention.
        parent = connection.execute(
            text("SELECT parent_run_id FROM run WHERE run_id = 'old-run-1'")
        ).scalar()
    assert "parent_run_id" in run_columns
    assert "continuation_prompt" in config_columns
    assert parent is None

    db_module._apply_sqlite_compat_migrations()
