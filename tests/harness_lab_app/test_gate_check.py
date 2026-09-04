"""The repository's own gate, measured before any agent touches it (ADR-0042).

The property under test is what makes a red run readable: a gate check measures
the repository at the pinned commit, names the phase that failed, and refuses to
run at all when it would be measuring nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_lab_app import checkout_service, config
from harness_lab_app.gate_check_service import (
    GateCheckError,
    build_gate_check_spec,
    summarize,
    workspace_for,
)
from harness_lab_app.models import TargetRepository


@pytest.fixture
def lab_root(monkeypatch, tmp_path: Path) -> Path:
    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    return tmp_path


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "payments",
        "name": "Payments",
        "repository_url": "https://example.invalid/payments.git",
        "pinned_commit": "c" * 40,
        "validation_commands": ["make check"],
    }
    base.update(over)
    return TargetRepository(**base)


def test_an_unpinned_repository_cannot_be_gate_checked(lab_root: Path):
    """A verdict that cannot name its commit cannot be compared with the runs."""
    with pytest.raises(GateCheckError):
        build_gate_check_spec(_target(pinned_commit=""))


def test_a_repository_with_no_runnable_gate_is_refused(lab_root: Path):
    """Otherwise the check passes by running nothing, which reads as success."""
    with pytest.raises(GateCheckError):
        build_gate_check_spec(_target(validation_commands=[]))
    with pytest.raises(GateCheckError):
        build_gate_check_spec(
            _target(validation_commands=["Ask the team whether it looks right"])
        )


def test_the_spec_provisions_then_sets_up_then_validates(lab_root: Path):
    spec = build_gate_check_spec(
        _target(
            setup_commands=["uv sync"],
            validation_commands=["make check", "make test"],
        )
    )

    assert [command["phase"] for command in spec["commands"]] == [
        "provision",
        "setup",
        "validate",
        "validate",
    ]
    provision = spec["commands"][0]["argv"][-1]
    assert "git clone" in provision
    assert spec["commit"][:10] in provision
    workspace = workspace_for("payments")
    assert all(workspace in command["argv"][-1] for command in spec["commands"][1:])


def test_prose_setup_lines_are_skipped_but_the_gate_still_runs(lab_root: Path):
    spec = build_gate_check_spec(
        _target(setup_commands=["Install whatever the README says"])
    )
    assert [command["phase"] for command in spec["commands"]] == [
        "provision",
        "validate",
    ]


def test_the_workspace_placeholder_is_rendered(lab_root: Path):
    spec = build_gate_check_spec(
        _target(validation_commands=["pytest {workspace}/tests"])
    )
    assert "{workspace}" not in spec["commands"][-1]["argv"][-1]
    assert workspace_for("payments") in spec["commands"][-1]["argv"][-1]


def test_summarize_names_the_phase_that_failed():
    """Provision, setup and validate failures mean different things."""
    verdict = summarize(
        [
            {"phase": "provision", "command": "clone", "exit_code": 0, "output": ""},
            {
                "phase": "setup",
                "command": "uv sync",
                "exit_code": 1,
                "output": "No interpreter found for Python 3.13",
            },
        ]
    )

    assert verdict["passed"] is False
    assert verdict["failed_phase"] == "setup"
    assert "No interpreter found" in verdict["checks"][1]["detail"]


def test_summarize_passes_only_when_something_actually_ran():
    assert summarize([])["passed"] is False
    assert (
        summarize(
            [
                {
                    "phase": "validate",
                    "command": "make check",
                    "exit_code": 0,
                    "output": "",
                }
            ]
        )["passed"]
        is True
    )
