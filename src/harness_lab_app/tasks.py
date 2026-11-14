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

from harness_lab_app import llm_transport, run_failures
from harness_lab_app.audit_service import AuditService
from harness_lab_app.case_profile import (
    CaseProfileError,
    agent_hidden_for,
    play_command_for,
    runner_image_for,
    sandbox_image_for,
)
from harness_lab_app.case_service import CaseService, resolve_prompt_variant_text
from harness_lab_app.celery_app import celery_app
from harness_lab_app.config import get_settings
from harness_lab_app.db import engine, init_db
from harness_lab_app.executors import (
    GOVERNANCE_MASK_ALL,
    GOVERNANCE_MASK_ANCESTORS,
    GOVERNANCE_MASK_NONE,
    build_run_environment,
    container_name_for_run,
    get_executor,
)
from harness_lab_app.models import (
    Case,
    CaseType,
    HarnessSource,
    Run,
    RunEvent,
    RunState,
    utc_now,
)
from harness_lab_app.run_service import RunService, continuation_source_dir
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.sandbox_service import SandboxService
from harness_lab_app.schemas import RunConfigCreate
from harness_lab_app.target_service import TargetError, TargetService, clone_source
from harness_lab_app.validation import describe_objective_for_judge

logger = logging.getLogger(__name__)

_AUDIT_TIMEOUT_SECONDS = 300
# `play` is a desktop that starts the case's own game on boot (ADR-0078): the
# same noVNC port, a different first window.
_SANDBOX_PORTS = {"terminal": "7681/tcp", "desktop": "6080/tcp", "play": "6080/tcp"}

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    from harness_lab_app.executors import RunExecutor, RunHandle
    from harness_lab_app.models import TargetRepository

_FAILURE_TAIL_LINES = 30


# A run's harness condition decides how much of the lab the container hides
# (ADR-0049). `lab_root` hides nothing — there the lab's harness *is* the
# treatment. The in-target pair hides what an agent could otherwise pick up from
# the workspace's ancestors, but keeps `.github/` because bench_run.py projects
# the injected harness from it. `bare` hides all of it.
GOVERNANCE_MASK_FOR_SOURCE = {
    HarnessSource.lab_root: GOVERNANCE_MASK_NONE,
    HarnessSource.native: GOVERNANCE_MASK_ANCESTORS,
    HarnessSource.injected: GOVERNANCE_MASK_ANCESTORS,
    HarnessSource.bare: GOVERNANCE_MASK_ALL,
}


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
        skills_invoke=list(config.skills_invoke),
        target_repo_id=config.target_repo_id,
        target_ref=config.target_ref,
        harness_source=config.harness_source,
    )


def _target_spec(session: Session, run: Run) -> dict[str, Any] | None:
    """The target block for ``case_spec.json``, or None for a local run.

    The runner container has no database, so everything it needs to clone and
    set up the repository is staged here by the worker (the same channel
    ``case_spec.json`` already uses for cases, ADR-0009).

    The **commit** is what is shipped, never the ref: the ref was already
    resolved at run creation (ADR-0029), and re-resolving it in the runner would
    let a run execute against a commit its own provenance does not name.
    """
    config = RunService(session).get_run_config(run.run_config_id)
    if not config.target_repo_id:
        return None
    try:
        target = TargetService(session).get_target(config.target_repo_id)
    except TargetError:
        # An archived-then-deleted target must not silently downgrade the run to
        # a local one: the case would run in a bare workspace and look fine.
        raise RuntimeError(
            f"Run {run.run_id} targets unknown repository '{config.target_repo_id}'."
        ) from None
    # The operator's own checkout when there is one (ADR-0042). Both the worker
    # and the runner container mount the lab root at /app, so a path under
    # data/checkouts resolves identically on both sides and the clone inside the
    # container never touches the network — which is what makes a private
    # repository runnable without putting a credential in a container.
    source = clone_source(target)
    return {
        "target_id": target.target_id,
        "repo_url": source,
        # Kept alongside for provenance: which repository this was, independent
        # of where its bytes were read from on the day of the run.
        "remote_url": target.repository_url,
        "ref": config.target_ref or target.default_ref,
        "commit": config.target_commit,
        "setup": list(target.setup_commands),
        # Which harness the agent will see inside the clone (ADR-0031). The
        # runner acts on this: `injected` projects the lab harness into the
        # workspace, `native` deliberately leaves the repo as it is.
        "harness_source": str(config.harness_source),
    }


def _case_prompt_text(case: Case, prompt_variant: str) -> str:
    """The prompt body for a variant, whatever form the case stores it in.

    Delegates to :func:`case_service.resolve_prompt_variant_text`, which reads
    a ``task.yaml``-style path off disk. The runner falls back to the raw
    stored value (a path string, or "") when that resolution fails, so a
    misconfigured variant degrades to an odd prompt rather than an error.
    """
    resolved = resolve_prompt_variant_text(case, prompt_variant)
    if resolved is not None:
        return resolved
    return case.prompt_variants.get(prompt_variant, "")


def _phase_prompts_for_variant(case: Case, prompt_variant: str) -> list[str]:
    """A case's later-step prompts, carrying the harness when the run's variant does.

    Each step is a fresh agent session, so the harness preamble that opens the
    ``harness`` variant's step 1 would otherwise govern only the first phase of
    a phased case and leave every later phase to the bare task (ADR-0079). The
    preamble is the same file ``make prompts`` builds that variant from, so the
    two cannot drift.
    """
    phases = list(case.step_prompts)
    if not phases or prompt_variant != "harness":
        return phases
    preamble_path = get_settings().repo_root / "benchmarks" / "_harness_preamble.md"
    if not preamble_path.is_file():
        return phases
    preamble = preamble_path.read_text()
    return [f"{preamble}\n{phase}" for phase in phases]


