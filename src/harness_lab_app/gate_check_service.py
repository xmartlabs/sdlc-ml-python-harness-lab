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

import shlex
from typing import TYPE_CHECKING, Any

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
                    # sh -c (not -lc): a login shell resets PATH and would drop
                    # the runner image's /opt/venv/bin, exactly as objective
                    # validation documents.
                    "argv": ["sh", "-c", f"cd {shlex.quote(workspace)} && {rendered}"],
                }
            )
    return {
        "target_id": target.target_id,
        "commit": commit,
        "source": source,
        "workspace": workspace,
        "commands": commands,
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
