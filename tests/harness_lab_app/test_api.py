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


def test_retry_endpoints_404_when_the_run_is_missing():
    """A stale run page asks about a run that is gone: not found, never a 500."""
    app = create_app()
    options = _route_endpoint(app, "/api/runs/{run_id}/retry-options")
    retry = _route_endpoint(app, "/api/runs/{run_id}/retry", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            options("ghost", session)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            retry("ghost", "resume", session)
        assert exc.value.status_code == 404


def test_retry_endpoint_does_not_call_a_broken_config_not_found(monkeypatch):
    """Only an unknown run is a 404; any other ValueError is not "not found"."""
    app = create_app()
    retry = _route_endpoint(app, "/api/runs/{run_id}/retry", method="POST")

    def broken(self, run_id, mode="resume"):
        raise ValueError("stored config no longer validates")

    monkeypatch.setattr(RunService, "retry_run", broken)
    with _session() as session:
        _seed_run(session, "api-retry-broken")
        with pytest.raises(ValueError, match="no longer validates"):
            retry("api-retry-broken", "resume", session)


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


def test_continue_run_endpoint_404_when_the_parent_is_missing():
    from harness_lab_app.schemas import RunContinueRequest

    app = create_app()
    continue_run = _route_endpoint(app, "/api/runs/{run_id}/continue", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            continue_run("ghost", RunContinueRequest(prompt="keep going"), session)
        assert exc.value.status_code == 404


def test_continue_run_endpoint_409_when_the_run_has_not_stopped():
    from harness_lab_app.schemas import RunContinueRequest

    app = create_app()
    continue_run = _route_endpoint(app, "/api/runs/{run_id}/continue", method="POST")
    with _session() as session:
        service = _seed_run(session, "api-cont-1")
        service.mark_running("api-cont-1")
        with pytest.raises(HTTPException) as exc:
            continue_run("api-cont-1", RunContinueRequest(prompt="more"), session)
        assert exc.value.status_code == 409


def test_continue_request_rejects_an_empty_prompt():
    """Re-running the original prompt is what retry is for."""
    from pydantic import ValidationError

    from harness_lab_app.schemas import RunContinueRequest

    with pytest.raises(ValidationError):
        RunContinueRequest(prompt="   ")
    assert RunContinueRequest(prompt="  do X  ").prompt == "do X"


# --- failure diagnosis -------------------------------------------------------


def _fail_run(session, run_id: str, reason: str) -> RunService:
    from datetime import datetime

    from harness_lab_app.models import RunState

    service = _seed_run(session, run_id)
    run = service.get_run(run_id)
    run.state = RunState.failed
    run.failure_reason = reason
    run.started_at = datetime(2026, 11, 16, 14, 46)
    run.ended_at = datetime(2026, 11, 16, 15, 41)
    session.add(run)
    session.commit()
    return service


def test_failure_evidence_endpoint_returns_the_condensed_facts(tmp_path, monkeypatch):
    from harness_lab_app import config

    monkeypatch.setattr(config.get_settings(), "repo_root", tmp_path)
    app = create_app()
    evidence = _route_endpoint(app, "/api/runs/{run_id}/failure-evidence")
    with _session() as session:
        _fail_run(session, "api-diag-1", "Run stalled: no output for 10 minutes")
        body = evidence("api-diag-1", session)
    assert body["run"]["failure_kind"] == "stalled"
    assert body["run"]["executor_model"] == "stub-executor"
    assert body["run"]["limits"]["stall_seconds"] >= 0
    assert body["steps"] == []


def test_failure_evidence_is_only_for_a_failed_run():
    app = create_app()
    evidence = _route_endpoint(app, "/api/runs/{run_id}/failure-evidence")
    with _session() as session:
        _seed_run(session, "api-diag-pending")
        with pytest.raises(HTTPException) as exc:
            evidence("api-diag-pending", session)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            evidence("ghost", session)
        assert exc.value.status_code == 404


def test_diagnose_endpoint_reports_a_missing_model_as_503(tmp_path, monkeypatch):
    from harness_lab_app import config, llm_transport

    def unavailable(*args, **kwargs):
        raise llm_transport.TransportNotConfigured("no transport configured")

    monkeypatch.setattr(config.get_settings(), "repo_root", tmp_path)
    monkeypatch.setattr(llm_transport, "chat_reply", unavailable)
    app = create_app()
    diagnose = _route_endpoint(app, "/api/runs/{run_id}/diagnose", method="POST")
    with _session() as session:
        _fail_run(session, "api-diag-2", "Run timed out")
        with pytest.raises(HTTPException) as exc:
            diagnose("api-diag-2", "en", False, session)
        assert exc.value.status_code == 503
        assert "no transport" in exc.value.detail


def test_diagnose_endpoint_returns_the_reading_and_a_get_reads_it_back(
    tmp_path, monkeypatch
):
    from harness_lab_app import config, llm_transport

    def answer(model, system, user, **kwargs):
        return llm_transport.ChatReply(
            "## Cause\nslow", "gateway", model, model, 0.2, 0.2
        )

    monkeypatch.setattr(config.get_settings(), "repo_root", tmp_path)
    monkeypatch.setattr(llm_transport, "chat_reply", answer)
    app = create_app()
    diagnose = _route_endpoint(app, "/api/runs/{run_id}/diagnose", method="POST")
    stored = _route_endpoint(app, "/api/runs/{run_id}/diagnosis")
    with _session() as session:
        _fail_run(session, "api-diag-3", "Run timed out")
        assert stored("api-diag-3", "es", session) is None
        reading = diagnose("api-diag-3", "es", False, session)
        assert reading["text"].startswith("## Cause")
        assert stored("api-diag-3", "es", session) == reading
