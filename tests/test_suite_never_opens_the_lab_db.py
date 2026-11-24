"""The suite never opens the live lab database (2026-11-24, see tests/conftest.py).

Opening ``data/app/harness_lab.db`` from the host while the stack runs deletes
the WAL the api still writes to, and from then on launches vanish. Two checks:
the module engine points at the scratch file, and running a script that calls
``init_db()`` leaves the lab's database files untouched.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from harness_lab_app import db
from harness_lab_app.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_DB = REPO_ROOT / "data" / "app" / "harness_lab.db"


def _lab_db_files() -> dict[str, float | None]:
    return {
        suffix: (path.stat().st_mtime_ns if path.exists() else None)
        for suffix in ("", "-wal", "-shm")
        for path in [LAB_DB.with_name(LAB_DB.name + suffix)]
    }


def test_the_module_engine_is_not_the_lab_database():
    assert db.engine.url.database is not None
    assert Path(db.engine.url.database).resolve() != LAB_DB.resolve()
    assert "harness_lab.db" not in get_settings().database_url


def test_a_script_that_calls_init_db_leaves_the_lab_database_alone():
    """The prune refusal that used to open the lab DB from the host."""
    spec = importlib.util.spec_from_file_location(
        "prune_script", REPO_ROOT / "scripts" / "prune.py"
    )
    assert spec and spec.loader
    prune = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prune)

    before = _lab_db_files()
    with pytest.raises(SystemExit):
        prune.main(["--all", "--model", "m", "--cases", "x"])
    assert _lab_db_files() == before


# -- the guard in harness_lab_app.db ------------------------------------------


def _engine_for(
    tmp_path, monkeypatch, *, explicit: bool, stack_up: bool, in_docker: bool = False
):
    from sqlmodel import create_engine

    path = tmp_path / "harness_lab.db"
    if explicit:
        monkeypatch.setenv("HARNESS_LAB_DATABASE_URL", f"sqlite:///{path}")
    else:
        monkeypatch.delenv("HARNESS_LAB_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "_stack_api_running", lambda: stack_up)
    real_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: in_docker if str(self) == "/.dockerenv" else real_exists(self),
    )
    engine = create_engine(f"sqlite:///{path}")
    db._refuse_host_open_of_live_db(engine, path)
    return engine, path


def test_the_host_cannot_open_the_default_db_while_the_stack_runs(
    tmp_path, monkeypatch
):
    engine, path = _engine_for(tmp_path, monkeypatch, explicit=False, stack_up=True)
    with pytest.raises(db.LabDatabaseInUse, match="make app-down"):
        engine.connect()
    assert not path.exists()  # refused before SQLite created or touched the file


@pytest.mark.parametrize(
    ("explicit", "stack_up", "in_docker"),
    [
        (False, False, False),  # stack down: host scripts may use the lab DB
        (True, True, False),  # make app-host / a scratch copy: explicit URL
        (False, True, True),  # the containers themselves
    ],
)
def test_the_guard_steps_aside_when_opening_is_safe(
    tmp_path, monkeypatch, explicit, stack_up, in_docker
):
    engine, _path = _engine_for(
        tmp_path, monkeypatch, explicit=explicit, stack_up=stack_up, in_docker=in_docker
    )
    with engine.connect():
        pass
