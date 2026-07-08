"""Celery tasks that execute benchmark runs, one Docker container per run."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections import deque
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from celery.signals import worker_ready
from sqlmodel import Session, select

from harness_lab_app.audit_service import AuditService
from harness_lab_app.celery_app import celery_app
from harness_lab_app.config import get_settings
from harness_lab_app.db import engine, init_db
from harness_lab_app.executors import (
    build_run_environment,
    container_name_for_run,
    get_executor,
)
from harness_lab_app.models import Run, RunEvent, RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate

_AUDIT_TIMEOUT_SECONDS = 300

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from harness_lab_app.executors import RunExecutor, RunHandle

_FAILURE_TAIL_LINES = 30


def _config_payload(session: Session, run: Run) -> RunConfigCreate:
    service = RunService(session)
    config = service.get_run_config(run.run_config_id)
    return RunConfigCreate(
        case_id=config.case_id,
        prompt_variant=config.prompt_variant,
        harness=config.harness,
        planner_model=config.planner_model,
        executor_model=config.executor_model,
        score_mode=config.score_mode,
        max_steps=config.max_steps,
        resume=config.resume,
        dry_run=config.dry_run,
    )


def _cancel_requested(session: Session, run_id: str) -> bool:
    session.expire_all()
    run = session.exec(select(Run).where(Run.run_id == run_id)).first()
    return bool(run and run.cancel_requested)


def _drain(handle: RunHandle, lines: Queue[tuple[str, str] | None]) -> None:
    try:
        for item in handle.iter_lines():
            lines.put(item)
    finally:
        lines.put(None)


def _stream_run(
    session: Session, run_id: str, handle: RunHandle, timeout_seconds: int
) -> tuple[bool, bool, deque[str]]:
    """Persist run output as RunEvents; watch for cancellation and timeout.

    Returns (cancelled, timed_out, tail_of_output).
    """
    lines: Queue[tuple[str, str] | None] = Queue()
    reader = threading.Thread(target=_drain, args=(handle, lines), daemon=True)
    reader.start()

    tail: deque[str] = deque(maxlen=_FAILURE_TAIL_LINES)
    cancelled = False
    timed_out = False
    started = time.monotonic()
    last_cancel_check = 0.0
    while True:
        now = time.monotonic()
        if not cancelled and not timed_out:
            if now - last_cancel_check >= 1.0:
                last_cancel_check = now
                if _cancel_requested(session, run_id):
                    cancelled = True
                    handle.stop()
            if now - started >= timeout_seconds:
                timed_out = True
                handle.stop()
        try:
            item = lines.get(timeout=0.2)
        except Empty:
            continue
        if item is None:
            break
        level, message = item
        if not message:
            continue
        session.add(RunEvent(run_id=run_id, level=level, message=message))
        session.commit()
        tail.append(message)
    return cancelled, timed_out, tail


def _execute_run_body(
    run_id: str, executor: RunExecutor, db_engine: Engine | None = None
) -> None:
    settings = get_settings()
    with Session(db_engine if db_engine is not None else engine) as session:
        service = RunService(session)
        run = session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None:
            return
        if run.state != RunState.pending:
            # acks_late redelivery or duplicate enqueue: never re-execute.
            session.add(
                RunEvent(
                    run_id=run_id,
                    message=f"Skipping execution: run state is {run.state}",
                )
            )
            session.commit()
            return

        service.mark_running(run_id)
        try:
            # Rebuild the command at execution time; the stored Run.command is
            # only a preview and may predate config/adapter changes.
            command = BenchRunAdapter().build_command(
                run_id, _config_payload(session, run)
            )
            run.command = command
            session.add(run)
            session.commit()

            handle = executor.start(run_id, command)
            cancelled, timed_out, tail = _stream_run(
                session, run_id, handle, settings.run_timeout_seconds
            )
            exit_code = handle.wait()
            if cancelled:
                service.mark_cancelled(run_id, exit_code)
            elif timed_out:
                service.mark_finished(
                    run_id,
                    exit_code if exit_code != 0 else -1,
                    f"Run timed out after {settings.run_timeout_seconds}s",
                )
            else:
                failure = None
                if exit_code != 0:
                    failure = "\n".join(tail)[-1000:] or "Run failed with no output"
                service.mark_finished(run_id, exit_code, failure)
                if exit_code == 0:
                    _run_objective_validation(session, run_id)
            # Attribution is useful for any run that produced step logs
            # (including cancelled/failed), so index it regardless of outcome.
            _index_attribution(session, run_id)
        except Exception as exc:
            service.mark_finished(run_id, -1, f"Executor error: {exc!r}")


def _index_attribution(session: Session, run_id: str) -> None:
    """Best-effort structured harness attribution from the run's logs."""
    from harness_lab_app.attribution_service import AttributionService

    try:
        AttributionService(session).index_run(run_id)
    except Exception as exc:  # attribution must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Attribution indexing failed: {exc!r}",
            )
        )
        session.commit()


