from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app import run_service as run_service_module
from harness_lab_app.models import Artifact, Run, RunConfig, RunEvent, RunState
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _stub_config(**over) -> RunConfigCreate:
    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
    }
    base.update(over)
    return RunConfigCreate(**base)


def make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_command_preview_uses_bench_run():
    config = RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="sdlc",
        executor_model="gateway/nim-nemotron-super-120b",
        max_steps=10,
        dry_run=True,
    )

    with make_session() as session:
        service = RunService(session)
        command = service.preview_command("tetris-v1-J-002", config)

    assert command[:4] == ["uv", "run", "python", "scripts/bench_run.py"]
    assert "--dry-run" in command
    assert "tetris-v1-J-002" in command


def test_create_run_starts_pending():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="tetris-v1-J-002",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

        assert run.state == RunState.pending
        assert "--case" in run.command


def test_command_includes_fresh_flag():
    # Web runs are managed: the runner must reset a stale workspace and archive
    # it afterwards (ADR-0006), signalled by --fresh from the adapter.
    config = RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="sdlc",
        executor_model="stub-executor",
    )
    with make_session() as session:
        command = RunService(session).preview_command("tetris-v1-K-001", config)
    assert "--fresh" in command


def test_preflight_rejects_empty_executor_model():
    with make_session() as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError):
            service.create_run(
                RunCreate(
                    run_id="no-exec-1",
                    config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
                )
            )


def test_preflight_allows_dry_run_without_executor():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="dry-no-exec-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
    assert run.state == RunState.pending


def test_preflight_rejects_placeholder_gateway_token(monkeypatch):
    monkeypatch.setattr(
        run_service_module,
        "read_env_file_value",
        lambda *_a, **_k: "sk-local-change-me",
    )
    with make_session() as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError):
            service.create_run(
                RunCreate(
                    run_id="placeholder-token-1",
                    config=RunConfigCreate(
                        case_id="tetris-v1",
                        prompt_variant="sdlc",
                        executor_model="gateway/some-model",
                    ),
                )
            )


def test_preflight_allows_gateway_with_real_token(monkeypatch):
    monkeypatch.setattr(
        run_service_module, "read_env_file_value", lambda *_a, **_k: "sk-real-token"
    )
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="real-token-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="gateway/some-model",
                ),
            )
        )
    assert run.state == RunState.pending


def test_create_run_rejects_interactive_score_mode():
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-001",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="interactive",
            ),
        )


def test_create_run_rejects_unknown_score_mode():
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-002",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="anything",
            ),
        )


def test_create_run_rejects_auto_score_mode():
    # bench_run.py argparse only accepts manual|interactive|none; the web
    # flow standardizes on manual.
    with pytest.raises(ValidationError):
        RunCreate(
            run_id="tetris-v1-K-003",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                score_mode="auto",
            ),
        )


def test_create_run_rejects_duplicate_run_id():
    with make_session() as session:
        service = RunService(session)
        payload = RunCreate(
            run_id="dup-run-1",
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
        )
        service.create_run(payload)

        with pytest.raises(RunAlreadyExistsError):
            service.create_run(payload)


def test_dry_run_defaults_to_false():
    config = RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc")
    assert config.dry_run is False