def _stage_harness_adapters(
    run_id: str, harness_source: object, mask: str
) -> list[str]:
    """Copy the registry's adapter files where a masked runner can read them.

    The `injected` arm projects the lab's harness into the target clone, and
    the adapter file (`CLAUDE.md`, `AGENTS.md`, …) is the document that
    projection splices the generated skills region into. That same file is
    masked to an empty stub inside the runner (ADR-0049), because the agent's
    cwd sits under the lab root and would otherwise read it from an ancestor.
    Both are right, and together they used to abort provisioning with
    "Adapter file is missing the generated-skills sentinels".

    The worker is not masked, so it stages the real bytes into the run
    directory, which no mask covers. `bench_run.inject_harness` prefers that
    directory when it exists and falls back to the repo root, so a lane that
    never masks keeps working untouched.
    """
    if (
        str(harness_source) != str(HarnessSource.injected)
        or mask == GOVERNANCE_MASK_NONE
    ):
        return []
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry

    settings = get_settings()
    staged: list[str] = []
    try:
        registry = load_registry(settings.repo_root / DEFAULT_REGISTRY_PATH)
        destination = settings.data_runs_dir / run_id / "harness_source"
        destination.mkdir(parents=True, exist_ok=True)
        for tool in registry.tools:
            relative = tool.adapter_file
            if not relative:
                continue
            source = settings.repo_root / relative
            if not source.is_file():
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged.append(relative)
    except Exception as exc:  # provisioning reports the real failure itself
        logger.warning("Could not stage harness adapters for %s: %s", run_id, exc)
    return staged


def _write_case_spec(
    session: Session,
    run_id: str,
    case_id: str,
    prompt_variant: str,
    *,
    continuation_prompt: str = "",
    parent_run_id: str | None = None,
    target: dict[str, Any] | None = None,
    harness_source: str = "lab_root",
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
            "step_prompts": _phase_prompts_for_variant(case, prompt_variant),
            "max_steps": case.max_steps,
            "constraints": list(case.constraints),
            "success_criteria": dict(case.success_criteria),
            # The starter files a bug-fix case begins from (ADR-0068).
            "starter_dir": meta.get("starter_dir", ""),
            # What the prompt names as the entry point; main.py when unset.
            "entry_point": meta.get("entry_point", ""),
        }
    if target:
        # An external target repository (ADR-0029). Carried alongside the case
        # rather than inside it: the case describes the task, the target
        # describes where it runs, and the same case must be pairable with any
        # number of repositories. `source` is the explicit provisioning
        # discriminator (ADR-0030) — the runner selects a strategy from it
        # instead of inferring one from the shape of the data.
        spec["source"] = "git_target"
        spec["target"] = target
    # At the top level, because that is where bench_run.py reads it
    # (`case_spec.get("harness_source")`), and unconditionally, because a
    # targetless run has a condition too: `bare` lives entirely without a
    # repository (ADR-0049). Staging it only inside the target block silently
    # degraded every declared non-`lab_root` run to `lab_root` — the agent ran
    # from the lab root with the lab's own governance loaded, a control arm
    # quietly turned into a second treatment arm, and the audit then judged it
    # against documents the condition said it never had.
    #
    # The target block, when there is one, is built from the same config field,
    # so the two can only disagree if a caller omitted the argument — in which
    # case the target's value is the better answer, because degrading a
    # declared condition to `lab_root` is the failure being guarded against.
    spec["harness_source"] = (
        (target or {}).get("harness_source") or "lab_root"
        if harness_source == "lab_root"
        else harness_source
    )
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
    harness: str = "",
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

    ``harness`` selects whose words those are. The markers used to be
    opencode's, applied to every run, so a ``claude`` run never once looked
    like it was waiting on a model and every quiet gateway was charged to the
    agent as a stall. A harness :mod:`harness_lab_app.run_failures` does not
    know resolves the ambiguity the safe way: unattributable silence is a lab
    failure, never an agent one.
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
        awaiting_model = run_failures.awaits_model_response(message, harness)
        if run_failures.is_model_request(message, harness):
            model_requests += 1
        if run_failures.is_stream_error(message, harness):
            stream_errors += 1
            failed_requests += 1
            last_error = message
            if stop_reason is None and 0 < max_stream_errors <= stream_errors:
                stop_reason = "endpoint"
        elif not run_failures.is_retry_noise(message, harness):
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


def _wait_bounded(
    handle: RunHandle, deadline_seconds: float, grace_seconds: float = 30.0
) -> int:
    """Join the run's process, but never for longer than the deadline.

    The watchdog decides *why* a run must end; this guarantees the worker gets
    to act on that decision. `handle.wait()` on its own is unbounded — a
    container that ignores SIGTERM, or a docker daemon that stops answering,
    parks the worker thread on a run that is already over and no verdict is
    ever written. The validation lane has had a deadline on its wait since
    ADR-0048; the agent lane did not, which is the whole of this gap.
    """
    result: list[int] = []

    def _join() -> None:
        with contextlib.suppress(Exception):
            result.append(handle.wait())

    joiner = threading.Thread(target=_join, daemon=True)
    joiner.start()
    joiner.join(timeout=max(1.0, deadline_seconds))
    if result:
        return result[0]
    # Still running past its own timeout plus a grace period: ask once more,
    # then stop waiting. `grace_seconds` is how long a stopped process is given
    # to report its real exit code; after that, -1 — the lab's "no exit code
    # was observed", which is not the same as "it exited cleanly".
    with contextlib.suppress(Exception):
        handle.stop()
    joiner.join(timeout=max(0.0, grace_seconds))
    return result[0] if result else -1


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


