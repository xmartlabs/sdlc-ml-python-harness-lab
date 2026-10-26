"""Orchestrates objective validation and multidimensional scoring."""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.case_service import CaseService
from harness_lab_app.config import get_settings
from harness_lab_app.executors import SubprocessRunExecutor, get_executor
from harness_lab_app.models import Run, RunEvent, Validation, utc_now
from harness_lab_app.run_service import RunService
from harness_lab_app.target_service import TargetError, TargetService
from harness_lab_app.validation import (
    build_validation_spec,
    collect_cost,
    collect_effort,
    command_deadline_seconds,
    compute_composite,
    parse_objective,
    workspace_modules,
)

if TYPE_CHECKING:
    from harness_lab_app.executors import RunExecutor


# How much of one validation command's output is kept. Two mebibytes is far
# more than any real suite's summary and far less than a runaway loop's.
_OUTPUT_CAP_BYTES = 2 * 1024 * 1024


class ValidationService:
    def __init__(self, session: Session, executor: RunExecutor | None = None):
        self.session = session
        self._executor = executor

    def _executor_instance(self) -> RunExecutor:
        return self._executor if self._executor is not None else get_executor()

    def get_validation(self, run_id: str) -> Validation | None:
        return self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()

    def _upsert(self, run_id: str) -> Validation:
        validation = self.get_validation(run_id)
        if validation is None:
            validation = Validation(run_id=run_id)
            self.session.add(validation)
        return validation

    def _chain_depth(self, run_id: str) -> int:
        """How many continuations led to this run (ADR-0026)."""
        from harness_lab_app.leaderboard_service import chain_depth

        parents = {
            row.run_id: row.parent_run_id for row in self.session.exec(select(Run))
        }
        return chain_depth(run_id, parents)

    def _run_dir(self, run_id: str):
        return get_settings().data_runs_dir / run_id

    def _run_objective(self, spec: dict[str, Any], run_id: str) -> dict[str, Any]:
        # "local" specs (SWE-bench Pro) shell out to docker themselves; run them
        # in this process — the worker has the socket, validation containers
        # deliberately do not.
        executor: RunExecutor = (
            SubprocessRunExecutor() if spec.get("local") else self._executor_instance()
        )
        # A "local" spec runs docker itself, from this process, against the
        # worker's own socket — hardening it would strip the very environment
        # that lets it reach the daemon. Everything else is an audited
        # repository's own command and takes the hardened lane.
        hardened = not spec.get("local")
        results: list[dict[str, Any]] = []
        # The graders' dependencies, installed with network before the offline
        # checks (ADR-0071). A grader that could not be equipped measured
        # nothing: its checks are recorded inconclusive without running.
        for command in spec.get("setup") or []:
            try:
                handle = executor.start(
                    f"{run_id}-setup", command, hardened=hardened, phase="setup"
                )
                output, exit_code = self._drain(handle, "validation setup")
            except Exception as exc:
                output, exit_code = f"executor error: {exc!r}", -1
            if exit_code != 0:
                self.session.add(
                    RunEvent(
                        run_id=run_id,
                        level="warning",
                        message="Validation setup could not install the graders' "
                        f"dependencies (exit {exit_code}): {output[-400:]}",
                    )
                )
                return parse_objective(
                    [
                        {
                            "command": argv[-1] if argv else "",
                            "exit_code": 1,
                            "output": "validation setup failed; check not run",
                            "inconclusive_reason": "missing_dependency",
                        }
                        for argv in spec["commands"]
                    ],
                    spec["strategy"],
                )
        # What the agent wrote: a missing one of these is its failure, never a
        # missing grader dependency. The archived copy when there is one, the
        # live workspace otherwise — the same choice the spec made.
        archived = self._run_dir(run_id) / "workspace"
        # data/runs/<id> → the lab root is three levels up.
        live = self._run_dir(run_id).parents[2] / "workspace" / run_id
        own_modules = workspace_modules(archived if archived.exists() else live)
        for command in spec["commands"]:
            label = command[-1] if command else ""
            try:
                handle = executor.start(
                    f"{run_id}-validate",
                    command,
                    hardened=hardened,
                    phase="validate",
                )
                output, exit_code = self._drain(handle, label)
            except Exception as exc:  # record the failure, never crash the run
                results.append(
                    {
                        "command": label,
                        "exit_code": -1,
                        "output": f"executor error: {exc!r}",
                    }
                )
                continue
            results.append(
                {
                    "command": label,
                    "exit_code": exit_code,
                    "output": output,
                    "own_modules": own_modules,
                }
            )
        return parse_objective(results, spec["strategy"])

    def _drain(self, handle: Any, label: str) -> tuple[str, int]:
        """Collect a validation command's output under a hard deadline.

        Neither ``iter_lines()`` nor ``wait()`` has a deadline of its own, so a
        command that never returns blocked this loop forever — the state the
        fourth run of experiment `...-on--2` sat in until it was killed by hand.
        A watchdog calls ``handle.stop()`` (SIGTERM for the subprocess backend,
        ``container.stop`` for docker, which escalates to SIGKILL on its own),
        which is what lets the loop finish and record an exit code rather than
        hang. The command's own ``timeout -k`` prefix remains the first line of
        defence; this is the one that fires when that prefix cannot help.
        """
        expired = threading.Event()

        def _kill() -> None:
            expired.set()
            with contextlib.suppress(Exception):
                handle.stop()

        watchdog = threading.Timer(command_deadline_seconds(label), _kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            output = self._capped(handle)
            exit_code = handle.wait()
        finally:
            watchdog.cancel()
        if expired.is_set():
            # Whatever the terminated process reported on the way out, the
            # check settled nothing — say so with a code parse_objective reads
            # as inconclusive rather than as a failed assertion.
            return (
                f"{output}\nvalidation deadline exceeded; command terminated".strip(),
                124,
            )
        return output, exit_code

    def _capped(self, handle: Any) -> str:
        """Read a command's output, keeping the head and dropping the rest.

        An audited repository's suite can print without limit, and the whole
        string is stored on the validation row and shown to a judge. The head
        is what is worth keeping — a failure states itself early — but the
        stream is drained to the end regardless: stopping early would leave the
        container blocked on a full pipe and `wait()` would never return.
        """
        chunks: list[str] = []
        size = 0
        truncated = False
        for _level, message in handle.iter_lines():
            if size >= _OUTPUT_CAP_BYTES:
                truncated = True
                continue
            chunks.append(message)
            size += len(message) + 1
        output = "\n".join(chunks)
        if truncated:
            output += (
                f"\n[output truncated at {_OUTPUT_CAP_BYTES} bytes; "
                "the command kept printing]"
            )
        return output

    def validate_run(self, run_id: str) -> Validation:
        """Run objective validation and record cost + effort (subjective stays)."""
        services = RunService(self.session)
        run = services.get_run(run_id)
        config = services.get_run_config(run.run_config_id)
        case = CaseService(self.session).get_case(config.case_id)

        # A run against an external target is validated by that repository's
        # own gates, not by the case's (ADR-0029).
        target_commands: list[str] | None = None
        if config.target_repo_id:
            with contextlib.suppress(TargetError):
                target = TargetService(self.session).get_target(config.target_repo_id)
                target_commands = list(target.validation_commands) or None

        spec = build_validation_spec(case, run_id, target_commands)
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info",
                message=f"Objective validation ({spec['strategy']}): "
                f"{len(spec['commands'])} command(s)",
            )
        )
        objective = self._run_objective(spec, run_id)
        cost = collect_cost(self._run_dir(run_id))
        effort = collect_effort(
            self._run_dir(run_id), run.interventions, self._chain_depth(run_id)
        )

        validation = self._upsert(run_id)
        validation.objective = objective
        validation.cost = cost
        validation.effort = effort
        validation.composite = compute_composite(objective, validation.subjective)
        validation.updated_at = utc_now()
        self.session.add(validation)
        # Three outcomes, three levels: a failure is an error, a check that
        # settled nothing is a warning about the lab rather than the agent.
        verdict = objective.get("passed")
        level = "error" if verdict is False else "info"
        if verdict is None and objective.get("checks"):
            level = "warning"
        self.session.add(
            RunEvent(
                run_id=run_id,
                level=level,
                message=f"Objective validation: {objective.get('summary', 'done')}",
            )
        )
        self.session.commit()
        self.session.refresh(validation)
        return validation

    def apply_subjective(
        self,
        run_id: str,
        functional_score: int,
        interventions: int,
        rubric: dict[str, int] | None = None,
        notes: str = "",
        *,
        score_source: str = "manual",
    ) -> Validation:
        """Record the subjective score and recompute the composite.

        ``score_source`` records how the score was produced — ``manual`` (a human
        entered it), ``objective`` (derived from the automated test verdict), or
        ``llm_judge`` (an LLM graded the run) — so the UI can label it.
        """
        validation = self._upsert(run_id)
        validation.subjective = {
            "functional_score": functional_score,
            "rubric": rubric or {},
            "notes": notes,
            "score_source": score_source,
        }
        # Refresh effort with the confirmed intervention count.
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is not None:
            validation.effort = collect_effort(
                self._run_dir(run_id), interventions, self._chain_depth(run_id)
            )
        validation.composite = compute_composite(
            validation.objective, validation.subjective
        )
        validation.updated_at = utc_now()
        self.session.add(validation)
        self.session.commit()
        self.session.refresh(validation)
        return validation
