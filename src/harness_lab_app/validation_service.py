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
        # The per-step trajectory is computed by `validate_steps` and lives on
        # the same JSON; a re-validate of the final state must not erase it.
        previous_steps = (validation.objective or {}).get("per_step")
        if previous_steps is not None:
            objective = {**objective, "per_step": previous_steps}
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

    def _step_costs(self, run_id: str, steps: list[int]) -> dict[int, dict[str, Any]]:
        """Each step's own cost: claude's per-step ``result`` event when present."""
        from harness_lab_app.step_reward import claude_step_cost, step_record_files

        records = step_record_files(self._run_dir(run_id))
        costs: dict[int, dict[str, Any]] = {}
        for step in steps:
            events = records.get(step, {}).get("events")
            if events is not None:
                cost = claude_step_cost(events.read_text(errors="replace").splitlines())
                if cost:
                    costs[step] = cost
        return costs

    def _grade_step_tree(
        self,
        run_id: str,
        step: int,
        step_meta: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any] | None:
        """One SWE-bench Pro step's F2P/P2P verdict, graded in the worker.

        Rebuilds the tree the step left (its diff on the commit it was cut
        from, in a worktree of the archived clone) and runs the case's own
        command pointed at it. None when the tree cannot be rebuilt or the
        command printed no verdict — the step is then unmeasured, never
        credited with the final state.
        """
        from harness_lab_app.step_reward import parse_swe_verdict
        from harness_lab_app.step_trees import StepTreeError, step_tree

        run_dir = self._run_dir(run_id)
        diff = run_dir / "steps" / str(step_meta.get("path") or f"step-{step}.diff")
        if not diff.is_file():
            return None
        outputs: list[str] = []
        try:
            with step_tree(
                run_dir / "workspace", str(step_meta["base"]), diff, run_dir, step
            ) as tree:
                # Absolute: the validator resolves it against its own cwd.
                for argv in spec["commands"]:
                    command = [*argv[:-1], f"{argv[-1]} --repo-dir {tree.resolve()}"]
                    # The Pro suite shells out to docker itself; like the final
                    # validation it runs here, where the socket is.
                    handle = SubprocessRunExecutor().start(
                        f"{run_id}-step-{step}", command, hardened=False
                    )
                    output, _exit_code = self._drain(handle, command[-1])
                    outputs.append(output)
        except (StepTreeError, ValueError, OSError) as exc:
            self._step_not_graded(run_id, step, str(exc))
            return None
        for output in outputs:
            # Read the whole output, not the check's one-line summary: a suite
            # that never ran still prints `F2P=0/n` as a placeholder, and only
            # the error line beside it says that nothing was measured.
            if "SWEBENCH-ERROR:" in output:
                reason = output.split("SWEBENCH-ERROR:", 1)[1].strip().splitlines()[0]
                self._step_not_graded(run_id, step, f"the suite did not run: {reason}")
                return None
            verdict = parse_swe_verdict(output)
            if verdict is not None:
                return verdict
        self._step_not_graded(run_id, step, "the validator printed no verdict")
        return None

    def _step_not_graded(self, run_id: str, step: int, reason: str) -> None:
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="warning",
                message=f"Per-step reward: step {step} not graded — {reason}",
            )
        )

    def validate_steps(self, run_id: str) -> dict[str, Any] | None:
        """Grade every step's snapshot with the case's own checks (ADR-0076).

        The per-step reward: the same spec, the same executor and the same
        parser as the final validation (ADR-0075), pointed at
        ``steps/step-N.snapshot`` instead of the final workspace. Run after the
        agent has finished, so the agent never sees a reward it could steer by.

        The SWE lane needs no re-run: the runner already measured F2P/P2P live
        after every step and kept it in ``step-N.meta.json``. A step recorded
        as a diff (a cloned target repository) is listed but not graded — its
        checks run inside the repository and replaying a diff per step onto a
        fresh clone is not done yet; the gap shows as an unmeasured step.

        Stored on ``Validation.objective["per_step"]`` and returned; None when
        the run left no per-step record at all.
        """
        from harness_lab_app.attribution_service import AttributionService
        from harness_lab_app.step_reward import (
            build_trajectory,
            progress_of,
            read_step_meta,
            snapshot_dirs,
            swe_progress,
        )

        run_dir = self._run_dir(run_id)
        meta = read_step_meta(run_dir)
        snapshots = dict(snapshot_dirs(run_dir))
        if not meta and not snapshots:
            return None

        services = RunService(self.session)
        run = services.get_run(run_id)
        config = services.get_run_config(run.run_config_id)
        case = CaseService(self.session).get_case(config.case_id)
        target_commands: list[str] | None = None
        if config.target_repo_id:
            with contextlib.suppress(TargetError):
                target = TargetService(self.session).get_target(config.target_repo_id)
                target_commands = list(target.validation_commands) or None
        spec = build_validation_spec(case, run_id, target_commands)
        final_workspace = spec["workspace"]

        consulted = AttributionService(self.session).per_step_consulted(run_id)
        steps_numbers = sorted(set(meta) | set(snapshots))
        costs = self._step_costs(run_id, steps_numbers)
        rows: list[dict[str, Any]] = []
        for step in steps_numbers:
            step_meta = meta.get(step, {})
            row: dict[str, Any] = {
                "step": step,
                "snapshot": step_meta.get("kind")
                or ("snapshot" if step in snapshots else "missing"),
                "seconds": step_meta.get("seconds"),
                "consulted": consulted.get(step, []),
                **costs.get(step, {}),
            }
            swe = step_meta.get("swe_eval")
            if isinstance(swe, dict) and swe.get("f2p_total"):
                row["passed"] = int(swe.get("f2p_passed") or 0)
                row["total"] = int(swe.get("f2p_total") or 0)
                row["progress"] = swe_progress(swe)
                row["source"] = "swe_live"
            elif (
                spec.get("local")
                and step_meta.get("kind") == "diff"
                and step_meta.get("base")
                and spec["commands"]
            ):
                # SWE-bench Pro: its suite runs in the instance image, the
                # runner has no docker, so the live verdict was skipped. The
                # worker has the socket: rebuild the step's tree and grade it
                # with the case's own command.
                swe = self._grade_step_tree(run_id, step, step_meta, spec)
                if swe is not None and swe.get("f2p_total"):
                    row["passed"] = int(swe.get("f2p_passed") or 0)
                    row["total"] = int(swe.get("f2p_total") or 0)
                    row["progress"] = swe_progress(swe)
                    row["source"] = "swe_regraded"
                else:
                    row["progress"] = None
                    row["source"] = "unmeasured"
            elif step in snapshots and spec["commands"] and not spec.get("local"):
                snapshot_rel = f"data/runs/{run_id}/steps/{snapshots[step].name}"
                # Only a command that names the workspace can be pointed at a
                # snapshot. One that does not (an absolute path of its own, a
                # tamper guard on the lab's git state) would grade the *final*
                # state and credit it to every step, so it sits this pass out.
                pointed = [
                    [*argv[:-1], argv[-1].replace(final_workspace, snapshot_rel)]
                    for argv in spec["commands"]
                    if argv and final_workspace in argv[-1]
                ]
                if not pointed:
                    row["progress"] = None
                    row["source"] = "unmeasured"
                    rows.append(row)
                    continue
                step_spec = {
                    **spec,
                    "commands": pointed,
                    # The graders' deps were installed for the final run (and
                    # live in the run dir); a per-step pass reuses them.
                    "setup": [],
                }
                objective = self._run_objective(step_spec, run_id)
                conclusive = [
                    c for c in objective["checks"] if c.get("conclusive") is not False
                ]
                row["passed"] = sum(1 for c in conclusive if c.get("passed"))
                row["total"] = len(conclusive)
                row["progress"] = progress_of(objective)
                row["source"] = "snapshot_checks"
            else:
                row["progress"] = None
                row["source"] = "unmeasured"
            rows.append(row)

        trajectory = build_trajectory(rows).as_dict()
        trajectory["sources"] = sorted({row["source"] for row in rows})
        validation = self._upsert(run_id)
        objective = dict(validation.objective or {})
        objective["per_step"] = trajectory
        validation.objective = objective
        validation.updated_at = utc_now()
        self.session.add(validation)
        resolved = trajectory.get("steps_to_resolve")
        graded = len(rows) - int(trajectory.get("unmeasured_steps") or 0)
        if not graded:
            # Not "never fully resolved": nothing was measured, which is a
            # statement about the grader, not about the agent.
            self.session.add(
                RunEvent(
                    run_id=run_id,
                    level="warning",
                    message=(
                        f"Per-step reward: none of {len(rows)} step(s) could be "
                        "graded (no snapshot the case's checks can run on), so "
                        "the run has a final verdict but no per-step curve."
                    ),
                )
            )
            self.session.commit()
            return trajectory
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info",
                message=(
                    f"Per-step reward: {graded} of {len(rows)} step(s) graded; "
                    + (
                        f"resolved at step {resolved}"
                        if resolved
                        else "never fully resolved"
                    )
                    + (
                        f", {trajectory['post_resolution_cost_share']:.0%} of cost "
                        "spent after resolution"
                        if trajectory.get("post_resolution_cost_share") is not None
                        else ""
                    )
                    + "."
                ),
            )
        )
        self.session.commit()
        return trajectory

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
