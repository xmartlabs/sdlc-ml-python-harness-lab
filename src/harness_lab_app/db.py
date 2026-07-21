from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
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
    return create_engine(settings.database_url, connect_args=connect_args)


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


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
