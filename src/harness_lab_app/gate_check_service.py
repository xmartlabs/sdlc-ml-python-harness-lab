"""Does this repository pass its own gate, before any agent touches it?

The first failed run against an unfamiliar repository is ambiguous in the worst
way: the agent may have failed, or the repository may simply not build in the
runner image. Nothing in the run distinguishes them, so the audit's opening
number is unreadable.

A gate check removes the ambiguity by measuring the repository alone. It
provisions the pinned commit exactly the way a run does — clone, checkout,
setup commands — and then runs the repository's own validation commands with no
agent, no prompt and no model. A red gate check is a fact about the repository
or the image; every run after a green one is a fact about the agent.

It runs through the same executor as objective validation (the runner image,
lab root mounted at /app), so it needs no new infrastructure and inherits the
credential-free provisioning of ADR-0042: the clone source is the operator's own
checkout.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Any

from harness_lab_app.runner_capabilities import shell_for_command
from harness_lab_app.validation import is_executable_command

if TYPE_CHECKING:
    from harness_lab_app.models import TargetRepository

# Where the gate check provisions the repository, relative to the lab root (the
# executor's working directory). Under data/tmp because it is disposable by
# construction and already ignored.
_WORKSPACE_ROOT = "data/tmp/gate-checks"


class GateCheckError(ValueError):
    """A gate check could not be assembled for this repository."""


def workspace_for(target_id: str) -> str:
    from harness_lab_app.checkout_service import validate_target_id

    return f"{_WORKSPACE_ROOT}/{validate_target_id(target_id)}"


def _provision_commands(
    target: TargetRepository, workspace: str, commit: str
) -> list[dict[str, Any]]:
    """Clone the pinned commit into a disposable workspace, then set it up."""
    from harness_lab_app.target_service import clone_source

    source = clone_source(target)
    commands: list[dict[str, Any]] = [
        {
            "phase": "provision",
            "label": f"clone {target.target_id} @ {commit[:10]}",
            "argv": [
                "sh",
                "-c",
                f"rm -rf {shlex.quote(workspace)} && "
                f"git clone --quiet {shlex.quote(source)} {shlex.quote(workspace)} && "
                f"git -C {shlex.quote(workspace)} checkout --quiet {shlex.quote(commit)}",
            ],
        }
    ]
    for raw in target.setup_commands:
        if not is_executable_command(raw):
            continue
        rendered = raw.format(workspace=workspace, run_id=f"gate-{target.target_id}")
        commands.append(
            {
                "phase": "setup",
                "label": raw,
                "argv": [
                    shell_for_command(rendered),
                    "-c",
                    f"cd {shlex.quote(workspace)} && {rendered}",
                ],
            }
        )
    return commands


def build_case_dry_run_spec(
    case: Any, target: TargetRepository, workspace: str
) -> dict[str, Any]:
    """The case's own checks, against the pinned tree with no agent work applied.

    A validation command that passes here has told you nothing: it would have
    passed before the agent started. That is the whole point of running it —
    two of the talos cases were exactly this, and their green checks were read
    as the agent having done the work.

    Same provisioning and the same hardened lane as the gate check, because it
    is the same question asked of a different set of commands.
    """
    commit = (target.pinned_commit or "").strip()
    if not commit:
        raise GateCheckError(
            "Pin the repository first — a dry run has to name the commit it "
            "measured, or its verdict says nothing about this case."
        )
    validation = [
        command
        for command in (case.validation_commands or [])
        if is_executable_command(command)
    ]
    if not validation:
        raise GateCheckError(
            "This case has no runnable validation commands, so there is "
            "nothing to dry-run."
        )
    commands = _provision_commands(target, workspace, commit)
    for raw in validation:
        rendered = raw.format(workspace=workspace, run_id=f"dry-{case.case_id}")
        commands.append(
            {
                "phase": "validate",
                "label": raw,
                "argv": [
                    shell_for_command(rendered),
                    "-c",
                    f"cd {shlex.quote(workspace)} && {rendered}",
                ],
            }
        )
    return {
        "case_id": case.case_id,
        "target_id": target.target_id,
        "commit": commit,
        "workspace": workspace,
        "commands": commands,
    }


def dry_run_verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    """What a dry run against the clean tree says about the case that wrote it.

    Three answers, and only one of them is good news:

    - ``ok`` — at least one check failed conclusively on the untouched tree, so
      the case has something to measure.
    - ``tautological`` — every check passed before the agent did anything. The
      case cannot distinguish work from no work; its green is not a result.
    - ``inconclusive`` — provisioning or setup broke, or a check timed out.
      Nothing was measured, so nothing is claimed (ADR-0048).
    """
    from harness_lab_app.validation import INCONCLUSIVE_EXIT_CODES

    checks = [
        {
            "phase": result.get("phase", ""),
            "command": result.get("command", ""),
            "exit_code": result.get("exit_code"),
            "passed": result.get("exit_code") == 0,
            "conclusive": result.get("exit_code") not in INCONCLUSIVE_EXIT_CODES,
            "detail": _tail(str(result.get("output", ""))),
        }
        for result in results
    ]
    validate = [check for check in checks if check["phase"] == "validate"]
    setup_failed = any(
        not check["passed"] for check in checks if check["phase"] != "validate"
    )
    if setup_failed or not validate:
        verdict = "inconclusive"
    elif any(not check["passed"] and check["conclusive"] for check in validate):
        verdict = "ok"
    elif all(check["passed"] for check in validate):
        verdict = "tautological"
    else:
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "passed_on_clean": bool(validate) and all(c["passed"] for c in validate),
        "checks": checks,
    }


def build_gate_check_spec(target: TargetRepository) -> dict[str, Any]:
    """The commands that provision and test the repository, in order.

    ``phase`` travels with each command because the three failures mean
    different things: a failed *provision* is a checkout problem, a failed
    *setup* is a toolchain problem, and a failed *validate* is the repository's
    own suite being red at that commit. Collapsing them into one exit code is
    what makes a red gate unactionable.
    """
    from harness_lab_app.target_service import clone_source

    commit = (target.pinned_commit or "").strip()
    if not commit:
        raise GateCheckError(
            "Pin the repository first — a gate check has to name the commit it "
            "measured, or its verdict cannot be compared with the runs."
        )
    validation = [
        command
        for command in target.validation_commands
        if is_executable_command(command)
    ]
    if not validation:
        raise GateCheckError(
            "This repository has no runnable validation commands. Adopt the "
            "inferred gate on the readiness step, or add one, then try again."
        )

    workspace = workspace_for(target.target_id)
    source = clone_source(target)
    commands: list[dict[str, Any]] = [
        {
            "phase": "provision",
            "label": f"clone {target.target_id} @ {commit[:10]}",
            "argv": [
                "sh",
                "-c",
                f"rm -rf {shlex.quote(workspace)} && "
                f"git clone --quiet {shlex.quote(source)} {shlex.quote(workspace)} && "
                f"git -C {shlex.quote(workspace)} checkout --quiet {shlex.quote(commit)}",
            ],
        }
    ]
    for phase, raw_commands in (
        ("setup", target.setup_commands),
        ("validate", validation),
    ):
        for raw in raw_commands:
            if phase == "setup" and not is_executable_command(raw):
                continue
            rendered = raw.format(
                workspace=workspace, run_id=f"gate-{target.target_id}"
            )
            commands.append(
                {
                    "phase": phase,
                    "label": raw,
                    # `-c` and not `-lc`: a login shell resets PATH and would
                    # drop the runner image's /opt/venv/bin, exactly as
                    # objective validation documents. Only argv[0] varies —
                    # `/bin/sh` is dash in the runner image, so a command using
                    # bash syntax has to be handed to bash or it fails for a
                    # reason that has nothing to do with the repository.
                    "argv": [
                        shell_for_command(rendered),
                        "-c",
                        f"cd {shlex.quote(workspace)} && {rendered}",
                    ],
                }
            )
    return {
        "target_id": target.target_id,
        "commit": commit,
        "source": source,
        "workspace": workspace,
        "commands": commands,
    }


def build_health_spec(target: TargetRepository) -> dict[str, Any]:
    """The gate spec for an audit's ``health_at_pin``, or an empty one.

    Same commands as :func:`build_gate_check_spec`; the one difference is what
    "no runnable validation" means. A gate check with nothing to run is a
    configuration error the wizard can fix. An audit with nothing to run is a
    fact about the repository, reported as ``unmeasured`` beside everything
    else the audit found — so the spec comes back with no commands instead of
    refusing.
    """
    try:
        return build_gate_check_spec(target)
    except GateCheckError as exc:
        if "no runnable validation" not in str(exc):
            raise
        return {
            "target_id": target.target_id,
            "commit": (target.pinned_commit or "").strip(),
            "source": "",
            "workspace": "",
            "commands": [],
        }


_COUNTS = (
    re.compile(r"(?P<passed>\d+)\s+passed,\s+(?P<failed>\d+)\s+failed"),
    re.compile(r"(?P<failed>\d+)\s+failed,\s+(?P<passed>\d+)\s+passed"),
    re.compile(r"Tests:\s+(?P<failed>\d+)\s+failed,\s+(?P<passed>\d+)\s+passed"),
)


def parse_suite_counts(output: str) -> dict[str, int] | None:
    """``{"passed": n, "failed": m}`` when the tail says so in a shape we know.

    The last match wins: a suite that prints a summary per file and then a
    total ends with the total. Anything unfamiliar is ``None`` — an audit that
    invents a count is worse than one that says it could not read one.
    """
    found: dict[str, int] | None = None
    for line in (output or "").splitlines():
        for pattern in _COUNTS:
            match = pattern.search(line)
            if match:
                found = {
                    "passed": int(match.group("passed")),
                    "failed": int(match.group("failed")),
                }
    return found


def health_at_pin(results: list[dict[str, Any]], *, measured: bool) -> dict[str, Any]:
    """The repository's own suite at the pin, as evidence for the auditor.

    ``pass`` and ``fail`` only when a validate command actually ran and
    finished; a broken provision or setup is ``unmeasured`` with the phase
    that broke, because a suite that never ran says nothing about the tree.
    """
    if not measured:
        return {
            "status": "unmeasured",
            "reason": "no runnable validation commands",
            "checks": [],
        }
    verdict = summarize(results)
    validate = [c for c in verdict["checks"] if c["phase"] == "validate"]
    if verdict["failed_phase"] and verdict["failed_phase"] != "validate":
        return {
            "status": "unmeasured",
            "reason": f"{verdict['failed_phase']} failed before the suite ran",
            "checks": verdict["checks"],
        }
    if not validate:
        return {
            "status": "unmeasured",
            "reason": "no validate command ran",
            "checks": verdict["checks"],
        }
    counts = None
    for check in validate:
        counts = parse_suite_counts(check.get("detail", "")) or counts
    return {
        "status": "pass" if verdict["passed"] else "fail",
        "reason": "",
        "counts": counts,
        "checks": verdict["checks"],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Verdict + the phase that decided it.

    Stops being interesting after the first failure on purpose: the executor
    stops there too, because running a test suite in a workspace whose setup
    failed measures the setup, not the suite.
    """
    checks = [
        {
            "phase": result.get("phase", ""),
            "command": result.get("command", ""),
            "exit_code": result.get("exit_code"),
            "passed": result.get("exit_code") == 0,
            "detail": _tail(str(result.get("output", ""))),
        }
        for result in results
    ]
    failed = next((check for check in checks if not check["passed"]), None)
    return {
        "passed": failed is None and bool(checks),
        "failed_phase": failed["phase"] if failed else "",
        "failed_command": failed["command"] if failed else "",
        "checks": checks,
    }


def _tail(output: str, limit: int = 2000) -> str:
    text = output.strip()
    return text if len(text) <= limit else "…" + text[-limit:]
