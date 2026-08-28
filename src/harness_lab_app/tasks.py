"""Celery tasks that execute benchmark runs, one Docker container per run."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from celery.signals import worker_ready
from celery.worker.control import control_command
from sqlmodel import Session, select

from harness_lab_app import run_failures
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
from harness_lab_app.models import Case, CaseType, Run, RunEvent, RunState
from harness_lab_app.run_service import RunService, continuation_source_dir
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.sandbox_service import SandboxService
from harness_lab_app.schemas import RunConfigCreate

logger = logging.getLogger(__name__)

_AUDIT_TIMEOUT_SECONDS = 300
_SANDBOX_PORTS = {"terminal": "7681/tcp", "desktop": "6080/tcp"}

if TYPE_CHECKING:
    from collections.abc import Callable

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
        continuation_prompt=config.continuation_prompt,
        skills_disabled=list(config.skills_disabled),
        skills_extra=list(config.skills_extra),
    )


def _case_prompt_text(case: Case, prompt_variant: str) -> str:
    """The prompt body for a variant, whatever form the case stores it in.

    Cases created from the web (and every SWE-bench import) store the prompt
    *text* in ``prompt_variants``. Cases imported from a ``benchmarks/<id>/
    task.yaml`` store a repo-relative *path* instead, because that is what the
    YAML says. The runner only ever wants text, so resolve the path here — on
    the worker, which can read the repo — rather than teaching the container
    about benchmark layout.
    """
    raw = case.prompt_variants.get(prompt_variant, "")
    if not raw or "\n" in raw:
        return raw
    candidate = get_settings().repo_root / raw
    try:
        if candidate.is_file():
            return candidate.read_text()
    except OSError:
        return raw
    return raw


def _write_case_spec(
    session: Session,
    run_id: str,
    case_id: str,
    prompt_variant: str,
    *,
    continuation_prompt: str = "",
    parent_run_id: str | None = None,
) -> None:
    """Stage the case, as the DB holds it, for the DB-blind runner container.

    The runner container has no DB access, so the worker (which does) writes
    case_spec.json into the run dir on the host; because the repo is
    bind-mounted at /app, bench_run.py reads it at data/runs/<id>/case_spec.json
    (ADR-0009).

    Written for *every* case type, not only SWE-bench: without it a case that
    exists only in the DB — anything created from the web UI — has no
    benchmarks/<id>/task.yaml for bench_run.py to load, and the run dies with
    "Unknown case". A non-empty ``repo`` (an ``owner/repo`` slug) is what marks
    a spec as needing workspace provisioning; the generic branch leaves it
    empty. NOTE: a case may carry a ``subject_repo`` for provenance that is NOT
    cloneable (Tetris says ``ml-python-base``), so type — not subject_repo — is
    the gate.
    """
    try:
        case = CaseService(session).get_case(case_id)
    except ValueError:
        # No case row: drop any spec left over from a prior run reusing this id
        # rather than letting the runner act on a stranger's provisioning data.
        (get_settings().data_runs_dir / run_id / "case_spec.json").unlink(
            missing_ok=True
        )
        return
    run_dir = get_settings().data_runs_dir / run_id
    stale_spec = run_dir / "case_spec.json"
    meta = case.metadata_json or {}
    spec: dict[str, Any]
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
            "prompt": _case_prompt_text(case, prompt_variant),
        }
    elif case.type == CaseType.swe_bench_instance:
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
            "prompt": _case_prompt_text(case, prompt_variant),
        }
    else:
        # Everything the DB is the sole source of truth for: single_prompt,
        # multi_step, build_from_scratch, bugfix, repo_task. `repo` stays empty
        # so the runner provisions nothing and works in a bare workspace.
        spec = {
            "case_type": str(case.type),
            "case_id": case.case_id,
            "repo": "",
            "base_commit": case.base_commit or "",
            "prompt_variant": prompt_variant,
            "prompt": _case_prompt_text(case, prompt_variant),
            "step_prompts": list(case.step_prompts),
            "max_steps": case.max_steps,
            "constraints": list(case.constraints),
            "success_criteria": dict(case.success_criteria),
        }
    if continuation_prompt:
        # A continuation replaces the instruction, not the case (ADR-0026): the
        # SWE fields, constraints and success criteria still describe the task
        # being measured. The canned step prompts are dropped — they scripted
        # the original attempt, and replaying them would talk past the state the
        # workspace is actually in.
        spec["prompt"] = continuation_prompt
        spec["continues_run"] = parent_run_id or ""
        if "step_prompts" in spec:
            spec["step_prompts"] = []
    run_dir.mkdir(parents=True, exist_ok=True)
    stale_spec.write_text(json.dumps(spec, indent=2))


def _seed_continuation_workspace(run_id: str, parent_run_id: str) -> None:
    """Copy the parent's final workspace into the child's, before it starts.

    This is what makes a continuation continue anything: the runner is launched
    with ``--resume``, so ``validate_workspace`` keeps whatever is already at
    ``workspace/<run_id>`` instead of resetting it, and ``provision_workspace``
    short-circuits on the existing ``.git`` rather than re-cloning over it.

    ``symlinks=True`` matches what ``finalize_workspace`` archived with — a
    workspace can hold a dangling venv symlink, and following it would either
    explode or silently inline gigabytes of interpreter.

    Raises:
        RuntimeError: the parent has no workspace left, so there is nothing to
            continue from. Better a failed run with that sentence than a child
            that quietly starts from an empty directory.
    """
    source = continuation_source_dir(parent_run_id)
    if source is None:
        raise RuntimeError(
            f"Cannot continue run {parent_run_id}: its workspace is gone "
            "(deleted, or never created)."
        )
    dest = get_settings().workspace_dir / run_id
    if dest.exists():
        # A retry of a continuation re-enters here; the seed must be the
        # parent's state, not whatever the previous attempt left behind.
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, symlinks=True, ignore_dangling_symlinks=True)


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


@dataclass
class StreamOutcome:
    """Why streaming ended, and what the run last said before it did."""

    # None when the process ended on its own; otherwise "cancelled", "timeout",
    # "stalled", "model_silent" or "endpoint". At most one applies — the loop
    # stops the handle on the first one it sees.
    stop_reason: str | None
    tail: deque[str]
    # Seconds of silence when the run was stopped. Only meaningful for the two
    # silence verdicts, where it is the whole evidence, so it goes in the
    # failure message.
    silent_seconds: float = 0.0
    # Provider requests that failed back-to-back with no progress in between,
    # and the last one's text. The evidence behind an "endpoint" verdict.
    stream_errors: int = 0
    last_error: str = ""
    # Lifetime totals, never reset: requests handed to the provider (retries
    # included) and how many failed. `failed_requests >= model_requests > 0`
    # means the model was never heard from at all — the evidence behind
    # voiding a run that otherwise ended cleanly (see `_never_answered_failure`).
    model_requests: int = 0
    failed_requests: int = 0


def _stream_run(
    session: Session,
    run_id: str,
    handle: RunHandle,
    timeout_seconds: int,
    stall_seconds: int = 0,
    model_wait_seconds: int = 0,
    max_stream_errors: int = 0,
) -> StreamOutcome:
    """Persist run output as RunEvents; watch for the four ways a run goes bad.

    ``stall_seconds`` catches the failure ``timeout_seconds`` structurally
    cannot: a run that is not slow but *stuck*, producing nothing while its
    total budget ticks away. The two are indistinguishable from outside the
    process, so they need separate clocks — one since the start, one since the
    last byte of output.

    A single silence clock, though, cannot tell a wedged agent from a model
    that has simply not answered yet, and the two deserve opposite verdicts.
    opencode announces the moment it hands a request to the provider, so the
    budget applied to a silence is chosen by what the run was doing when it
    went quiet: ``stall_seconds`` for a tool call, ``model_wait_seconds`` for a
    request in flight. ``max_stream_errors`` covers the third case, where the
    provider is answering — with failures — and opencode would retry until the
    budget ran out. Zero disables any of the three.
    """
    lines: Queue[tuple[str, str] | None] = Queue()
    reader = threading.Thread(target=_drain, args=(handle, lines), daemon=True)
    reader.start()

    tail: deque[str] = deque(maxlen=_FAILURE_TAIL_LINES)
    stop_reason: str | None = None
    silent_seconds = 0.0
    stream_errors = 0
    last_error = ""
    model_requests = 0
    failed_requests = 0
    awaiting_model = False
    stopped = False
    started = time.monotonic()
    last_output = started
    last_cancel_check = 0.0
    while True:
        now = time.monotonic()
        if stop_reason is None:
            if now - last_cancel_check >= 1.0:
                last_cancel_check = now
                if _cancel_requested(session, run_id):
                    stop_reason = "cancelled"
            if stop_reason is None and now - started >= timeout_seconds:
                stop_reason = "timeout"
            if stop_reason is None:
                # Which silence this is decides both the budget and the
                # verdict. A local model is quiet for as long as it takes to
                # process the prompt before the first token — measured at 318s
                # for 24k tokens on the box that runs qwen3.6-35b — so the two
                # budgets are orders of magnitude apart, and charging a run the
                # wrong one is how a healthy model gets recorded as stuck.
                budget = model_wait_seconds if awaiting_model else stall_seconds
                quiet = now - last_output
                if budget > 0 and quiet >= budget:
                    stop_reason = "model_silent" if awaiting_model else "stalled"
                    silent_seconds = quiet
        if stop_reason is not None and not stopped:
            # Stopping the handle is what unblocks the reader thread: it kills
            # the container, the log stream hits EOF, `_drain` posts its
            # sentinel and this loop leaves through the `break` below.
            stopped = True
            handle.stop()
        try:
            item = lines.get(timeout=0.2)
        except Empty:
            continue
        if item is None:
            break
        # Any output at all resets the stall clock, including the blank lines
        # dropped just below — a run that is talking is a run that is alive.
        last_output = time.monotonic()
        level, message = item
        if not message:
            continue
        session.add(RunEvent(run_id=run_id, level=level, message=message))
        session.commit()
        tail.append(message)
        awaiting_model = run_failures.awaits_model_response(message)
        if run_failures.is_model_request(message):
            model_requests += 1
        if run_failures.is_stream_error(message):
            stream_errors += 1
            failed_requests += 1
            last_error = message
            if stop_reason is None and 0 < max_stream_errors <= stream_errors:
                stop_reason = "endpoint"
        elif not run_failures.is_retry_noise(message):
            # Real work happened, so whatever failed before was survivable.
            stream_errors = 0
    return StreamOutcome(
        stop_reason,
        tail,
        silent_seconds,
        stream_errors,
        last_error,
        model_requests,
        failed_requests,
    )


def _with_tail(message: str, tail: str, heading: str) -> str:
    """Attach the evidence to a verdict, so nobody has to go dig for it."""
    return f"{message}\n\n{heading}\n{tail}" if tail else message


def _stall_failure(outcome: StreamOutcome, tail: str) -> str:
    """The verdict for a run that went quiet with no model request in flight.

    Which means the agent's own last act is what it is stuck inside, and the
    tail is that act — the single most useful thing to read next, so it ships
    with the verdict.
    """
    minutes = round(outcome.silent_seconds / 60)
    message = (
        f"Run stalled: no output for {minutes} minutes and no model request was "
        "in flight, so the run was stopped rather than left to burn its "
        "remaining time budget. The agent is blocked in a tool call that will "
        "never return (a shell command holding its stdout pipe open, a process "
        "that ignores SIGTERM). Retry, or run it again with a narrower step."
    )
    return _with_tail(message, tail, "Last output before the stall:")


def _model_silence_failure(outcome: StreamOutcome, tail: str, budget: int) -> str:
    """The verdict for a run stopped while the model still owed it a first token.

    Deliberately not called a stall: the run did nothing wrong, a budget the
    lab chose ran out. Naming the knob in the message is the point — the
    operator reading this is the one who can tell "too slow to be worth it"
    from "too slow for a threshold set too low", and only they should decide.
    """
    minutes = round(outcome.silent_seconds / 60)
    message = (
        "No model response: the endpoint accepted the request and sent nothing "
        f"back for {minutes} minutes, so the run was stopped. Nothing was "
        "learned about the model here, so this does not count as an attempt. "
        "On a local model this silence is prompt-processing time before the "
        "first token and it grows with the prompt — if the model is merely "
        f"slow, raise run_model_wait_seconds (currently {budget}s). Otherwise "
        "the endpoint is wedged: check that the model is loaded and that its "
        "context window fits the prompt (docs/local-model-runtime-config.md)."
    )
    return _with_tail(message, tail, "Last output before the silence:")


def _endpoint_failure(outcome: StreamOutcome, tail: str) -> str:
    """The verdict for a run that spent itself retrying a provider that refused."""
    message = (
        f"Model endpoint failing: {outcome.stream_errors} provider requests "
        "failed back-to-back with no progress in between, so the run was "
        "stopped instead of retrying until its time budget ran out. Nothing "
        "was learned about the model, so this does not count as an attempt. "
        "Check that the endpoint is reachable from the runner container and "
        "that the model id and context window are configured "
        "(docs/local-model-runtime-config.md)."
    )
    error = outcome.last_error.strip()
    if error:
        return _with_tail(message, error[-600:], "Last error:")
    return _with_tail(message, tail, "Last output:")


def _model_never_answered(outcome: StreamOutcome) -> bool:
    """True when the run made model requests and not one of them was answered."""
    return 0 < outcome.model_requests <= outcome.failed_requests


def _never_answered_failure(outcome: StreamOutcome, tail: str) -> str:
    """The verdict for a run that ended on its own without one model response.

    The watchdog verdicts all require the run to still be alive when the
    threshold trips. Run instance_qutebrowser…-K-003 slipped past every one of
    them: both provider requests died in two seconds on a gateway config error
    (`max_tokens` above the model's completion cap) — fewer errors than
    `run_max_stream_errors` — opencode gave up, and the harness script carried
    on to scoring, archived an empty diff and exited 0. Recorded as
    `completed`, score 0, it read as "the model attempted the case and
    resolved nothing" on the leaderboard, when the model was never heard from.
    Sharing the `Model endpoint failing:` prefix keeps the taxonomy in
    :mod:`harness_lab_app.run_failures` a closed set.
    """
    message = (
        f"Model endpoint failing: all {outcome.model_requests} provider "
        "requests made by this run failed, so it ended without a single model "
        "response. The harness may still have exited cleanly and archived an "
        "empty result, but nothing was learned about the model, so this does "
        "not count as an attempt. The request itself is being rejected — "
        "check the model id, context window and completion-token cap at the "
        "gateway (docs/local-model-runtime-config.md)."
    )
    error = outcome.last_error.strip()
    if error:
        return _with_tail(message, error[-600:], "Last error:")
    return _with_tail(message, tail, "Last output:")


def _watchdog_failure(outcome: StreamOutcome, settings: Any, tail: str) -> str:
    """The failure reason for a run the watchdog stopped.

    Each branch names a different culprit — the agent, the endpoint, the clock
    — and :mod:`harness_lab_app.run_failures` reads these prefixes back to
    decide which of them measured anything. The two must move together.
    """
    if outcome.stop_reason == "stalled":
        return _stall_failure(outcome, tail)
    if outcome.stop_reason == "model_silent":
        return _model_silence_failure(
            outcome, tail, getattr(settings, "run_model_wait_seconds", 0)
        )
    if outcome.stop_reason == "endpoint":
        return _endpoint_failure(outcome, tail)
    return f"Run timed out after {settings.run_timeout_seconds}s"


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

            # A continuation starts from its parent's final state, so seed the
            # workspace before anything else touches it (ADR-0026).
            if run.parent_run_id:
                _seed_continuation_workspace(run_id, run.parent_run_id)

            # Stage repo-task provisioning data for the container (no-op for
            # non-repo cases like Tetris).
            _write_case_spec(
                session,
                run_id,
                config_payload.case_id,
                config_payload.prompt_variant,
                continuation_prompt=config_payload.continuation_prompt,
                parent_run_id=run.parent_run_id,
            )
            # Pro lane only: image-based provisioning needs docker, which the
            # runner lacks — do it here before the container starts. Skipped for
            # a continuation: the workspace it would build is the one we just
            # seeded, and rebuilding it would discard the parent's work.
            if not run.parent_run_id:
                _provision_pro_workspace(session, run_id, config_payload.case_id)

            handle = executor.start(run_id, command)
            outcome = _stream_run(
                session,
                run_id,
                handle,
                settings.run_timeout_seconds,
                getattr(settings, "run_stall_seconds", 0),
                getattr(settings, "run_model_wait_seconds", 0),
                getattr(settings, "run_max_stream_errors", 0),
            )
            exit_code = handle.wait()
            if outcome.stop_reason == "cancelled":
                service.mark_cancelled(run_id, exit_code)
            elif outcome.stop_reason is not None:
                service.mark_finished(
                    run_id,
                    exit_code if exit_code != 0 else -1,
                    _watchdog_failure(
                        outcome, settings, "\n".join(outcome.tail)[-600:]
                    ),
                )
            else:
                failure = None
                if _model_never_answered(outcome):
                    # Checked before the exit code: a clean exit earned by
                    # archiving an empty result is still not a measurement.
                    failure = _never_answered_failure(
                        outcome, "\n".join(outcome.tail)[-600:]
                    )
                elif exit_code != 0:
                    failure = (
                        "\n".join(outcome.tail)[-1000:] or "Run failed with no output"
                    )
                service.mark_finished(
                    run_id,
                    exit_code if failure is None or exit_code != 0 else -1,
                    failure,
                )
                if failure is None:
                    _run_objective_validation(session, run_id)
                    _autoscore_objective_case(session, run_id)
            # Attribution is useful for any run that produced step logs
            # (including cancelled/failed), so index it regardless of outcome.
            _index_attribution(session, run_id)
            _index_artifacts(session, run_id)
            # Last, and outside every branch: a failed run is exactly the one
            # an operator wants to open in Langfuse.
            _export_langfuse_traces(session, run_id)
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


def _autoscore_objective_case(
    session: Session,
    run_id: str,
    *,
    gateway: Callable[[str, str, str], str] | None = None,
) -> None:
    """Complete objectively-scored cases without a manual score (SWE-bench).

    SWE-bench's official score is binary and fully automatic: resolved iff all
    FAIL_TO_PASS pass and no PASS_TO_PASS regresses — exactly what the run's
    objective validation already computed. For both swe_bench_instance and
    swe_pro_instance cases with a conclusive verdict we apply the score
    ourselves (resolved → 10, else 0) and complete the run.

    SWE-bench Pro tests often cannot deliver a conclusive verdict in this
    environment (heavy per-instance images). Rather than park those runs in
    awaiting_manual_score — a human cannot eyeball a Pro resolution the way they
    VNC into Tetris — the audit LLM grades the diff and the run is recorded as
    ``llm_judge``. Best-effort throughout: any trouble leaves the run awaiting a
    manual score, never fails it. ``gateway`` is injectable for tests.
    """
    from harness_lab_app.models import Validation

    try:
        run_service = RunService(session)
        run = run_service.get_run(run_id)
        if run.state != RunState.awaiting_manual_score:
            return
        config = run_service.get_run_config(run.run_config_id)
        case = CaseService(session).get_case(config.case_id)
        if case.type not in (CaseType.swe_bench_instance, CaseType.swe_pro_instance):
            return
        validation = session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        objective = (validation.objective if validation else None) or {}
        passed = objective.get("passed")
        summary = objective.get("summary", "")

        if passed is not None:
            label = (
                "SWE-bench Pro"
                if case.type == CaseType.swe_pro_instance
                else "SWE-bench"
            )
            score = 10 if passed else 0
            notes = f"auto-scored ({label} objective): {summary}".strip()
            _record_autoscore(
                session, run_service, run_id, score, notes, source="objective"
            )
            return

        # No conclusive objective verdict.
        if case.type == CaseType.swe_bench_instance:
            return  # bare SWE-bench without a verdict → manual fallback
        _llm_judge_swe_pro(session, run_service, run_id, summary, gateway)
    except Exception as exc:  # auto-scoring must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Auto-score failed (score manually): {exc!r}",
            )
        )
        session.commit()


def _record_autoscore(
    session: Session,
    run_service: RunService,
    run_id: str,
    score: int,
    notes: str,
    *,
    source: str,
) -> None:
    """Apply an automatic score (objective or LLM judge) and complete the run."""
    from harness_lab_app.validation_service import ValidationService

    ValidationService(session).apply_subjective(
        run_id, score, 0, {}, notes, score_source=source
    )
    run_service.score_run(run_id, score, 0, notes)
    session.add(RunEvent(run_id=run_id, message=f"Auto-scored {score}/10 ({source})"))
    session.commit()


def _llm_judge_swe_pro(
    session: Session,
    run_service: RunService,
    run_id: str,
    objective_summary: str,
    gateway: Callable[[str, str, str], str] | None,
) -> None:
    """Grade a SWE-bench Pro run's diff with the audit LLM when tests can't."""
    from harness_lab_app.harness_lifecycle import judge

    run_dir = get_settings().data_runs_dir / run_id
    problem = _read_run_text(run_dir / "prompt.md")
    diff = _read_run_text(run_dir / "diff.patch")
    report = _read_run_text(run_dir / "report.md")
    if not diff and not problem:
        return  # nothing to grade → manual fallback
    # The sentinel "No objective validation defined" is noise, not a test summary.
    test_summary = objective_summary if "F2P" in objective_summary else ""
    gateway = gateway or _worker_gateway()
    model = os.environ.get("AUDITOR_MODEL") or judge.DEFAULT_JUDGE_MODEL
    result = judge.judge_functional_score(
        problem_statement=problem,
        diff=diff,
        objective_summary=test_summary,
        report=report,
        gateway=gateway,
        model=model,
    )
    rationale = result["rationale"]
    notes = f"LLM-judged ({model}): {rationale}".strip()
    _record_autoscore(
        session, run_service, run_id, result["score"], notes, source="llm_judge"
    )


def _read_run_text(path: Path) -> str:
    """Read a run artifact, or '' if it is missing/unreadable (best-effort)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _worker_gateway() -> Callable[[str, str, str], str]:
    """An in-process gateway caller for the worker (URLs host-alias-rewritten).

    Mirrors ``case_summary._gateway``: read GATEWAY_* from env/.env and, inside a
    container, rewrite ``localhost`` to the docker host alias. Raises on missing
    config or a bad response so the best-effort caller falls back to manual.
    """
    import urllib.error
    import urllib.request

    from harness_lab_app.config import read_env_file_value
    from harness_lab_app.executors import rewrite_localhost
    from harness_lab_app.gateway_models import (
        GATEWAY_USER_AGENT,
        normalize_model_name,
    )

    base = (
        os.environ.get("GATEWAY_BASE_URL")
        or read_env_file_value("GATEWAY_BASE_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("GATEWAY_TOKEN") or read_env_file_value("GATEWAY_TOKEN") or ""
    ).strip()
    if not base or not token:
        raise RuntimeError("gateway not configured (GATEWAY_BASE_URL/TOKEN)")
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    base = base.rstrip("/")

    def _call(model: str, system: str, user: str) -> str:
        payload = {
            "model": normalize_model_name(model),
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": GATEWAY_USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as resp:  # nosec B310
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

    return _call


def _export_langfuse_traces(session: Session, run_id: str) -> None:
    """Best-effort Langfuse export once a run reaches a terminal state.

    A no-op unless LANGFUSE_ENABLED and LANGFUSE_AUTO_EXPORT are both on. The
    exporter reads `data/runs/<id>/`, so re-running it later (or after a
    re-validation) rebuilds the same traces under the same ids.
    """
    from harness_lab_app.observability_service import export_run_traces

    try:
        export_run_traces(session, run_id)
    except Exception as exc:  # observability must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Langfuse export failed: {exc!r}",
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
        # Re-export so the validation verdict and the new score reach the trace
        # too; deterministic ids mean this updates rather than duplicates.
        _export_langfuse_traces(session, run_id)


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


@celery_app.task(name="harness_lab_app.tasks.improve_run", acks_late=True)
def improve_run(
    run_id: str, model: str | None = None, strictness: str | None = None
) -> None:
    """Synthesize improvement proposals from a run's audit (worker-side).

    Shells out to scripts/lab_improve.py in generate/dry-run mode (the CLI
    stays the single source of truth): reads audit.md + audit_findings.json,
    runs the LLM synthesis via the gateway, and writes
    data/runs/<id>/improvements.json (+ .md preview). Filing the GitHub issues
    stays a host-side step (`make improve RUN=<id> CREATE=1` — `gh` auth lives
    on the host, not in the worker). Progress lands in RunEvents; the frontend
    polls GET /api/runs/<id>/improvements for the artifact.
    """
    init_db()
    settings = get_settings()

    def _event(message: str, level: str = "info") -> None:
        with Session(engine) as session:
            session.add(RunEvent(run_id=run_id, level=level, message=message[:500]))
            session.commit()

    _event(f"Improvement synthesis started (model {model or 'auditor default'})")
    python = settings.runner_python or "python"
    command = [python, "scripts/lab_improve.py", "--run", run_id]
    if model:
        command += ["--model", model]
    if strictness:
        command += ["--strictness", strictness]
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
        _event(
            f"Improvement synthesis timed out after {_AUDIT_TIMEOUT_SECONDS}s",
            level="error",
        )
        return

    if result.returncode == 0:
        _event("Improvement proposals ready (improvements.json)")
    else:
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        _event(f"Improvement synthesis failed: {tail}", level="error")


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


@celery_app.task(name="harness_lab_app.tasks.reconcile_runs", acks_late=True)
def reconcile_runs() -> int:
    """The health panel's heal button for stuck runs.

    Same body the worker runs on boot, exposed on demand: the API cannot do
    this itself because stopping a leftover container needs the docker socket,
    which only the worker holds (ADR-0002).
    """
    init_db()
    return reconcile_stale_runs()


@celery_app.task(name="harness_lab_app.tasks.build_runner_image", acks_late=True)
def build_runner_image() -> dict[str, Any]:
    """The health panel's heal button for a missing runner image.

    Runs on the worker because building needs the docker socket, which only the
    worker holds (ADR-0002). The build context is the worker's own ``/app`` bind
    mount of the repo, so no host-path translation is involved. Takes minutes;
    the health panel re-checks the docker row afterwards rather than waiting.
    """
    settings = get_settings()
    if settings.execution_backend != "docker":
        return {"ok": True, "detail": "Execution backend is not docker; nothing to do."}
    try:
        import docker  # local import: only the worker needs the socket

        client = docker.from_env()
        _, logs = client.images.build(
            path=str(settings.repo_root),
            dockerfile="Dockerfile.runner",
            tag=settings.runner_image,
            rm=True,
        )
        for entry in logs:
            line = str(entry.get("stream", "")).rstrip()
            if line:
                logger.info("build_runner_image: %s", line)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("build_runner_image failed: %s", detail)
        return {"ok": False, "detail": detail}
    return {"ok": True, "detail": f"Built {settings.runner_image}"}


@control_command()
def harness_facts(_state: Any) -> dict[str, Any]:
    """Answer "is the run runtime usable?" for the health panel (ADR-0018).

    Registered as a Celery *control* command rather than a task so the API gets
    a synchronous reply from whichever worker is alive, without a result
    backend and without ever touching the docker socket itself.
    """
    settings = get_settings()
    facts: dict[str, Any] = {
        "hostname": os.environ.get("HOSTNAME", ""),
        "backend": settings.execution_backend,
        "runner_image": settings.runner_image,
        "docker_ok": False,
        "runner_image_present": False,
        "docker_version": "",
        "docker_error": "",
    }
    if settings.execution_backend != "docker":
        return facts
    try:
        import docker  # local import: only the worker needs the socket

        client = docker.from_env()
        facts["docker_version"] = client.version().get("Version", "")
        facts["docker_ok"] = True
        with contextlib.suppress(Exception):
            client.images.get(settings.runner_image)
            facts["runner_image_present"] = True
    except Exception as exc:  # the failure *is* the answer this command reports
        facts["docker_error"] = f"{type(exc).__name__}: {exc}"
    return facts


@worker_ready.connect
def _on_worker_ready(**_kwargs: Any) -> None:
    init_db()
    reconcile_stale_runs()
