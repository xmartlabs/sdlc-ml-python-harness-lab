import hashlib
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.config import get_settings


def _sqlite_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url.removeprefix(prefix))


def get_engine():
    settings = get_settings()
    sqlite_path = _sqlite_path(settings.database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    connect_args = (
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(settings.database_url, connect_args=connect_args)
    if settings.database_url.startswith("sqlite"):
        _tune_sqlite(engine)
    return engine


def _tune_sqlite(engine) -> None:
    """Make SQLite survive a worker that writes while the UI reads.

    The default rollback journal takes a database-wide exclusive lock on every
    commit, and `_stream_run` commits once per log line of a running benchmark —
    so the API polling the same file competed with thousands of writes a minute
    and surfaced them as `database is locked`. WAL lets readers run against the
    last committed snapshot while a write is in flight.

    `synchronous=NORMAL` is the pairing WAL is designed for: durable across a
    process crash, and only at risk of losing the last commits if the host loses
    power — an acceptable trade for a lab's run log. `busy_timeout` turns the
    remaining contention into a short wait instead of an immediate error.

    Applied per connection because `synchronous` and `busy_timeout` are
    connection-scoped; `journal_mode` is a property of the file and simply
    re-confirms itself after the first connection sets it.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


engine = get_engine()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _apply_sqlite_compat_migrations()


def _apply_sqlite_compat_migrations() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info('run')")).fetchall()
        }
        if "cancel_requested" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE run ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "notes" not in columns:
            connection.execute(
                text("ALTER TABLE run ADD COLUMN notes VARCHAR NOT NULL DEFAULT ''")
            )
        for column in ("experiment_id", "condition_label"):
            # Nullable for the same reason as batch_id below: a run created
            # outside an experiment belongs to none, and inventing membership
            # would put runs into a design they were never part of.
            if column not in columns:
                connection.execute(text(f"ALTER TABLE run ADD COLUMN {column} VARCHAR"))
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_run_{column} ON run ({column})"
                    )
                )
        if "batch_id" not in columns:
            # Nullable on purpose: pre-existing runs were not part of a batch,
            # and backfilling them with a synthetic id would invent grouping
            # that never existed.
            connection.execute(text("ALTER TABLE run ADD COLUMN batch_id VARCHAR"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_run_batch_id ON run (batch_id)")
            )
        if "parent_run_id" not in columns:
            # Nullable for the same reason as batch_id: a run that predates
            # continuations had no parent, and inventing one would fabricate
            # lineage the leaderboard then reports as a human intervention.
            connection.execute(text("ALTER TABLE run ADD COLUMN parent_run_id VARCHAR"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_run_parent_run_id "
                    "ON run (parent_run_id)"
                )
            )

        audit_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info('audit')")).fetchall()
        }
        if audit_columns and "findings_json" not in audit_columns:
            connection.execute(
                text(
                    "ALTER TABLE audit ADD COLUMN findings_json JSON NOT NULL DEFAULT '{}'"
                )
            )

        runconfig_columns = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info('runconfig')")
            ).fetchall()
        }
        for column in ("skills_disabled", "skills_extra"):
            if runconfig_columns and column not in runconfig_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE runconfig ADD COLUMN {column} JSON NOT NULL DEFAULT '[]'"
                    )
                )
        if runconfig_columns and "continuation_prompt" not in runconfig_columns:
            connection.execute(
                text(
                    "ALTER TABLE runconfig ADD COLUMN continuation_prompt "
                    "VARCHAR NOT NULL DEFAULT ''"
                )
            )
        # Target repository pairing (ADR-0029). Empty string, not NULL: absence
        # of a target is a real state ("runs in the lab's own workspace"), and
        # the readers treat "" as that state rather than branching on None.
        for column in ("target_repo_id", "target_ref", "target_commit"):
            if runconfig_columns and column not in runconfig_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE runconfig ADD COLUMN {column} "
                        "VARCHAR NOT NULL DEFAULT ''"
                    )
                )
        # Harness condition (ADR-0031). The default backfills historical rows
        # with `lab_root`, which is not a placeholder — it is what those runs
        # actually did, so the column is accurate the moment it is added.
        if runconfig_columns and "harness_source" not in runconfig_columns:
            connection.execute(
                text(
                    "ALTER TABLE runconfig ADD COLUMN harness_source "
                    "VARCHAR NOT NULL DEFAULT 'lab_root'"
                )
            )

        # Case provenance and expected/forbidden surfaces (ADR-0032). Existing
        # cases default to `synthetic`: they were handcrafted or seeded, and
        # calling them `organization` would claim a lineage they do not have.
        case_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info('case')")).fetchall()
        }
        if case_columns and "provenance" not in case_columns:
            connection.execute(
                text(
                    'ALTER TABLE "case" ADD COLUMN provenance '
                    "VARCHAR NOT NULL DEFAULT 'synthetic'"
                )
            )
        for column in ("files_expected", "files_forbidden"):
            if case_columns and column not in case_columns:
                connection.execute(
                    text(
                        f'ALTER TABLE "case" ADD COLUMN {column} '
                        "JSON NOT NULL DEFAULT '[]'"
                    )
                )

        saved_proposal_columns = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info('savedproposal')")
            ).fetchall()
        }
        if saved_proposal_columns and "run_created_at" not in saved_proposal_columns:
            connection.execute(
                text("ALTER TABLE savedproposal ADD COLUMN run_created_at DATETIME")
            )
        for column in ("template_version", "template_fingerprint"):
            if saved_proposal_columns and column not in saved_proposal_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE savedproposal ADD COLUMN {column} "
                        "VARCHAR NOT NULL DEFAULT ''"
                    )
                )

        _migrate_staged_skills(connection)


def _migrate_staged_skills(connection) -> None:
    """Widen StagedSkill for multi-file trees and backfill legacy rows (ADR-0017).

    A skill that predates this migration has exactly one file — its SKILL.md,
    living in the ``content`` column. Insert it as a file row so every reader
    can assume the child table is complete, and so ``reconcile_all()`` can
    rebuild ``data/skills_staging/`` for old skills too.
    """
    rows = connection.execute(text("PRAGMA table_info('stagedskill')")).fetchall()
    columns = {row[1] for row in rows}
    if not columns:
        return

    for column in ("content_hash", "source_url", "source_ref", "source_kind"):
        if column not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE stagedskill ADD COLUMN {column} "
                    "VARCHAR NOT NULL DEFAULT ''"
                )
            )
    if "imported_at" not in columns:
        connection.execute(
            text("ALTER TABLE stagedskill ADD COLUMN imported_at DATETIME")
        )

    # The child table normally exists (create_all runs first in init_db); bail out
    # rather than crash if this is called on its own against an older schema.
    child = connection.execute(text("PRAGMA table_info('stagedskillfile')")).fetchall()
    if not child:
        return

    # One-time backfill: only rows with no file at all, so re-running is a no-op
    # and a skill whose SKILL.md was later edited through the file API is safe.
    orphans = connection.execute(
        text(
            "SELECT s.id, s.content, s.created_at, s.updated_at FROM stagedskill AS s "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM stagedskillfile AS f WHERE f.staged_skill_id = s.id"
            ")"
        )
    ).fetchall()
    for skill_id, content, created_at, updated_at in orphans:
        raw = (content or "").encode("utf-8")
        connection.execute(
            text(
                "INSERT INTO stagedskillfile "
                "  (staged_skill_id, path, content, encoding, sha256, size, "
                "   created_at, updated_at) "
                "VALUES (:id, 'SKILL.md', :content, 'utf-8', :sha, :size, "
                "        :created_at, :updated_at)"
            ),
            {
                "id": skill_id,
                "content": content or "",
                "sha": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "created_at": created_at,
                "updated_at": updated_at,
            },
        )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
