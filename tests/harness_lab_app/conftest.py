from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
