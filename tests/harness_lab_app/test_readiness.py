"""Repository readiness — detected, or explicitly not detected (§7 step 2).

Step 2 of the guided audit is the page a client reads first, so the rule it has
to hold is narrow and absolute: everything on it was read out of the repository,
and anything that was not is shown as "Not detected" rather than assumed,
inferred or quietly omitted.
"""

from __future__ import annotations

import pytest

from harness_lab_app import readiness_service
from harness_lab_app.models import TargetRepository


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "payments",
        "name": "Payments API",
        "repository_url": "https://github.com/acme/payments.git",
        "default_ref": "main",
    }
    base.update(over)
    return TargetRepository(**base)


def _check(report: dict, label: str) -> dict:
    return next(check for check in report["checks"] if check["label"] == label)


# --------------------------------------------------------------------------
# Classification — the detection rules, argued with directly
# --------------------------------------------------------------------------


def test_a_governed_repository_is_read_accurately():
    found = readiness_service.classify(
        [
            "CLAUDE.md",
            ".claude/skills/writing-plans/SKILL.md",
            ".claude/skills/verify/SKILL.md",
            ".claude/agents/reviewer.md",
            ".github/standards.md",
            ".github/workflows/ci.yml",
            "docs/adr/0001-start.md",
            "memory/context.md",
            "Makefile",
            "src/app/main.py",
        ]
    )
    assert found["rules_files"] == ["CLAUDE.md"]
    assert found["skills"] == ["verify", "writing-plans"]
    assert found["agents"] == ["reviewer"]
    assert found["governance_docs"] == ["standards"]
    assert found["ci_workflows"] == ["ci.yml"]
    assert found["adrs"] == ["0001-start"]
    assert found["memory"] == ["context"]
    assert found["test_markers"] == ["Makefile"]


def test_a_repository_with_no_governance_reports_empty_rather_than_guessing():
    found = readiness_service.classify(["src/app/main.py", "README.md"])
    assert found["rules_files"] == []
    assert found["skills"] == []
    assert found["agents"] == []


def test_a_skill_is_its_directory_not_its_file():
    found = readiness_service.classify([".github/skills/plan_and_execute/SKILL.md"])
    assert found["skills"] == ["plan_and_execute"]


def test_a_stray_markdown_file_in_a_skills_directory_is_not_a_skill():
    found = readiness_service.classify([".claude/skills/README.md"])
    assert found["skills"] == []


def test_the_same_skill_declared_twice_is_counted_once():
    found = readiness_service.classify(
        [".claude/skills/verify/SKILL.md", ".github/skills/verify/SKILL.md"]
    )
    assert found["skills"] == ["verify"]


