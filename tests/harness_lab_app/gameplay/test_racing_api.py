"""HTTP shape, session gate and untrusted human controls for fixed gameplay."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_lab_app.gameplay.api import racing_router


def test_human_wire_refuses_claimed_scores_unknown_controls_and_nonfinite_values():
    app = FastAPI()
    app.include_router(racing_router)
    # Dependency never used: malformed bodies must fail before persistence.
    from harness_lab_app.db import get_session

    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        for data in [
            {
                "actions": [{"throttle": 1, "brake": 0, "steer": 0, "reset": 0}],
                "score": 100,
            },
            {"actions": [{"throttle": 2, "brake": 0, "steer": 0, "reset": 0}]},
            {"actions": [{"throttle": True, "brake": 0, "steer": 0, "reset": 0}]},
            {"actions": []},
            {"actions": [{"teleport": 1}]},
        ]:
            assert (
                client.post("/api/gameplay/racing/human", json=data).status_code == 422
            )


def test_gameplay_routes_are_covered_by_the_existing_session_gate(monkeypatch):
    import harness_lab_app.session_middleware as gate
    from harness_lab_app.api import create_app

    monkeypatch.setattr(gate, "auth_enabled", lambda: True)
    app = create_app()
    # No lifespan startup: avoid creating or seeding a real lab database.
    client = TestClient(app)
    assert client.get("/api/gameplay/racing/episodes").status_code == 401
    assert client.post("/api/gameplay/racing/human", json={}).status_code == 401
    assert client.post("/api/gameplay/racing/episodes", json={}).status_code == 401

    assert client.get("/api/gameplay/racing/ghosts/race-" + "a" * 20).status_code == 401
    assert client.post("/api/gameplay/racing/ghosts/calibration").status_code == 401