def test_create_run_publishes_to_queue():
    enqueued: list[str] = []
    with make_session() as session:
        service = RunService(session, enqueue=enqueued.append)
        service.create_run(
            RunCreate(
                run_id="queued-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

    assert enqueued == ["queued-run-1"]


def test_create_run_marks_failed_on_enqueue_failure():
    def broken_enqueue(run_id: str) -> None:
        raise ConnectionError("broker down")

    with make_session() as session:
        service = RunService(session, enqueue=broken_enqueue)
        run = service.create_run(
            RunCreate(
                run_id="queued-run-2",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

        # A broker failure must not leave the run silently stuck in pending.
        assert run.state == RunState.failed
        assert run.failure_reason == "Queue broker unavailable — run not started"
        messages = [
            event.message
            for event in service.list_events("queued-run-2", ascending=True)
        ]
        assert any("Failed to enqueue" in message for message in messages)


def test_cancel_run_signals_running_process_instead_of_overwriting_state():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="cancel-signal-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("cancel-signal-1")

        cancelled = service.cancel_run("cancel-signal-1")

        assert cancelled.state == RunState.running
        assert cancelled.cancel_requested is True


def test_retry_run_rebuilds_command_with_resume():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="retry-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("retry-run-1")
        service.mark_finished("retry-run-1", 1, "boom")

        retried = service.retry_run("retry-run-1")

        assert retried.state == RunState.pending
        assert retried.exit_code is None
        assert retried.failure_reason is None
        assert "--resume" in retried.command
        assert run.run_id == retried.run_id


def test_mark_finished_reports_the_failure_reason_over_the_exit_code():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="finish-reason-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("finish-reason-1")
        service.mark_finished("finish-reason-1", 137, "Run timed out after 7200s")

        messages = [e.message for e in service.list_events("finish-reason-1")]

        # The watchdog killed the container, so 137 is the harness's own signal,
        # not a crash: the reason leads and the code stays as context.
        assert "Run timed out after 7200s (exit code 137)" in messages
        assert "Run exited with 137" not in messages


def test_mark_finished_falls_back_to_the_exit_code_without_a_reason():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="finish-reason-2",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("finish-reason-2")
        service.mark_finished("finish-reason-2", 1)

        messages = [e.message for e in service.list_events("finish-reason-2")]

        assert "Run exited with 1" in messages


def test_score_run_updates_fields_and_state():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="score-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        service.mark_running("score-run-1")
        service.mark_finished("score-run-1", 0)

        scored = service.score_run("score-run-1", 9, 2, "Manual QA passed")

        assert scored.state == RunState.completed
        assert scored.score == 9
        assert scored.interventions == 2
        assert scored.notes == "Manual QA passed"


def test_create_run_generates_auto_id_when_omitted():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(RunCreate(config=_stub_config(case_id="autoid-a")))
    assert run.run_id == "autoid-a-K-001"


def test_auto_id_increments_across_runs():
    with make_session() as session:
        service = RunService(session)
        first = service.create_run(RunCreate(config=_stub_config(case_id="autoid-b")))
        first_id = first.run_id
        second = service.create_run(RunCreate(config=_stub_config(case_id="autoid-b")))
        assert first_id == "autoid-b-K-001"
        assert second.run_id == "autoid-b-K-002"


def test_explicit_label_used_verbatim():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(label="my-custom-run", config=_stub_config(case_id="autoid-c"))
        )
    assert run.run_id == "my-custom-run"


def test_clone_run_reuses_config_with_new_id():
    with make_session() as session:
        service = RunService(session)
        source = service.create_run(
            RunCreate(config=_stub_config(case_id="autoid-d", max_steps=7))
        )
        source_id = source.run_id
        clone = service.clone_run(source_id)

        assert clone.run_id == "autoid-d-K-002"
        assert clone.run_id != source_id
        # Same arm: the cloned command carries the source's config (max steps etc.).
        assert "7" in clone.command


def test_delete_run_removes_run_and_dependent_rows():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-1", config=_stub_config()))
        session.add(Artifact(run_id="del-1", name="report.md", path="x"))
        session.commit()

        service.delete_run("del-1")

        assert session.exec(select(Run).where(Run.run_id == "del-1")).first() is None
        assert (
            session.exec(select(Artifact).where(Artifact.run_id == "del-1")).first()
            is None
        )
        assert not service.list_events("del-1")
        # The 1:1 RunConfig is removed too.
        assert session.exec(select(RunConfig)).first() is None


def test_delete_running_run_without_force_conflicts():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-run-2", config=_stub_config()))
        service.mark_running("del-run-2")

        with pytest.raises(RunDeleteConflictError):
            service.delete_run("del-run-2")

        assert service.get_run("del-run-2").state == RunState.running


def test_delete_running_run_with_force_succeeds():
    with make_session() as session:
        service = RunService(session)
        service.create_run(RunCreate(run_id="del-run-3", config=_stub_config()))
        service.mark_running("del-run-3")

        service.delete_run("del-run-3", force=True)

        assert (
            session.exec(select(Run).where(Run.run_id == "del-run-3")).first() is None
        )


def test_delete_missing_run_raises():
    with make_session() as session, pytest.raises(ValueError):
        RunService(session).delete_run("nope")


def test_delete_run_enqueues_container_cleanup():
    cleaned: list[str] = []
    with make_session() as session:
        service = RunService(session, enqueue_cleanup=cleaned.append)
        service.create_run(RunCreate(run_id="del-run-4", config=_stub_config()))

        service.delete_run("del-run-4")

    assert cleaned == ["del-run-4"]


def test_list_events_ascending_orders_chronologically():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="ordered-run-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
        session.add(RunEvent(run_id="ordered-run-1", message="first"))
        session.add(RunEvent(run_id="ordered-run-1", message="second"))
        session.commit()

        ascending = service.list_events("ordered-run-1", ascending=True)
        descending = service.list_events("ordered-run-1")

        assert [event.message for event in ascending][-2:] == ["first", "second"]
        assert [event.message for event in descending][:2] == ["second", "first"]


def test_run_list_carries_the_objective_verdict(session):
    """A run whose code does not start must not read as merely 'pending review'.

    Runs K-035/K-037/K-039 all failed the smoke check (exit 124, 1 and 2) and
    all three sat in `awaiting_manual_score` with an empty score — visually
    identical to a run waiting for a human. The lab had already computed the
    answer; the list just never showed it.
    """
    from harness_lab_app.api import create_app
    from harness_lab_app.models import Validation

    service = RunService(session)
    for run_id in ("broken-run", "good-run", "unchecked-run"):
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
    session.add(
        Validation(
            run_id="broken-run", objective={"strategy": "smoke", "passed": False}
        )
    )
    session.add(
        Validation(run_id="good-run", objective={"strategy": "smoke", "passed": True})
    )
    session.commit()

    app = create_app()
    endpoint = next(
        route.endpoint
        for route in app.router.routes
        if getattr(route, "path", None) == "/api/runs"
    )
    # Keyword, not positional: the endpoint also takes a `batch_id` query
    # filter, and binding by position would silently pass the session as it.
    verdicts = {
        row["run_id"]: row["objective_passed"] for row in endpoint(session=session)
    }

    assert verdicts["broken-run"] is False
    assert verdicts["good-run"] is True
    # Never validated: absent, not "failed" — the two mean different things.
    assert verdicts["unchecked-run"] is None


def test_preflight_refuses_a_run_the_missing_runner_image_would_kill(monkeypatch):
    """Without this the run is persisted, queued, and dies in ~17 ms — a failed
    run in the history for something one button fixes."""
    monkeypatch.setattr(
        "harness_lab_app.health_service.worker_facts",
        lambda **_kwargs: {
            "backend": "docker",
            "docker_ok": True,
            "runner_image_present": False,
            "runner_image": "harness-lab-runner:latest",
        },
    )
    with make_session() as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError, match="Build runner image"):
            service.create_run(
                RunCreate(
                    run_id="no-image-1",
                    config=RunConfigCreate(
                        case_id="tetris-v1",
                        prompt_variant="sdlc",
                        executor_model="lmstudio/stub",
                    ),
                )
            )


def test_preflight_is_silent_about_the_image_when_no_worker_replies(monkeypatch):
    """A slow or absent worker is the worker check's story to tell; blocking
    every run on it would be worse than the failure it prevents."""
    monkeypatch.setattr(
        "harness_lab_app.health_service.worker_facts", lambda **_kwargs: {}
    )
    with make_session() as session:
        RunService(session)._check_runner_image()


def test_preflight_does_not_ask_about_the_image_for_a_dry_run(monkeypatch):
    def _explode(**_kwargs):
        raise AssertionError("dry runs never touch the runner image")

    monkeypatch.setattr("harness_lab_app.health_service.worker_facts", _explode)
    with make_session() as session:
        run = RunService(session).create_run(
            RunCreate(
                run_id="dry-no-image-1",
                config=RunConfigCreate(
                    case_id="tetris-v1", prompt_variant="sdlc", dry_run=True
                ),
            )
        )
    assert run.state == RunState.pending