def _case_agent_machine(session: Session, case_id: str) -> tuple[str, list[str]]:
    """The image a case's agent runs in and the grading it must not read.

    The image is the case's own (ADR-0078) or ``""``; the hidden paths are the
    case's ``agent_hidden`` (ADR-0079), to which the executor adds the lab-wide
    calibration references. A case naming something the lab refuses fails the
    run here, before a container starts, with the reason — never a silent fall
    back, which would grade a Godot case in a runner that has no Godot, or let
    an agent read the grader it was told was hidden.
    """
    try:
        case = CaseService(session).get_case(case_id)
    except ValueError:
        return "", []
    try:
        return runner_image_for(case), agent_hidden_for(case)
    except CaseProfileError as exc:
        raise RuntimeError(f"Case {case_id}: {exc}") from exc


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
                target=_target_spec(session, run),
                harness_source=str(config_payload.harness_source),
            )
            # Pro lane only: image-based provisioning needs docker, which the
            # runner lacks — do it here before the container starts. Skipped for
            # a continuation: the workspace it would build is the one we just
            # seeded, and rebuilding it would discard the parent's work.
            if not run.parent_run_id:
                _provision_pro_workspace(session, run_id, config_payload.case_id)

            # How much of the lab's own governance the container hides
            # (ADR-0049). The keyword is passed only when there is something to
            # hide, so an unmasked run reaches the executor exactly as before.
            mask = GOVERNANCE_MASK_FOR_SOURCE.get(
                config_payload.harness_source, GOVERNANCE_MASK_NONE
            )
            # An injected arm has to read the very adapter files that mask
            # hides, so the worker — which is not masked — stages them where
            # the runner can still reach them.
            _stage_harness_adapters(run_id, config_payload.harness_source, mask)
            # The mask and the image are passed only when there is something to
            # pass; `hidden` always is, because the agent's container is the one
            # container the calibration references must never reach.
            options: dict[str, Any] = {}
            if mask != GOVERNANCE_MASK_NONE:
                options["governance_mask"] = mask
            image, hidden = _case_agent_machine(session, config_payload.case_id)
            if image:
                options["image"] = image
            # Always passed for the agent's container — even empty, the lab's
            # calibration references are masked out of it (ADR-0079).
            options["hidden"] = hidden
            handle = executor.start(run_id, command, **options)
            outcome = _stream_run(
                session,
                run_id,
                handle,
                settings.run_timeout_seconds,
                getattr(settings, "run_stall_seconds", 0),
                getattr(settings, "run_model_wait_seconds", 0),
                getattr(settings, "run_max_stream_errors", 0),
                harness=str(config_payload.harness or ""),
            )
            exit_code = _wait_bounded(handle, settings.run_timeout_seconds + 60)
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
                    _autoscore_run(session, run_id)
                    _run_step_validation(session, run_id)
            # Attribution is useful for any run that produced step logs
            # (including cancelled/failed), so index it regardless of outcome.
            _index_attribution(session, run_id)
            _index_artifacts(session, run_id)
            _check_harness_provenance(session, run_id)
            # Scored for every terminal run, not only successful ones: a run
            # that failed still spent tokens and produced a diff, and excluding
            # it would make cost aggregates describe only the runs that worked.
            _record_scores(session, run_id)
            # Every terminal run, whatever its verdict: an experiment closes on
            # its last run stopping, not on its last run succeeding.
            _close_experiment_for_run(session, run_id)
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


def _check_harness_provenance(session: Session, run_id: str) -> None:
    """Warn when metadata.json names a different harness than the run config.

    The config is what the operator asked for; metadata.json is what the runner
    actually executed. They diverged silently once — a runs.yaml cell out-voted
    the ``--harness`` flag — and the corpus carried runs filed under a CLI that
    never touched them, which is the one error a comparison cannot recover
    from, since every arm looks well-formed. Recording the mismatch on the run
    makes it visible where it matters instead of only to whoever opens the
    JSON. Best-effort: a missing or unreadable file says nothing.
    """
    try:
        run_service = RunService(session)
        run = run_service.get_run(run_id)
        config = run_service.get_run_config(run.run_config_id)
        path = get_settings().data_runs_dir / run_id / "metadata.json"
        executed = str(json.loads(path.read_text(encoding="utf-8")).get("harness", ""))
        if not executed or not config.harness or executed == config.harness:
            return
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=(
                    f"Harness provenance mismatch: configured "
                    f"'{config.harness}', executed '{executed}'. Do not pool "
                    "this run with either harness until it is resolved."
                ),
            )
        )
        session.commit()
    except Exception:  # a provenance check must never fail the run
        return


