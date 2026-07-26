from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine


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