def _run_objective_validation(session: Session, run_id: str) -> None:
    """Best-effort objective validation after a successful run."""
    from harness_lab_app.validation_service import ValidationService

    try:
        ValidationService(session).validate_run(run_id)
    except Exception as exc:  # validation must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Objective validation could not run: {exc!r}",
            )
        )
        session.commit()


@celery_app.task(name="harness_lab_app.tasks.execute_run", acks_late=True)
def execute_run(run_id: str) -> None:
    init_db()
    _execute_run_body(run_id, get_executor())


@celery_app.task(name="harness_lab_app.tasks.validate_run", acks_late=True)
def validate_run(run_id: str) -> None:
    """Objective validation on demand (runs in the worker, which has docker)."""
    init_db()
    with Session(engine) as session:
        _run_objective_validation(session, run_id)


@celery_app.task(name="harness_lab_app.tasks.audit_run", acks_late=True)
def audit_run(run_id: str, auditor_model: str | None = None) -> None:
    """Run the qualitative LLM audit for a run (worker-side).

    The audit is an LLM HTTP call to the gateway (no container). We shell out to
    the existing scripts/bench_audit.py so the CLI stays the single source of
    truth, with build_run_environment() so localhost gateway URLs are rewritten
    to the docker host alias (the worker is a container). The result (audit.md)
    is read back and stored on the Audit row for the frontend to poll.
    """
    init_db()
    settings = get_settings()
    with Session(engine) as session:
        service = AuditService(session)
        service.mark_running(run_id, auditor_model or "")

    python = settings.runner_python or "python"
    command = [python, "scripts/bench_audit.py", "--run", run_id]
    if auditor_model:
        command += ["--auditor-model", auditor_model]
    env = {**os.environ, **build_run_environment()}

    try:
        result = subprocess.run(
            command,
            cwd=str(settings.repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=_AUDIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        with Session(engine) as session:
            AuditService(session).store_error(
                run_id, f"Audit timed out after {_AUDIT_TIMEOUT_SECONDS}s"
            )
        return

    with Session(engine) as session:
        service = AuditService(session)
        if result.returncode == 0:
            markdown = service.read_audit_markdown(run_id)
            if markdown:
                service.store_result(run_id, markdown)
            else:
                service.store_error(run_id, "Audit finished but wrote no audit.md")
        else:
            tail = (result.stderr or result.stdout or "").strip()[-1500:]
            service.store_error(run_id, tail or "Audit failed (no output)")


@celery_app.task(name="harness_lab_app.tasks.cleanup_run_container", acks_late=True)
def cleanup_run_container(run_id: str) -> None:
    """Best-effort teardown of a deleted run's leftover container.

    The API cannot touch the docker socket (ADR-0002), so run deletion enqueues
    this task to the worker to stop/remove any container the run left behind.
    """
    settings = get_settings()
    if settings.execution_backend != "docker":
        return
    import docker  # local import: only the worker needs the socket

    _stop_orphan_container(docker.from_env(), run_id)


def _stop_orphan_container(client: Any, run_id: str) -> None:
    """Stop/remove a leftover run container that no task is attached to."""
    try:
        container = client.containers.get(container_name_for_run(run_id))
    except Exception:
        return
    # Stop/remove are best-effort; the container may already be gone.
    with contextlib.suppress(Exception):
        container.stop(timeout=10)
    with contextlib.suppress(Exception):
        container.remove(force=True)


def reconcile_stale_runs(
    client: Any | None = None, db_engine: Engine | None = None
) -> int:
    """Fail `running` runs left over from a worker crash/restart.

    After a restart no task is attached to those runs anymore (redelivered
    tasks skip non-pending runs), so any leftover container is stopped and
    the run is failed; retry re-runs it with --resume.
    """
    settings = get_settings()
    if client is None and settings.execution_backend == "docker":
        import docker  # local import: only the worker needs the socket

        client = docker.from_env()
    reconciled = 0
    with Session(db_engine if db_engine is not None else engine) as session:
        service = RunService(session)
        stale = list(session.exec(select(Run).where(Run.state == RunState.running)))
        for run in stale:
            if client is not None:
                _stop_orphan_container(client, run.run_id)
            service.mark_finished(
                run.run_id,
                -1,
                "Worker restarted while the run was in flight; retry to resume",
            )
            reconciled += 1
    return reconciled


@worker_ready.connect
def _on_worker_ready(**_kwargs: Any) -> None:
    init_db()
    reconcile_stale_runs()