def _close_experiment_for_run(session: Session, run_id: str) -> None:
    """Complete the run's experiment once every one of its runs has stopped.

    Nothing used to close an experiment: `ExperimentStatus.complete` was in the
    enum and assigned nowhere, so `running` outlived the last run by however
    long nobody looked. Completion also queues the reading, which was manual —
    and therefore, in practice, often never done.

    Best-effort: a run's own outcome never depends on this.
    """
    from harness_lab_app.experiment_service import ExperimentService

    try:
        run = RunService(session).get_run(run_id)
        experiment_id = str(run.experiment_id or "")
        if not experiment_id:
            return
        if ExperimentService(session).try_complete(experiment_id) is None:
            return  # not this run's doing, or already closed
        session.add(
            RunEvent(
                run_id=run_id,
                message=f"Experiment {experiment_id} complete: every run has stopped",
            )
        )
        session.commit()
        # Only when there is something to call: without a transport the audit
        # would queue, fail and record an error that says nothing about the
        # experiment.
        if llm_transport.available_transport() != "none":
            from harness_lab_app.celery_app import enqueue_experiment_audit

            enqueue_experiment_audit(experiment_id, None)
    except Exception as exc:  # closing an experiment must never fail a run
        with contextlib.suppress(Exception):
            session.add(
                RunEvent(
                    run_id=run_id,
                    level="error",
                    message=f"Could not close the experiment: {exc!r}",
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


def _autoscore_run(
    session: Session,
    run_id: str,
    *,
    gateway: Callable[[str, str, str], str] | None = None,
) -> None:
    """Score a parked run automatically when its case gives a scale to use.

    Two scales exist, and which one applies is a property of the case:

    - **An objective verdict.** SWE-bench's official score is binary and fully
      automatic: resolved iff all FAIL_TO_PASS pass and no PASS_TO_PASS
      regresses — exactly what objective validation already computed. Where
      that verdict is conclusive it *is* the score (resolved → 10, else 0), and
      no model opinion is invited on top of a fact.
    - **A written rubric.** Every other case type carries the rubric its author
      wrote (``case.scoring_rubric``), and the audit LLM grades the run against
      it. Parking these in ``awaiting_manual_score`` was the lab's largest
      blind spot: an experiment can only compare what has been scored, and a
      smoke check that asks "does it start" cannot move once both arms start.
      A judged rubric score is an opinion and is recorded as one — provenance
      ``llm_judge`` — but an opinion on the record beats a blank a human never
      gets around to filling in, and a human can still overwrite it.

    Best-effort throughout: any trouble leaves the run awaiting a manual score
    and never fails it. ``gateway`` is injectable for tests.
    """
    from harness_lab_app.models import Validation

    try:
        run_service = RunService(session)
        run = run_service.get_run(run_id)
        validation = session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        previous_source = ((validation.subjective if validation else None) or {}).get(
            "score_source"
        )
        # A run waiting for its score is scored. A run already scored by the
        # judge or by an objective verdict is scored *again* when validation is
        # re-run: the checks are its evidence, so a corrected check must reach
        # the score (four tetris-v2 runs sat at 2/10 with 12/12 checks passed
        # because the judge never looked twice). A human's score is never
        # overwritten — that is a different kind of judgement.
        rescorable = run.state == RunState.completed and previous_source in (
            "llm_judge",
            "objective",
        )
        if run.state != RunState.awaiting_manual_score and not rescorable:
            return
        config = run_service.get_run_config(run.run_config_id)
        case = CaseService(session).get_case(config.case_id)
        objective = (validation.objective if validation else None) or {}
        passed = objective.get("passed")
        summary = objective.get("summary", "")
        # The judge gets the checks, not the headline: "0/1 checks passed" reads
        # the same whether the smoke command timed out or an assertion returned
        # the wrong number, and those are different facts about the agent.
        evidence = describe_objective_for_judge(objective)

        if case.type in (CaseType.swe_bench_instance, CaseType.swe_pro_instance):
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
            _llm_judge_swe_pro(session, run_service, run_id, evidence, gateway)
            return

        _llm_judge_rubric(session, run_service, run_id, case, evidence, gateway)
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
    gateway = gateway or _worker_gateway(run_id)
    model = os.environ.get("AUDITOR_MODEL") or llm_transport.default_audit_model()
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


def _rubric_text(case: Case) -> str:
    """The case's rubric, whether it is stored inline or as a repo path.

    ``scoring_rubric`` holds a path for benchmark cases synced from disk
    (``benchmarks/tetris-v2/scoring.md``) and inline markdown for cases created in
    the UI. Both are legitimate; the judge needs the text either way.
    """
    raw = (case.scoring_rubric or "").strip()
    if not raw or "\n" in raw:
        return raw
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = get_settings().repo_root / candidate
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        # A path that no longer resolves is not a rubric; better to skip
        # auto-scoring than to grade against a filename.
        return ""


def _llm_judge_rubric(
    session: Session,
    run_service: RunService,
    run_id: str,
    case: Case,
    objective_summary: str,
    gateway: Callable[[str, str, str], str] | None,
) -> None:
    """Grade a run against its case's written rubric with the audit LLM."""
    from harness_lab_app.harness_lifecycle import judge

    rubric = _rubric_text(case)
    if not rubric:
        # Recorded, not silent. Both talos cases carried an empty rubric, so
        # all four of their runs parked in `awaiting_manual_score` with no
        # trace of why — which read as a scoring backlog when it was a case
        # defect. `case_service` now refuses to create such a case; this is
        # the backstop for the ones already in the database.
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=(
                    f"Not auto-scored: case {case.case_id} has no scoring "
                    "rubric, so there is no scale to grade against. Score it "
                    "manually, or give the case a rubric and re-validate."
                ),
            )
        )
        session.commit()
        return
    run_dir = get_settings().data_runs_dir / run_id
    task = _read_run_text(run_dir / "prompt.md") or case.description
    diff = _read_run_text(run_dir / "diff.patch")
    report = _read_run_text(run_dir / "report.md")
    if not diff and not report:
        return  # nothing to grade → manual fallback
    gateway = gateway or _worker_gateway(run_id)
    model = os.environ.get("AUDITOR_MODEL") or llm_transport.default_audit_model()
    result = judge.judge_rubric_score(
        task=task,
        rubric=rubric,
        diff=diff,
        objective_summary=objective_summary,
        report=report,
        files_modified=_files_modified_for_judge(run_dir, run_id),
        gateway=gateway,
        model=model,
    )
    notes = f"LLM-judged against {case.case_id} rubric ({model}): {result['rationale']}"
    _record_autoscore(
        session, run_service, run_id, result["score"], notes.strip(), source="llm_judge"
    )


def _files_modified_for_judge(run_dir: Path, run_id: str) -> list[str]:
    """The paths the run's tool log says the agent wrote, workspace-relative.

    A second witness beside the diff: the two talos arms each created a test
    file the diff did not show, and the judge concluded from the diff alone
    that no test had been added. Relative so no local filesystem path reaches
    the model. Best-effort — a run without ``usage.json`` has no list.
    """
    try:
        usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    marker = f"/workspace/{run_id}/"
    out: list[str] = []
    for raw in usage.get("files_modified") or []:
        path = str(raw)
        if marker in path:
            path = path.split(marker, 1)[1]
        if path and path not in out:
            out.append(path)
    return out


