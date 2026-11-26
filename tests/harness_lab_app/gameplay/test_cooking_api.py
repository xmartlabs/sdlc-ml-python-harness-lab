"""Cooking HTTP controls and task dispatch do not enter the racing environment."""

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_lab_app.db import get_session
from harness_lab_app.gameplay.api import cooking_router


def test_cooking_wire_refuses_scores_fractional_stations_and_foreign_ghosts():
    app = FastAPI()
    app.include_router(cooking_router)
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    for payload in (
        {"actions": [{"target": 1.5, "interact": 1.0}]},
        {"actions": [{"target": True, "interact": 1.0}]},
        {"actions": [{"target": 0, "interact": 1.0}], "score": 100},
        {
            "actions": [{"target": 0, "interact": 1.0}],
            "ghost_episode_id": "race-" + "a" * 20,
        },
        {"actions": [{"target": -1, "interact": 0.0}] * 94},
    ):
        assert (
            client.post("/api/gameplay/cooking/human", json=payload).status_code == 422
        )


def test_cooking_runtime_errors_are_unavailable_not_empty_success(monkeypatch):
    from harness_lab_app.gameplay.racing_service import CookingService

    app = FastAPI()
    app.include_router(cooking_router)
    app.dependency_overrides[get_session] = lambda: None

    def unavailable(*args, **kwargs):
        raise RuntimeError("private runtime details")

    monkeypatch.setattr(CookingService, "create_calibration", unavailable)
    response = TestClient(app).post("/api/gameplay/cooking/ghosts/calibration")
    assert response.status_code == 503
    assert response.json() == {"detail": "Game runtime is unavailable"}


def test_worker_dispatches_cooking_using_stored_environment(
    session, tmp_path, monkeypatch
):
    import contextlib

    import harness_lab_app.gameplay.tasks as tasks
    from harness_lab_app.gameplay.racing_service import CookingService, RacingService

    service = CookingService(session, tmp_path)
    row = service._save(service._new(7, "model", "loaded"))
    calls = []
    monkeypatch.setattr(tasks, "Session", lambda _: contextlib.nullcontext(session))
    monkeypatch.setattr(tasks, "get_engine", lambda: None)
    monkeypatch.setattr(
        CookingService,
        "execute",
        lambda _self, episode_id: calls.append(("cooking", episode_id)),
    )
    monkeypatch.setattr(
        RacingService,
        "execute",
        lambda _self, episode_id: calls.append(("racing", episode_id)),
    )
    tasks.execute_race(row.episode_id)
    assert calls == [("cooking", row.episode_id)]


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
def test_cooking_routes_persist_recordings_using_cooking_schema(tmp_path):
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import harness_lab_app.gameplay.api as routes
    from harness_lab_app.gameplay.racing_service import CookingService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    class IsolatedCooking(CookingService):
        def __init__(self, session):
            super().__init__(session, tmp_path)

    app = FastAPI()
    app.include_router(
        routes.game_router(
            "/api/gameplay/cooking", IsolatedCooking, routes.HumanCooking
        )
    )
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    calibration = client.post("/api/gameplay/cooking/ghosts/calibration")
    assert calibration.status_code == 201
    episode_id = calibration.json()["episode_id"]
    assert client.get(f"/api/gameplay/cooking/ghosts/{episode_id}").status_code == 200
    response = client.post(
        "/api/gameplay/cooking/human",
        json={
            "actions": [{"target": 0, "interact": 1.0}],
            "ghost_episode_id": episode_id,
        },
    )
    assert response.status_code == 201
    assert response.json()["environment_id"] == "harbor-kitchen"
    assert response.json()["state"] == "completed"
    foreign = client.get("/api/gameplay/cooking/episodes/race-" + "a" * 20 + "/replay")
    assert foreign.status_code == 404
    session.close()
    engine.dispose()
