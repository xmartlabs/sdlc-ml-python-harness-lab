from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import config

# Credentials every test sees. Real in shape, fake in value: preflight only
# checks that a token is present and is not the documented placeholder.
FIXTURE_ENV = (
    "GATEWAY_BASE_URL=http://gateway.invalid/v1\n"
    "GATEWAY_TOKEN=sk-test-not-a-real-token\n"
    "CLAUDE_CODE_OAUTH_TOKEN=oat-test-not-a-real-token\n"
)


@pytest.fixture(autouse=True)
def _isolated_env_file(monkeypatch, tmp_path_factory) -> Path:
    """Never read the developer's real `.env`.

    Preflight resolves credentials from the repository `.env`, so without this
    the suite's result depended on whoever ran it: green on a machine with
    credentials configured, 11 failures on a fresh clone (and in CI, which
    writes no `.env`). Point every reader at a fixture file instead, so the
    suite asserts the same thing everywhere.

    Tests that need the *absence* of a credential patch
    ``run_service.read_env_file_value`` directly and are unaffected by this.

    Deliberately not written into ``tmp_path``: that is the working directory
    tests build fixtures in, and an extra file there changes what directory
    walks find.
    """
    env_file = tmp_path_factory.mktemp("envfile") / ".env"
    env_file.write_text(FIXTURE_ENV)
    monkeypatch.setattr(config, "env_file_path", lambda: env_file)
    return env_file


@pytest.fixture(autouse=True)
def _no_gateway_probe(monkeypatch) -> None:
    """Preflight must never make network calls from the test suite.

    Tests that exercise the probe itself call it directly (or clear this env).
    """
    monkeypatch.setenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", "1")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
