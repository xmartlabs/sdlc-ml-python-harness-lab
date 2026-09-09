"""The database half of the Langfuse export, and its two endpoints.

`observability/` is pure: it reads `data/runs/<id>/` and returns dictionaries.
Everything else a trace should carry — the run's state, its exit code, its
score, its objective validation — exists only in the database. This is where
the two halves meet, so what matters here is that the database facts arrive and
that a disabled integration stays completely silent.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app import observability_service as module
from harness_lab_app.api import create_app
from harness_lab_app.models import Run, RunEvent, RunState, Validation
from harness_lab_app.observability.run_export import ExportResult
from harness_lab_app.observability_service import (
    ObservabilityService,
    export_run_traces,
)

RUN_ID = "tetris-v1-K-901"

LANGFUSE_VARS = (
    "LANGFUSE_ENABLED",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_TRACING_ENVIRONMENT",
    "LANGFUSE_PROJECT_URL",
    "LANGFUSE_SESSION_PREFIX",
    "LANGFUSE_AUTO_EXPORT",
)


@pytest.fixture(autouse=True)
def _clean_langfuse_env(monkeypatch):
    for name in LANGFUSE_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Run(
                run_id=RUN_ID,
                run_config_id=1,
                state=RunState.failed,
                exit_code=137,
                failure_reason="container killed",
                score=4,
                interventions=2,
            )
        )
        session.add(
            Validation(
                run_id=RUN_ID,
                objective={"smoke": "fail"},
                composite=3.5,
            )
        )
        session.commit()
        yield session


def _enable(monkeypatch, **overrides: str) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


def _route(app, path: str, method: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


# -- what the database contributes -------------------------------------------


def test_run_extra_carries_the_facts_that_are_not_on_disk(db):
    extra = ObservabilityService(db).run_extra(RUN_ID)

    assert extra["state"] == "failed"
    assert extra["exit_code"] == 137
    assert extra["failure_reason"] == "container killed"
    assert extra["score"] == 4
    assert extra["validation"]["objective"] == {"smoke": "fail"}
    assert extra["validation"]["composite"] == 3.5


def test_an_unknown_run_still_yields_its_id(db):
    """Exporting a run directory the database never saw must not crash."""
    assert ObservabilityService(db).run_extra("ghost") == {"run_id": "ghost"}


def test_nulls_are_dropped_rather_than_sent_as_none(db):
    extra = ObservabilityService(db).run_extra(RUN_ID)
    assert "parent_run_id" not in extra
    assert "batch_id" not in extra


# -- the off switch ----------------------------------------------------------


def test_status_explains_why_there_is_nothing_to_look_at(db):
    status = ObservabilityService(db).status(RUN_ID)

    assert status["enabled"] is False
    assert "LANGFUSE_ENABLED" in status["reason"]
    # The session id is still reported: it is what a later backfill will use.
    assert status["session_id"] == f"bench-{RUN_ID}"
    assert status["session_url"] == ""


def test_status_links_straight_to_the_session_when_the_project_is_known(
    db, monkeypatch
):
    _enable(monkeypatch, LANGFUSE_PROJECT_URL="https://cloud.langfuse.com/project/p1")
    status = ObservabilityService(db).status(RUN_ID)

    assert status["enabled"] is True
    assert status["session_url"].endswith(f"/sessions/bench-{RUN_ID}")


def test_the_worker_hook_does_nothing_at_all_when_tracing_is_off(db, monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("a disabled integration must not build a payload")

    monkeypatch.setattr(module, "export_run", _explode)

    assert export_run_traces(db, RUN_ID) is None
    assert db.exec(select(RunEvent)).all() == []


def test_auto_export_off_keeps_the_manual_button_working(db, monkeypatch):
    _enable(monkeypatch, LANGFUSE_AUTO_EXPORT="false")
    monkeypatch.setattr(module, "export_run", lambda *_a, **_k: _result(ok=True))

    assert export_run_traces(db, RUN_ID) is None
    assert ObservabilityService(db).status(RUN_ID)["auto_export"] is False


# -- what the run log says ---------------------------------------------------


def _result(*, ok: bool, reason: str = "") -> ExportResult:
    return ExportResult(
        run_id=RUN_ID,
        session_id=f"bench-{RUN_ID}",
        ok=ok,
        spans=4,
        traces=3,
        reason=reason,
    )


def test_a_successful_export_is_recorded_in_the_run_log(db, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(module, "export_run", lambda *_a, **_k: _result(ok=True))

    result = export_run_traces(db, RUN_ID)

    assert result is not None and result.ok
    event = db.exec(select(RunEvent)).one()
    assert event.level == "info"
    assert "4 spans" in event.message


def test_a_failed_export_is_a_warning_not_an_error(db, monkeypatch):
    """An unreachable Langfuse costs a trace, not a run.

    The run has exited and its scores are written before this fires, so an
    ERROR line here would put a lost trace in the same filter as a failed
    validation — and an operator working away from the Langfuse host would
    read a green run as a broken one.
    """
    _enable(monkeypatch)
    monkeypatch.setattr(
        module, "export_run", lambda *_a, **_k: _result(ok=False, reason="Langfuse 503")
    )

    export_run_traces(db, RUN_ID)

    event = db.exec(select(RunEvent)).one()
    assert event.level == "warn"
    assert "Langfuse 503" in event.message


def test_an_exporter_that_raises_still_leaves_the_run_alone(db, monkeypatch):
    _enable(monkeypatch)

    def _boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(module, "export_run", _boom)

    assert export_run_traces(db, RUN_ID) is None
    event = db.exec(select(RunEvent)).one()
    assert event.level == "warn"
    assert "kaboom" in event.message


# -- endpoints ---------------------------------------------------------------


def test_the_status_endpoint_answers_for_a_lab_without_langfuse(db):
    endpoint = _route(create_app(), "/api/runs/{run_id}/langfuse", "GET")

    payload = endpoint(RUN_ID, session=db)

    assert payload["enabled"] is False
    assert payload["session_id"] == f"bench-{RUN_ID}"


def test_the_export_endpoint_reports_the_skip_instead_of_failing(db, monkeypatch):
    monkeypatch.setattr(
        module, "export_run", lambda *_a, **_k: _result(ok=False, reason="off")
    )
    endpoint = _route(create_app(), "/api/runs/{run_id}/langfuse-export", "POST")

    payload = endpoint(RUN_ID, session=db)

    assert payload["ok"] is False
    assert payload["reason"] == "off"
    assert payload["run_id"] == RUN_ID


def test_the_export_endpoint_returns_what_was_sent(db, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(module, "export_run", lambda *_a, **_k: _result(ok=True))
    endpoint = _route(create_app(), "/api/runs/{run_id}/langfuse-export", "POST")

    payload = endpoint(RUN_ID, session=db)

    assert (payload["ok"], payload["spans"], payload["traces"]) == (True, 4, 3)
