"""Celery tasks that execute benchmark runs, one Docker container per run."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from celery.signals import worker_ready
from sqlmodel import Session, select

from harness_lab_app.audit_service import AuditService
from harness_lab_app.case_service import CaseService
from harness_lab_app.celery_app import celery_app
from harness_lab_app.config import get_settings
from harness_lab_app.db import engine, init_db
from harness_lab_app.executors import (
    build_run_environment,
    container_name_for_run,
    get_executor,
)
from harness_lab_app.models import CaseType, Run, RunEvent, RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.sandbox_service import SandboxService
from harness_lab_app.schemas import RunConfigCreate

_AUDIT_TIMEOUT_SECONDS = 300
_SANDBOX_PORTS = {"terminal": "7681/tcp", "desktop": "6080/tcp"}

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


def _write_case_spec(
    session: Session, run_id: str, case_id: str, prompt_variant: str
) -> None:
    """Stage a repo-task's provisioning spec for the DB-blind runner container.

    For SWE-bench / repo-task cases the runner must clone a subject repo before
    the agent runs, but the container has no DB access. The worker (which does)
    writes case_spec.json into the run dir on the host; because the repo is
    bind-mounted at /app, bench_run.py reads it at data/runs/<id>/case_spec.json
    (ADR-0009). No-op for non-repo cases (e.g. Tetris).
    """
    try:
        case = CaseService(session).get_case(case_id)
    except ValueError:
        return
    run_dir = get_settings().data_runs_dir / run_id
    stale_spec = run_dir / "case_spec.json"
    # Only SWE-bench cases provision a repo. NOTE: other cases (e.g. Tetris) may
    # carry a subject_repo field for provenance that is NOT a cloneable
    # owner/repo, so type — not subject_repo — is the gate.
    if case.type not in (CaseType.swe_bench_instance, CaseType.swe_pro_instance):
        # Remove any spec left over from a prior run reusing this id, so the
        # runner does not mistake this for a repo task.
        stale_spec.unlink(missing_ok=True)
        return
    meta = case.metadata_json or {}
    if case.type == CaseType.swe_pro_instance:
        # Pro lane (docs/design-swe-bench-pro-lane.md): docker-image
        # provisioning + containerized validation. NOTE: the gold patch is
        # deliberately absent — this file is agent-readable via the bind mount.
        spec = {
            "case_type": "swe_pro",
            "repo": case.subject_repo or "",
            "base_commit": case.base_commit or "",
            "dockerhub_image": meta.get("dockerhub_image", ""),
            "before_repo_set_cmd": meta.get("before_repo_set_cmd", ""),
            "run_script": meta.get("run_script", ""),
            "parser": meta.get("parser", ""),
            "fail_to_pass": json.loads(meta.get("fail_to_pass", "[]")),
            "pass_to_pass": json.loads(meta.get("pass_to_pass", "[]")),
            "selected_test_files_to_run": json.loads(
                meta.get("selected_test_files_to_run", "[]")
            ),
            "prompt_variant": prompt_variant,
            "prompt": case.prompt_variants.get(prompt_variant, ""),
        }
    else:
        spec = {
            "case_type": str(case.type),
            "repo": case.subject_repo or "",
            "repo_url": meta.get("repo_url", ""),
            "base_commit": case.base_commit or "",
            "python": meta.get("python", "3.11"),
            "install_pins": json.loads(meta.get("install_pins", "[]")),
            "test_patch": meta.get("test_patch", ""),
            "apply_test_patch": True,
            "fail_to_pass": json.loads(meta.get("fail_to_pass", "[]")),
            "pass_to_pass": json.loads(meta.get("pass_to_pass", "[]")),
            "prompt_variant": prompt_variant,
            "prompt": case.prompt_variants.get(prompt_variant, ""),
        }
    run_dir.mkdir(parents=True, exist_ok=True)
    stale_spec.write_text(json.dumps(spec, indent=2))


def _provision_pro_workspace(session: Session, run_id: str, case_id: str) -> None:
    """Pre-provision a SWE-bench Pro workspace (worker side — docker access).

    The DB-blind runner container has no docker socket, so the image-based
    provisioning must happen before it starts (docs/design-swe-bench-pro-lane.md).
    No-op for non-Pro cases; raises on failure so the run is marked failed with
    the reason instead of the runner dying on an unprovisioned workspace.
    """
    try:
        case = CaseService(session).get_case(case_id)
    except ValueError:
        return
    if case.type != CaseType.swe_pro_instance:
        return
    script = get_settings().repo_root / "scripts" / "swebench_pro_common.py"
    result = subprocess.run(
        [sys.executable, str(script), "--provision-run", run_id],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if line.strip():
            session.add(RunEvent(run_id=run_id, message=line[:500]))
    session.commit()
    if result.returncode != 0:
        raise RuntimeError(
            f"SWE-bench Pro provisioning failed (rc={result.returncode})"
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
            config_payload = _config_payload(session, run)
            command = BenchRunAdapter().build_command(run_id, config_payload)
            run.command = command
            session.add(run)
            session.commit()

            # Stage repo-task provisioning data for the container (no-op for
            # non-repo cases like Tetris).
            _write_case_spec(
                session, run_id, config_payload.case_id, config_payload.prompt_variant
            )
            # Pro lane only: image-based provisioning needs docker, which the
            # runner lacks — do it here before the container starts.
            _provision_pro_workspace(session, run_id, config_payload.case_id)

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
                    _autoscore_objective_case(session, run_id)
            # Attribution is useful for any run that produced step logs
            # (including cancelled/failed), so index it regardless of outcome.
            _index_attribution(session, run_id)
            _index_artifacts(session, run_id)
        except Exception as exc:
            service.mark_finished(run_id, -1, f"Executor error: {exc!r}")


def _index_artifacts(session: Session, run_id: str) -> None:
    """Best-effort artifact indexing so the UI can read metadata.json et al."""
    from harness_lab_app.artifact_service import ArtifactService

    try:
        ArtifactService(session).index_run(run_id)
    except Exception as exc:  # indexing must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Artifact indexing failed: {exc!r}",
            )
        )
        session.commit()


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


def _autoscore_objective_case(session: Session, run_id: str) -> None:
    """Complete objectively-scored cases without a manual score (SWE-bench).

    SWE-bench's official score is binary and fully automatic: resolved iff all
    FAIL_TO_PASS pass and no PASS_TO_PASS regresses — exactly what the run's
    objective validation already computed. For swe_bench_instance cases we
    therefore apply the score ourselves (resolved → 10, else 0) and complete
    the run, instead of parking it in awaiting_manual_score. Best-effort: any
    trouble leaves the run awaiting a manual score, never fails it.
    """
    from harness_lab_app.models import Validation
    from harness_lab_app.validation_service import ValidationService

    try:
        run_service = RunService(session)
        run = run_service.get_run(run_id)
        if run.state != RunState.awaiting_manual_score:
            return
        config = run_service.get_run_config(run.run_config_id)
        case = CaseService(session).get_case(config.case_id)
        if case.type != CaseType.swe_bench_instance:
            return
        validation = session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        if validation is None:
            return  # objective validation missing → manual fallback
        objective = validation.objective or {}
        passed = objective.get("passed")
        if passed is None:
            return  # inconclusive objective result → manual fallback
        summary = objective.get("summary", "")
        score = 10 if passed else 0
        notes = f"auto-scored (SWE-bench objective): {summary}".strip()
        ValidationService(session).apply_subjective(run_id, score, 0, {}, notes)
        run_service.score_run(run_id, score, 0, notes)
        session.add(
            RunEvent(
                run_id=run_id,
                message=f"Auto-scored {score}/10 from SWE-bench validation ({summary})",
            )
        )
        session.commit()
    except Exception as exc:  # auto-scoring must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Auto-score failed (score manually): {exc!r}",
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
        # Re-validate also completes SWE-bench runs that were parked awaiting a
        # manual score before auto-scoring existed (or whose validation ran late).
        _autoscore_objective_case(session, run_id)


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
                service.store_result(
                    run_id, markdown, findings=service.read_audit_findings(run_id)
                )
            else:
                service.store_error(run_id, "Audit finished but wrote no audit.md")
        else:
            tail = (result.stderr or result.stdout or "").strip()[-1500:]
            service.store_error(run_id, tail or "Audit failed (no output)")


def _sandbox_container_name(run_id: str, kind: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in run_id)
    return f"harness-sandbox-{kind}-{safe}"


def _launch_sandbox(client: Any, run_id: str, kind: str) -> tuple[str, int, str]:
    """Copy the run's workspace and launch a sandbox container (worker-side).

    Returns (container_name, host_port, url). The bind mount uses the HOST path
    (docker-out-of-docker), and the port is docker-assigned then read back.
    """
    settings = get_settings()
    host_repo_root = settings.host_repo_root or str(settings.repo_root)
    workspace = settings.data_runs_dir / run_id / "workspace"
    if not workspace.exists():
        raise RuntimeError(f"No workspace to sandbox at {workspace}")

    sandbox_dir = settings.sandbox_dir(run_id, kind)
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
    shutil.copytree(workspace, sandbox_dir)
    host_sandbox = os.path.join(
        host_repo_root, "data", "runs", run_id, f"sandbox-{kind}"
    )

    name = _sandbox_container_name(run_id, kind)
    with contextlib.suppress(Exception):
        client.containers.get(name).remove(force=True)

    port_key = _SANDBOX_PORTS[kind]
    container = client.containers.run(
        image=settings.sandbox_image,
        detach=True,
        name=name,
        environment={"MODE": kind},
        volumes={host_sandbox: {"bind": "/work", "mode": "rw"}},
        ports={port_key: None},
        labels={"harness-lab.sandbox": run_id, "harness-lab.sandbox-kind": kind},
    )
    # The host port mapping may take a moment to appear; poll briefly.
    host_port = 0
    for _ in range(20):
        container.reload()
        mapping = (container.ports or {}).get(port_key) or []
        if mapping and mapping[0].get("HostPort"):
            host_port = int(mapping[0]["HostPort"])
            break
        time.sleep(0.25)
    if not host_port:
        raise RuntimeError("Sandbox container did not expose a host port")

    path = "/vnc.html?autoconnect=1&resize=remote" if kind == "desktop" else "/"
    return name, host_port, f"http://localhost:{host_port}{path}"


@celery_app.task(name="harness_lab_app.tasks.start_sandbox", acks_late=True)
def start_sandbox(run_id: str, kind: str) -> None:
    """Launch a validation sandbox (terminal/desktop) for a run (worker-side)."""
    init_db()
    settings = get_settings()
    if settings.execution_backend != "docker":
        with Session(engine) as session:
            SandboxService(session).mark_error(
                run_id, kind, "Sandboxes require the docker execution backend."
            )
        return
    import docker  # local import: only the worker needs the socket

    client = docker.from_env()
    with Session(engine) as session:
        service = SandboxService(session)
        try:
            client.images.get(settings.sandbox_image)
        except Exception:
            service.mark_error(
                run_id,
                kind,
                "Sandbox image not found. Build it with `make sandbox-build`.",
            )
            return
        try:
            name, port, url = _launch_sandbox(client, run_id, kind)
            service.mark_running(run_id, kind, name, port, url)
        except Exception as exc:
            service.mark_error(run_id, kind, repr(exc)[:800])


@celery_app.task(name="harness_lab_app.tasks.stop_sandbox", acks_late=True)
def stop_sandbox(run_id: str, kind: str) -> None:
    """Stop + remove a sandbox container and its ephemeral workspace copy."""
    init_db()
    settings = get_settings()
    if settings.execution_backend == "docker":
        with contextlib.suppress(Exception):
            import docker

            client = docker.from_env()
            container = client.containers.get(_sandbox_container_name(run_id, kind))
            with contextlib.suppress(Exception):
                container.stop(timeout=5)
            container.remove(force=True)
    with contextlib.suppress(Exception):
        shutil.rmtree(settings.sandbox_dir(run_id, kind))
    with Session(engine) as session:
        SandboxService(session).mark_stopped(run_id, kind)


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

    client = docker.from_env()
    _stop_orphan_container(client, run_id)
    # Also tear down any validation sandboxes the run left running.
    for kind in ("terminal", "desktop"):
        with contextlib.suppress(Exception):
            container = client.containers.get(_sandbox_container_name(run_id, kind))
            with contextlib.suppress(Exception):
                container.stop(timeout=5)
            container.remove(force=True)
        with contextlib.suppress(Exception):
            shutil.rmtree(settings.sandbox_dir(run_id, kind))


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