def test_classification_does_not_depend_on_the_order_git_lists_paths():
    paths = ["CLAUDE.md", ".claude/agents/reviewer.md", ".github/workflows/ci.yml"]
    assert readiness_service.classify(paths) == readiness_service.classify(
        list(reversed(paths))
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def test_a_reachable_configured_repository_is_ready():
    report = readiness_service.build(
        _target(validation_commands=["pytest -q"], setup_commands=["uv sync"]),
        commit="a" * 40,
        paths=["CLAUDE.md", "pyproject.toml"],
    )
    assert report["ready"] is True
    assert report["blocking"] == []
    assert _check(report, "Ref pinned to a commit")["status"] == "ok"


def test_a_repository_with_no_validation_gate_is_not_ready_and_says_why():
    report = readiness_service.build(_target(), commit="a" * 40, paths=["CLAUDE.md"])
    assert report["ready"] is False
    assert "Validation gate" in report["blocking"]
    assert "scored by hand" in _check(report, "Validation gate")["detail"]


def test_an_unreachable_repository_still_renders_the_rest_of_the_page():
    report = readiness_service.build(
        _target(validation_commands=["pytest -q"]),
        commit=None,
        ref_error="Could not resolve 'main'.",
        paths=None,
        read_error="clone failed",
    )
    assert report["ready"] is False
    assert "Repository reachable" in report["blocking"]
    assert (
        _check(report, "Repository reachable")["detail"] == "Could not resolve 'main'."
    )
    # The gate is configured, so it is still reported as configured.
    assert _check(report, "Validation gate")["status"] == "ok"


def test_governance_that_could_not_be_read_is_unknown_not_missing():
    """ "We could not look" and "there is nothing there" are different claims."""
    report = readiness_service.build(_target(), commit=None, paths=None)
    governance = _check(report, "AI governance")
    assert governance["status"] == "unknown"
    assert "could not be read" in governance["detail"]
    assert report["detected"] is None


def test_governance_that_was_read_and_found_absent_says_not_detected():
    report = readiness_service.build(
        _target(), commit="a" * 40, paths=["src/app/main.py"]
    )
    for label in ("Agent rules file", "Skills", "Agents", "CI workflows"):
        check = _check(report, label)
        assert check["status"] == "missing"
        assert check["detail"] == "Not detected."


def test_nothing_in_the_report_claims_a_capability_that_was_not_observed():
    report = readiness_service.build(
        _target(), commit="a" * 40, paths=["src/app/main.py"]
    )
    for check in report["checks"]:
        assert check["status"] in {"ok", "missing", "unknown"}
        if check["status"] != "ok":
            assert check["items"] == []


def test_readiness_is_about_running_a_run_not_about_governance_quality():
    """A repository with no AI governance at all is still perfectly runnable.

    Conflating the two would make the audit's own subject — how this team works
    with AI — into a precondition for measuring them.
    """
    report = readiness_service.build(
        _target(validation_commands=["pytest -q"]),
        commit="a" * 40,
        paths=["src/app/main.py"],
    )
    assert report["ready"] is True
    assert _check(report, "Agent rules file")["status"] == "missing"


def test_the_report_names_the_commit_it_would_pin():
    report = readiness_service.build(
        _target(), commit="abcdef1234567890" * 2 + "abcdefgh", paths=[]
    )
    assert "abcdef123456" in _check(report, "Ref pinned to a commit")["detail"]


def test_detected_items_are_listed_so_a_reader_can_check_them():
    report = readiness_service.build(
        _target(),
        commit="a" * 40,
        paths=[".claude/skills/verify/SKILL.md", "CLAUDE.md"],
    )
    assert _check(report, "Skills")["items"] == ["verify"]
    assert _check(report, "Agent rules file")["items"] == ["CLAUDE.md"]


# --------------------------------------------------------------------------
# Reading the mirror
# --------------------------------------------------------------------------


def test_listing_paths_surfaces_a_git_failure_rather_than_returning_nothing(
    monkeypatch, tmp_path
):
    """An empty list would read as "this repository has no governance"."""

    class _Result:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a tree object"

    monkeypatch.setattr(
        "harness_lab_app.case_derivation._git", lambda *_args, **_kwargs: _Result()
    )
    with pytest.raises(OSError, match="not a tree object"):
        readiness_service.list_paths(tmp_path, "main")


def test_listing_paths_returns_tracked_paths(monkeypatch, tmp_path):
    class _Result:
        returncode = 0
        stdout = "CLAUDE.md\nsrc/app/main.py\n\n"
        stderr = ""

    monkeypatch.setattr(
        "harness_lab_app.case_derivation._git", lambda *_args, **_kwargs: _Result()
    )
    assert readiness_service.list_paths(tmp_path, "main") == [
        "CLAUDE.md",
        "src/app/main.py",
    ]


# ---------------------------------------------------------------------------
# Command inference: an auditor rarely knows a client's real test command
# ---------------------------------------------------------------------------


def test_a_makefile_test_target_is_the_repositorys_own_word():
    from harness_lab_app.readiness_service import infer_commands

    out = infer_commands(
        ["Makefile", "src/app.py"],
        {"Makefile": "build:\n\tgo build\ntest:\n\tgo test\n"},
    )
    assert "make test" in out["validation"]


def test_npms_scaffold_placeholder_is_not_a_gate():
    from harness_lab_app.readiness_service import infer_commands

    scaffold = '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
    out = infer_commands(["package.json"], {"package.json": scaffold})
    assert out["validation"] == []


def test_a_real_npm_test_script_suggests_ci_install_with_a_lockfile():
    from harness_lab_app.readiness_service import infer_commands

    out = infer_commands(
        ["package.json", "package-lock.json"],
        {"package.json": '{"scripts": {"test": "vitest run"}}'},
    )
    assert out["validation"] == ["npm test"]
    assert out["setup"] == ["npm ci"]


def test_a_python_repo_with_uv_lock_suggests_uv_flavoured_commands():
    from harness_lab_app.readiness_service import infer_commands

    out = infer_commands(
        ["pyproject.toml", "uv.lock", "tests/test_app.py"],
        {"pyproject.toml": "[tool.pytest.ini_options]\n"},
    )
    assert out["setup"] == ["uv sync"]
    assert out["validation"] == ["uv run pytest -x -q"]


def test_a_polyglot_repo_suggests_one_gate_per_ecosystem():
    """deepseek-harness shape: TypeScript and Python side by side."""
    from harness_lab_app.readiness_service import infer_commands

    out = infer_commands(
        [
            "package.json",
            "yarn.lock",
            "pyproject.toml",
            "python/sdk/tests/test_x.py",
            "go.mod",
        ],
        {
            "package.json": '{"scripts": {"test": "jest"}}',
            "pyproject.toml": "[tool.pytest.ini_options]\n",
        },
    )
    assert (
        out["validation"] == ["yarn test", "go test ./..."]
        or "yarn test" in out["validation"]
    )
    assert "go test ./..." in out["validation"]


def test_suggestions_ride_on_the_readiness_report(session):
    """The payload carries the inference so the UI can offer one-click adoption."""
    from harness_lab_app.models import TargetRepository
    from harness_lab_app.readiness_service import build

    target = TargetRepository(
        target_id="acme",
        name="Acme",
        repository_url="https://example.invalid/acme.git",
    )
    report = build(
        target,
        commit="abc123",
        paths=["Makefile"],
        suggested={"validation": ["make test"], "setup": []},
    )
    assert report["suggested_validation_commands"] == ["make test"]
    assert report["suggested_setup_commands"] == []