def _read_run_text(path: Path) -> str:
    """Read a run artifact, or '' if it is missing/unreadable (best-effort)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _worker_gateway(run_id: str = "") -> Callable[[str, str, str], str]:
    """An in-process LLM caller for the worker.

    Thin adapter over :mod:`harness_lab_app.llm_transport`: the judges take an
    injectable ``(model, system, user) -> str`` callable so they stay testable
    without a network call. Raising on a missing transport or a bad response is
    the contract — the best-effort caller catches it and falls back to manual
    scoring, because an ungraded run is not a run that scored zero.

    ``run_id`` only travels to Langfuse as trace metadata: a judgement is the
    one score nobody can re-derive from the artifacts, so being able to open
    the prompt that produced it is worth the label.
    """

    def _call(model: str, system: str, user: str) -> str:
        return llm_transport.chat(
            model,
            system,
            user,
            timeout=120,
            temperature=0.2,
            trace_kind="judge",
            trace_metadata={"run_id": run_id} if run_id else None,
        )

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
                level="warn",
                message=f"Langfuse export failed: {exc!r}",
            )
        )
        session.commit()


def _record_scores(session: Session, run_id: str) -> None:
    """Score the run against the registry, right after validation.

    Scoring used to be on demand, which made the registry effectively
    write-only: nothing aggregates a metric no run has recorded, so every
    dashboard and comparison silently fell back to the older `Validation`
    fields. Running it here is what makes the score corpus exist at all.

    Best-effort, like validation and the trace export around it: a missing
    score is a gap in a chart, never a reason to fail a run that worked.
    """
    from harness_lab_app.score_service import ScoreService

    try:
        scores = ScoreService(session).evaluate_run(run_id)
        session.add(
            RunEvent(
                run_id=run_id,
                level="info",
                message=f"Recorded {len(scores)} score(s) against the registry.",
            )
        )
        session.commit()
    except Exception as exc:  # scoring must never fail the run
        session.add(
            RunEvent(
                run_id=run_id,
                level="error",
                message=f"Scoring could not run: {exc!r}",
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


def _run_step_validation(session: Session, run_id: str) -> None:
    """Grade every step's snapshot with the case's checks (ADR-0076).

    After the final validation, which installed the graders' dependencies the
    per-step pass reuses, and before scoring, which reads the trajectory.
    Best-effort like every post-run step: a run with no reward curve is a run
    with a gap in a chart, never a failed run.
    """
    from harness_lab_app.validation_service import ValidationService

    try:
        ValidationService(session).validate_steps(run_id)
    except Exception as exc:  # the step reward must never fail the run
        session.rollback()
        session.add(
            RunEvent(
                run_id=run_id,
                level="warning",
                message=f"Per-step reward could not be computed: {exc!r}",
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
        _autoscore_run(session, run_id)
        # The per-step reward re-grades the snapshots too, so a re-validate
        # after a grader fix refreshes the curve along with the verdict.
        _run_step_validation(session, run_id)
        # Scores come after validation because they read its verdict, cost and
        # effort; re-scoring overwrites in place, so a re-validate refreshes
        # them rather than accumulating contradictions.
        _record_scores(session, run_id)
        # A re-validate is often what finally scores the run an experiment was
        # waiting on, so this is the second place an experiment can close.
        _close_experiment_for_run(session, run_id)
        # Re-export so the validation verdict and the new score reach the trace
        # too; deterministic ids mean this updates rather than duplicates.
        _export_langfuse_traces(session, run_id)


@celery_app.task(name="harness_lab_app.tasks.gate_check_target", acks_late=True)
def gate_check_target(gate_check_id: str) -> None:
    """Run a repository's own gate at its pinned commit, with no agent.

    The control measurement of an audit (ADR-0042): it separates "the agent
    failed" from "this repository does not build in the runner image", which
    are indistinguishable in a failed run and mean opposite things.

    Stops at the first failing command on purpose — a test suite run in a
    workspace whose setup failed measures the setup.
    """
    from harness_lab_app.gate_check_service import build_gate_check_spec, summarize
    from harness_lab_app.models import GateCheck

    init_db()
    with Session(engine) as session:
        row = session.exec(
            select(GateCheck).where(GateCheck.gate_check_id == gate_check_id)
        ).first()
        if row is None:
            return
        try:
            target = TargetService(session).get_target(row.target_id)
            spec = build_gate_check_spec(target)
        except Exception as exc:
            row.status = "failed"
            row.error = str(exc)
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            return

        results = _run_spec_commands(get_executor(), gate_check_id, spec["commands"])
        verdict = summarize(results)
        row.status = "done"
        row.passed = verdict["passed"]
        row.failed_phase = verdict["failed_phase"]
        row.checks_json = verdict["checks"]
        row.updated_at = utc_now()
        session.add(row)
        session.commit()


def _run_spec_commands(
    executor: RunExecutor,
    prefix: str,
    commands: list[dict[str, Any]],
    *,
    stop_on_failed_check: bool = True,
) -> list[dict[str, Any]]:
    """Run a provision/setup/validate spec, stopping at the first failure.

    Shared by the gate check and the case dry run: both ask "what does this
    repository do with no agent involved?", and a check run in a workspace
    whose setup failed measures the setup — so provisioning and setup always
    stop the sequence.

    The two differ on a failed *check*. The gate check stops, because after one
    red gate the rest is not interesting. A dry run keeps going, because its
    question is whether **any** of the case's checks can fail on the clean
    tree, and stopping at the first answer would leave the others unasked.
    """
    results: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        try:
            # Untrusted repository code with no agent and no model: the
            # hardened lane, so no lab secrets and no network past provisioning.
            handle = executor.start(
                f"{prefix}-{index}",
                command["argv"],
                hardened=True,
                phase=command["phase"],
            )
            output = "\n".join(message for _level, message in handle.iter_lines())
            exit_code = handle.wait()
        except Exception as exc:  # an executor failure is a result, not a crash
            results.append(
                {
                    "phase": command["phase"],
                    "command": command["label"],
                    "exit_code": -1,
                    "output": f"executor error: {exc!r}",
                }
            )
            break
        results.append(
            {
                "phase": command["phase"],
                "command": command["label"],
                "exit_code": exit_code,
                "output": output,
            }
        )
        if exit_code != 0 and (stop_on_failed_check or command["phase"] != "validate"):
            break
    return results


def _dry_run_case_body(
    session: Session, case_id: str, executor: RunExecutor
) -> dict[str, Any]:
    """Run a case's own checks against its target's clean, pinned tree.

    The empirical half of case formation: a check that passes here passed
    before the agent started, so its green says nothing about the agent. Two
    talos cases were exactly that, and both reported numbers about themselves.

    Stored on the case rather than in a table of its own — there is no Alembic
    here, and a verdict about a case belongs to that case.
    """
    from harness_lab_app.gate_check_service import (
        GateCheckError,
        build_case_dry_run_spec,
        dry_run_verdict,
        workspace_for,
    )

    case = CaseService(session).get_case(case_id)
    target_id = (case.origin_target_id or case.subject_repo or "").strip()
    record: dict[str, Any] = {
        "at": utc_now().isoformat(),
        "case_id": case_id,
        "target_id": target_id,
    }
    try:
        if not target_id:
            raise GateCheckError(
                "This case names no repository, so there is no clean tree to "
                "run its checks against."
            )
        target = TargetService(session).get_target(target_id)
        spec = build_case_dry_run_spec(
            case, target, f"{workspace_for(target_id)}-dry-{case_id}"
        )
    except Exception as exc:
        record.update(verdict="inconclusive", error=str(exc), checks=[])
        _store_dry_run(session, case_id, record)
        return record

    results = _run_spec_commands(
        executor, f"dry-{case_id}", spec["commands"], stop_on_failed_check=False
    )
    record.update(commit=spec["commit"], **dry_run_verdict(results))
    _store_dry_run(session, case_id, record)
    return record


def _store_dry_run(session: Session, case_id: str, record: dict[str, Any]) -> None:
    """Persist a dry-run verdict on the case, as JSON in `metadata_json`."""
    case = CaseService(session).get_case(case_id)
    metadata = dict(case.metadata_json or {})
    metadata["dry_run"] = json.dumps(record, default=str)
    case.metadata_json = metadata
    case.updated_at = utc_now()
    session.add(case)
    session.commit()


@celery_app.task(name="harness_lab_app.tasks.dry_run_case", acks_late=True)
def dry_run_case(case_id: str) -> None:
    """Run one case's checks against the clean tree (worker-side: needs docker)."""
    init_db()
    with Session(engine) as session:
        _dry_run_case_body(session, case_id, get_executor())


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
                    run_id,
                    markdown,
                    findings=service.read_audit_findings(run_id),
                    governance=service.read_audit_governance(run_id),
                )
            else:
                service.store_error(run_id, "Audit finished but wrote no audit.md")
        else:
            tail = (result.stderr or result.stdout or "").strip()[-1500:]
            service.store_error(run_id, tail or "Audit failed (no output)")


