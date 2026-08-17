from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import api
from harness_lab_app.api import LoginRequest, create_app
from harness_lab_app.auth import (
    MAX_FAILURES,
    SESSION_COOKIE,
    hash_password,
    reset_throttle,
    verify_session,
)
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate

ADMIN_PASSWORD = "correct horse battery"
SESSION_SECRET = "test-session-secret"
# The real cost factor makes each login ~100ms; the tests exercise the logic,
# not the hardening.
_ADMIN_HASH = hash_password(ADMIN_PASSWORD, iterations=1_000)


def _enable_auth(monkeypatch) -> None:
    monkeypatch.setattr(api, "auth_enabled", lambda: True)
    monkeypatch.setattr(api, "admin_password_hash", lambda: _ADMIN_HASH)
    monkeypatch.setattr(api, "session_secret", lambda: SESSION_SECRET)


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


def test_login_endpoint_is_a_no_op_in_open_mode(monkeypatch):
    """No password configured: the UI must render, not be stranded on a form."""
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")
    monkeypatch.setattr(api, "auth_enabled", lambda: False)

    result = login(LoginRequest(password=""), Response())

    assert result == {"authenticated": True, "auth_enabled": False}


def test_login_endpoint_rejects_a_wrong_password(monkeypatch):
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")
    _enable_auth(monkeypatch)
    reset_throttle()

    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(password="wrong"), Response())

    assert exc.value.status_code == 401
    reset_throttle()


def test_login_endpoint_sets_a_verifiable_session_cookie(monkeypatch):
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")
    _enable_auth(monkeypatch)
    reset_throttle()
    response = Response()

    assert login(LoginRequest(password=ADMIN_PASSWORD), response) == {
        "authenticated": True,
        "auth_enabled": True,
    }

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    # Plain HTTP by default: a Secure cookie would be silently dropped.
    assert "Secure" not in cookie
    token = cookie.split(f"{SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    assert verify_session(SESSION_SECRET, token) is True


def test_login_endpoint_throttles_after_repeated_failures(monkeypatch):
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")
    _enable_auth(monkeypatch)
    reset_throttle()

    for _ in range(MAX_FAILURES):
        with pytest.raises(HTTPException):
            login(LoginRequest(password="wrong"), Response())
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(password=ADMIN_PASSWORD), Response())

    # Even the right password waits: the lockout is on the attempt, not the guess.
    assert exc.value.status_code == 429
    reset_throttle()


def test_logout_endpoint_clears_the_cookie():
    app = create_app()
    logout = _route_endpoint(app, "/api/auth/logout")
    response = Response()

    assert logout(response) == {"authenticated": False}
    assert SESSION_COOKIE in response.headers["set-cookie"]


def test_session_probe_reports_open_mode(monkeypatch):
    app = create_app()
    probe = _route_endpoint(app, "/api/auth/session")
    monkeypatch.setattr(api, "auth_enabled", lambda: False)

    assert probe() == {"authenticated": True, "auth_enabled": False}


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


def test_get_sandbox_endpoint_404_when_missing():
    app = create_app()
    get_sandbox = _route_endpoint(app, "/api/runs/{run_id}/sandbox", method="GET")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            get_sandbox("ghost", "terminal", session)
        assert exc.value.status_code == 404


def test_start_sandbox_endpoint_422_on_bad_kind():
    app = create_app()
    start = _route_endpoint(app, "/api/runs/{run_id}/sandbox", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            start("run-x", "banana", session)
        assert exc.value.status_code == 422


def test_start_sandbox_endpoint_409_without_workspace():
    app = create_app()
    start = _route_endpoint(app, "/api/runs/{run_id}/sandbox", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            start("no-such-run-workspace", "terminal", session)
        assert exc.value.status_code == 409


def test_validate_run_endpoint_greenlights_a_runnable_config():
    """The run form's Validate button: same checks as create, nothing written."""
    app = create_app()
    validate = _route_endpoint(app, "/api/runs/preflight", method="POST")
    with _session() as session:
        result = validate(
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
            session,
        )
        assert result["ok"] is True


def test_validate_run_endpoint_reports_the_reason_instead_of_raising():
    """A blocked config is an answer, not an error — the form renders `detail`."""
    app = create_app()
    validate = _route_endpoint(app, "/api/runs/preflight", method="POST")
    with _session() as session:
        result = validate(
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="",
            ),
            session,
        )
        assert result["ok"] is False
        assert "Executor model is required" in result["detail"]


def test_validate_run_endpoint_persists_nothing():
    app = create_app()
    validate = _route_endpoint(app, "/api/runs/preflight", method="POST")
    with _session() as session:
        validate(
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
            session,
        )
        assert RunService(session).list_runs() == []
