from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.api import create_app
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _route_endpoint(app, path: str, method: str | None = None):
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is not None and method not in getattr(route, "methods", set()):
            continue
        return route.endpoint
    raise AssertionError(f"Route not found: {path} ({method})")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_run(session, run_id: str) -> RunService:
    service = RunService(session)
    service.create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
        )
    )
    return service


def test_health_endpoint():
    app = create_app()

    assert _route_endpoint(app, "/api/health")() == {"status": "ok"}


def test_login_endpoint_accepts_default_credentials():
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")

    assert login(type("Login", (), {"username": "admin", "password": "admin"})()) == {
        "authenticated": True
    }


def test_delete_run_endpoint_404_when_missing():
    app = create_app()
    delete = _route_endpoint(app, "/api/runs/{run_id}", method="DELETE")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            delete("ghost", False, session)
        assert exc.value.status_code == 404


def test_delete_run_endpoint_409_when_running():
    app = create_app()
    delete = _route_endpoint(app, "/api/runs/{run_id}", method="DELETE")
    with _session() as session:
        service = _seed_run(session, "api-del-1")
        service.mark_running("api-del-1")
        with pytest.raises(HTTPException) as exc:
            delete("api-del-1", False, session)
        assert exc.value.status_code == 409


def test_get_audit_endpoint_404_when_missing():
    app = create_app()
    get_audit = _route_endpoint(app, "/api/runs/{run_id}/audit", method="GET")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            get_audit("ghost", session)
        assert exc.value.status_code == 404


def test_run_audit_endpoint_404_when_run_missing():
    app = create_app()
    run_audit = _route_endpoint(app, "/api/runs/{run_id}/audit", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            run_audit("ghost", None, session)
        assert exc.value.status_code == 404