def _audit_experiment_body(
    session: Session,
    experiment_id: str,
    auditor_model: str | None = None,
    gateway: Callable[[str, str, str], str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Read a finished experiment back as prose; store text and evidence.

    Separated from the Celery task so a test can inject the gateway (and the
    retry's sleep). Failure is recorded on the row, never raised: an experiment
    without a reading is still an experiment, and the page falls back to the
    cards.
    """
    from harness_lab_app.experiment_audit_service import (
        EXPERIMENT_AUDIT_SYSTEM_PROMPT,
        ExperimentAuditService,
        build_evidence,
        call_with_retry,
        render_audit_prompt,
    )

    service = ExperimentAuditService(session)
    model = (
        auditor_model
        or os.environ.get("AUDITOR_MODEL")
        or (llm_transport.default_audit_model())
    )
    service.mark_running(experiment_id, model)
    try:
        evidence = build_evidence(session, experiment_id)
        call = gateway or (
            lambda m, sys_, user: llm_transport.chat(
                m,
                sys_,
                user,
                timeout=_AUDIT_TIMEOUT_SECONDS,
                temperature=0.2,
                trace_kind="experiment_audit",
                trace_metadata={"experiment_id": experiment_id},
            )
        )
        prompt = render_audit_prompt(evidence)
        # Retried, because a transport error is a fact about the network and
        # not about the experiment. The `audit-talos` reading died on one and
        # was never asked again.
        attempt = lambda: call(  # noqa: E731 - one expression, named for retry
            model, EXPERIMENT_AUDIT_SYSTEM_PROMPT, prompt
        )
        markdown = (
            call_with_retry(attempt, sleep=sleep)
            if sleep is not None
            else call_with_retry(attempt)
        ).strip()
        if not markdown:
            raise RuntimeError("the auditor returned an empty reading")
        service.store_result(experiment_id, markdown, evidence, model)
    except Exception as exc:  # the reading is optional; the record says why
        service.store_error(experiment_id, f"{type(exc).__name__}: {exc}")


@celery_app.task(name="harness_lab_app.tasks.audit_experiment", acks_late=True)
def audit_experiment(experiment_id: str, auditor_model: str | None = None) -> None:
    """The LLM reading of a finished experiment (worker-side, one gateway call)."""
    init_db()
    with Session(engine) as session:
        _audit_experiment_body(session, experiment_id, auditor_model)


def _audit_comparison_body(
    session: Session,
    run_ids: list[str],
    auditor_model: str | None = None,
    gateway: Callable[[str, str, str], str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Read an ad-hoc comparison back as prose; store text and evidence.

    Separated from the Celery task so a test can inject the gateway (and the
    retry's sleep). Failure is recorded on the row, never raised: a comparison
    without a reading is still a comparison, and the page falls back to the
    table.
    """
    from harness_lab_app.compare_audit_service import (
        COMPARE_AUDIT_SYSTEM_PROMPT,
        CompareAuditService,
        build_evidence,
        comparison_key,
        render_audit_prompt,
    )
    from harness_lab_app.experiment_audit_service import call_with_retry

    service = CompareAuditService(session)
    key = comparison_key(run_ids)
    model = (
        auditor_model
        or os.environ.get("AUDITOR_MODEL")
        or (llm_transport.default_audit_model())
    )
    service.mark_running(key, run_ids, model)
    try:
        evidence = build_evidence(session, run_ids)
        call = gateway or (
            lambda m, sys_, user: llm_transport.chat(
                m,
                sys_,
                user,
                timeout=_AUDIT_TIMEOUT_SECONDS,
                temperature=0.2,
                trace_kind="compare_audit",
                trace_metadata={"comparison_key": key},
            )
        )
        prompt = render_audit_prompt(evidence)
        attempt = lambda: call(  # noqa: E731 - one expression, named for retry
            model, COMPARE_AUDIT_SYSTEM_PROMPT, prompt
        )
        markdown = (
            call_with_retry(attempt, sleep=sleep)
            if sleep is not None
            else call_with_retry(attempt)
        ).strip()
        if not markdown:
            raise RuntimeError("the auditor returned an empty reading")
        service.store_result(key, run_ids, markdown, evidence, model)
    except Exception as exc:  # the reading is optional; the record says why
        service.store_error(key, run_ids, f"{type(exc).__name__}: {exc}")


@celery_app.task(name="harness_lab_app.tasks.audit_comparison", acks_late=True)
def audit_comparison(run_ids: list[str], auditor_model: str | None = None) -> None:
    """The LLM reading of a hand-picked run comparison (worker-side, one call)."""
    init_db()
    with Session(engine) as session:
        _audit_comparison_body(session, run_ids, auditor_model)


def _audit_repository_body(
    session: Session,
    target_id: str,
    auditor_model: str | None = None,
    gateway: Callable[[str, str, str], str] | None = None,
    health: dict[str, Any] | None = None,
) -> None:
    """Read a pinned repository's harness and store the auditor's reading.

    Two model passes with the lab's facts between them (ADR-0058): the first
    maps every harness component to what it claims; the lab annotates which
    are evaluable here and which counterparts exist; the second theorises
    cases from that map, and the lab derives the experiment arms.

    Separated from the Celery task so a test can inject the gateway and the
    health verdict. Failure is recorded on the row, never raised: a
    repository without a reading is still a repository, and the wizard says
    the audit failed instead of hanging on "running".
    """
    from harness_lab_app.audit_contract import (
        annotate_cases,
        annotate_map,
        parse_case_proposals,
        parse_component_map,
    )
    from harness_lab_app.repository_audit_service import (
        CASE_PROPOSALS_PROMPT,
        COMPONENT_MAP_PROMPT,
        RepositoryAuditService,
        apply_case_plan,
        evidence_chars,
        gather_evidence,
        plan_cases,
        render_audit_prompt,
        render_case_prompt,
        render_markdown,
    )
    from harness_lab_app.runner_capabilities import (
        capabilities_from_dicts,
        runner_capabilities,
        service_capabilities,
    )

    service = RepositoryAuditService(session)
    model = (
        auditor_model
        or os.environ.get("AUDITOR_MODEL")
        or (llm_transport.default_audit_model())
    )
    service.mark_running(target_id, model)
    try:
        target = TargetService(session).get_target(target_id)
        health_verdict = health if health is not None else _health_at_pin(target)
        evidence = gather_evidence(target, health_at_pin=health_verdict)
        _log_audit_event(
            session,
            target_id,
            f"Evidence: {evidence_chars(evidence)} chars; suite at pin: "
            f"{evidence.get('health_at_pin', {}).get('status', 'unmeasured')}",
        )

        # What actually served each pass. The Claude CLI ignores the requested
        # temperature and answers a model name it cannot serve with its own
        # default, so the request alone is not a record of the instrument.
        served: list[llm_transport.ChatReply] = []

        def call(m: str, sys_: str, user: str, *, pass_number: int) -> str:
            if gateway is not None:
                return gateway(m, sys_, user)
            reply = llm_transport.chat_reply(
                m,
                sys_,
                user,
                timeout=_AUDIT_TIMEOUT_SECONDS,
                temperature=0.2,
                trace_kind="repository_audit",
                trace_metadata={
                    "target_id": target_id,
                    "commit": evidence["commit"],
                    "pass": pass_number,
                },
            )
            served.append(reply)
            return reply.text

        raw_map = call(
            model, COMPONENT_MAP_PROMPT, render_audit_prompt(evidence), pass_number=1
        )
        if not (raw_map or "").strip():
            raise RuntimeError("the auditor returned an empty reading")
        findings = parse_component_map(raw_map)
        raw = raw_map
        services = service_capabilities()
        evidence["services"] = services
        if "parse_error" not in findings:
            findings = annotate_map(
                findings,
                lab_skill_names=[
                    row["name"]
                    for row in evidence.get("lab_harness", {}).get("skills", [])
                ],
                services=services,
                installer_paths=list(evidence.get("installers") or {}),
            )
            raw_cases = call(
                model,
                CASE_PROPOSALS_PROMPT,
                render_case_prompt(evidence, findings),
                pass_number=2,
            )
            raw = f"{raw_map}\n\n---\n\n{raw_cases}"
            proposals = parse_case_proposals(raw_cases)
            if "parse_error" in proposals:
                findings["cases"] = []
                findings["warnings"].append(f"cases: {proposals['parse_error']}")
            else:
                proposals = annotate_cases(proposals, findings)
                findings["cases"] = proposals["cases"]
                findings["warnings"].extend(proposals.get("warnings") or [])
        # The kept cases, matched against the runner as every case is: the
        # capability match decides whether the hint can be settled, per case.
        capabilities = (
            capabilities_from_dicts(evidence.get("runtime", {}).get("capabilities"))
            or runner_capabilities()
        )
        kept = [
            {
                "title": case["title"],
                "why": case.get("observable", ""),
                "validation_hint": case.get("validation_hint", ""),
                "required_runtime_version": case.get("required_runtime_version", ""),
            }
            for case in findings.get("cases") or []
            if not case.get("dropped_reason")
        ]
        plan = plan_cases(kept, capabilities)
        evidence = apply_case_plan(evidence, plan)
        _log_case_plan(session, target_id, evidence, plan)
        recorded_model = _served_model(model, served)
        if served:
            evidence["llm"] = [reply.provenance() for reply in served]
        markdown = render_markdown(findings, evidence, model=recorded_model, raw=raw)
        service.store_result(
            target_id,
            markdown=markdown,
            findings=findings,
            evidence=evidence,
            model=recorded_model,
        )
    except Exception as exc:  # the reading is optional; the record says why
        service.store_error(target_id, f"{type(exc).__name__}: {exc}")


def _served_model(requested: str, served: list[llm_transport.ChatReply]) -> str:
    """The model to file an audit under: what answered, not what was asked.

    The requested name when nothing else is known (an injected gateway in a
    test) or when it is what answered. When the transport substituted another
    model, the record names it and keeps the request beside it, so an audit
    filed as `gpt-5.4-mini` cannot turn out to have been read by Sonnet. When
    a pass reached a model that reported nothing (a CLI too old to report
    usage, sent a name it cannot serve), that is said too: the request is the
    one model it certainly was not.
    """
    if not served:
        return requested
    effective = sorted({reply.model for reply in served if reply.model})
    if any(not reply.model for reply in served):
        effective.append("unknown")
    if effective == [requested]:
        return requested
    return f"{', '.join(effective)} (requested {requested})"


def _health_at_pin(target: TargetRepository) -> dict[str, Any]:
    """The repository's own suite at the pin, through the hardened lane.

    Never raises: an audit whose health check could not run is an audit with
    ``unmeasured`` health, which is a fact about the lab, not a reason to
    have no reading at all.
    """
    from harness_lab_app.gate_check_service import build_health_spec, health_at_pin

    try:
        spec = build_health_spec(target)
        if not spec["commands"]:
            return health_at_pin([], measured=False)
        results = _run_spec_commands(
            get_executor(), f"audit-health-{target.target_id}", spec["commands"]
        )
        return health_at_pin(results, measured=True)
    except Exception as exc:
        return {
            "status": "unmeasured",
            "reason": f"health check could not run: {type(exc).__name__}: {exc}",
            "checks": [],
        }


def _log_audit_event(session: Session, target_id: str, message: str) -> None:
    from harness_lab_app.models import RunEvent

    try:
        session.add(
            RunEvent(run_id=f"audit-{target_id}", level="info", message=message[:500])
        )
        session.commit()
    except Exception:  # observability never breaks the thing it observes
        session.rollback()


def _log_case_plan(
    session: Session, target_id: str, evidence: dict, plan: dict
) -> None:
    """Record the match as run events: what the runner had, what each case needed.

    Observability here is the same shape it is everywhere else in this repo —
    `RunEvent` rows and Langfuse traces rebuilt from artifacts, not a metrics
    layer this project does not have. Never raises: a missing log line must not
    cost the audit its reading.
    """
    from harness_lab_app.models import RunEvent

    try:
        runtime = evidence.get("runtime") or {}
        offered = ", ".join(
            f"{item.get('runtime')}{' ' + str(item.get('version')) if item.get('version') else ''}"
            for item in runtime.get("capabilities") or []
            if item.get("available")
        )
        session.add(
            RunEvent(
                run_id=f"audit-{target_id}",
                level="info",
                message=(
                    f"Runner capabilities: {offered or 'unknown'}; "
                    f"execution coverage {plan['coverage']} "
                    f"({plan['runnable']} runnable, {plan['static']} static, "
                    f"{plan['blocked']} blocked)"
                ),
            )
        )
        for case in plan.get("cases") or []:
            session.add(
                RunEvent(
                    run_id=f"audit-{target_id}",
                    level="warning" if case["status"] == "blocked" else "info",
                    message=(
                        f"Case [{case['status']}] runtime="
                        f"{case['runtime'] or 'none'} "
                        f"version={case['runtime_version'] or 'unknown'} "
                        f"required={case['required_runtime_version'] or 'any'} "
                        f"compatibility={case['compatibility']}: {case['title']}"
                    ),
                )
            )
        session.commit()
    except Exception:  # observability never breaks the thing it observes
        session.rollback()


@celery_app.task(name="harness_lab_app.tasks.audit_repository", acks_late=True)
def audit_repository(target_id: str, auditor_model: str | None = None) -> None:
    """The LLM harness audit of a pinned repository (worker-side, one call)."""
    init_db()
    with Session(engine) as session:
        _audit_repository_body(session, target_id, auditor_model)


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


def _sandbox_profile(run_id: str) -> tuple[str, str]:
    """The run's case's sandbox image and play command (ADR-0078).

    Read from the database by the run's config, so the same run always opens
    in the image its case names now. Empty strings mean the shared image and
    no game to launch.
    """
    with Session(engine) as session:
        service = RunService(session)
        try:
            config = service.get_run_config(service.get_run(run_id).run_config_id)
            case = CaseService(session).get_case(config.case_id)
        except ValueError:
            return "", ""
        return sandbox_image_for(case), play_command_for(case)


def _launch_sandbox(client: Any, run_id: str, kind: str) -> tuple[str, int, str]:
    """Copy the run's workspace and launch a sandbox container (worker-side).

    Returns (container_name, host_port, url). The bind mount uses the HOST path
    (docker-out-of-docker), and the port is docker-assigned then read back.
    """
    settings = get_settings()
    image, play_command = _sandbox_profile(run_id)
    image = image or settings.sandbox_image
    if kind == "play" and not play_command:
        raise RuntimeError(
            "This case declares no play_command, so there is no game to launch."
        )
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
    environment = {"MODE": "desktop" if kind == "play" else kind}
    if kind == "play":
        # Launched by the entrypoint once the desktop is up (sandbox_entrypoint.sh).
        environment["PLAY_COMMAND"] = play_command
    container = client.containers.run(
        image=image,
        detach=True,
        name=name,
        environment=environment,
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

    path = (
        "/vnc.html?autoconnect=1&resize=remote" if kind in ("desktop", "play") else "/"
    )
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
            image = _sandbox_profile(run_id)[0] or settings.sandbox_image
        except CaseProfileError as exc:
            service.mark_error(run_id, kind, str(exc))
            return
        try:
            client.images.get(image)
        except Exception:
            build = (
                "`make sandbox-build`"
                if image == settings.sandbox_image
                else "`make images-3d` (this case has its own image)"
            )
            service.mark_error(
                run_id, kind, f"Sandbox image {image} not found. Build it with {build}."
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
    for kind in ("terminal", "desktop", "play"):
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
        # From the image's label, so no container is started to ask (ADR-0070
        # erratum: an unpinned CLI install stayed on a cached old version).
        "claude_cli_version": "",
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
            image = client.images.get(settings.runner_image)
            facts["runner_image_present"] = True
            labels = (image.labels or {}) if hasattr(image, "labels") else {}
            facts["claude_cli_version"] = str(labels.get("claude-code.version", ""))
    except Exception as exc:  # the failure *is* the answer this command reports
        facts["docker_error"] = f"{type(exc).__name__}: {exc}"
    return facts


@worker_ready.connect
def _on_worker_ready(**_kwargs: Any) -> None:
    init_db()
    reconcile_stale_runs()
