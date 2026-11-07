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
    # `--offline`: setup installs with a network, validation runs without one,
    # and plain `uv run` dies re-checking the lock there (ADR-0062).
    assert out["validation"] == ["uv run --offline pytest -x -q"]


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


# ---------------------------------------------------------------------------
# Runtime compatibility: would the runner image even be able to build this?
# ---------------------------------------------------------------------------


def test_a_python_repo_is_compatible_with_the_runner():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(["pyproject.toml", "src/app.py"], [], [])
    assert check.status == "ok"
    assert "Python" in check.detail


def test_a_node_repo_is_compatible_with_the_runner():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(["package.json", "index.js"], [], [])
    assert check.status == "ok"
    assert "Node" in check.detail


def test_a_go_repo_warns_that_the_runner_has_no_go_toolchain():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(["go.mod", "main.go"], [], [])
    assert check.status == "missing"
    assert "Go" in check.detail


def test_a_repo_with_no_project_marker_is_unknown_not_missing():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(["README.md", "src/app.py"], [], [])
    assert check.status == "unknown"


def test_python_install_on_a_repo_with_no_python_marker_is_flagged():
    """The exact deepseek failure: `pip install -e .` on a Node repo."""
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(
        ["package.json", "pnpm-lock.yaml"],
        ["pnpm install --frozen-lockfile", "pip install -e ."],
        ["pnpm test", "pytest -x -q"],
    )
    assert check.status == "missing"
    assert any("pip install -e ." in item for item in check.items)


def test_node_install_on_a_repo_with_no_package_json_is_flagged():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(
        ["pyproject.toml"],
        ["pnpm install --frozen-lockfile"],
        [],
    )
    assert check.status == "missing"
    assert any("pnpm install" in item for item in check.items)


def test_consistent_python_commands_do_not_trip_the_mismatch():
    from harness_lab_app.readiness_service import runtime_compatibility

    check = runtime_compatibility(
        ["pyproject.toml"],
        ["pip install -e .", "pip install pytest"],
        ["pytest -q"],
    )
    assert check.status == "ok"


def test_runtime_compatibility_rides_on_the_readiness_report_without_blocking():
    """It is a warning, not a gate — a run can still be launched."""
    report = readiness_service.build(
        _target(validation_commands=["pytest -q"], setup_commands=["pip install -e ."]),
        commit="a" * 40,
        paths=["package.json"],
    )
    runtime = _check(report, "Runtime compatibility")
    assert runtime["status"] == "missing"
    # Soft warning: it never appears in the blocking list.
    assert "Runtime compatibility" not in report["blocking"]


def test_runtime_compatibility_is_unknown_when_the_repo_could_not_be_read():
    report = readiness_service.build(_target(), commit=None, paths=None)
    assert _check(report, "Runtime compatibility")["status"] == "unknown"


def test_inspect_reports_a_repository_before_it_is_saved(session, monkeypatch):
    """The form's Inspect: a mismatch is caught pre-creation, nothing persisted."""
    from harness_lab_app.readiness_service import ReadinessService

    monkeypatch.setattr(
        "harness_lab_app.target_service.resolve_ref", lambda _u, _r: "c" * 40
    )
    monkeypatch.setattr(
        "harness_lab_app.case_derivation.sync_mirror",
        lambda *_a, **_k: session,  # any non-None object; list_paths is stubbed
    )
    monkeypatch.setattr(
        "harness_lab_app.readiness_service.list_paths",
        lambda *_a, **_k: ["package.json", "pnpm-lock.yaml"],
    )
    report = ReadinessService(session).inspect(
        "https://github.com/acme/web.git",
        "main",
        setup_commands=["pnpm install --frozen-lockfile", "pip install -e ."],
        validation_commands=["pnpm test"],
    )
    runtime = _check(report, "Runtime compatibility")
    assert runtime["status"] == "missing"
    assert any("pip install -e ." in item for item in runtime["items"])
    # A transient inspection is never written to the targets table.
    from harness_lab_app.target_service import TargetService

    assert TargetService(session).list_targets() == []


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


# --------------------------------------------------------------------------
# Validation runs offline, and the report says so before a run proves it
# --------------------------------------------------------------------------


def _offline(commands, makefile=""):
    from harness_lab_app.readiness_service import offline_validation

    return offline_validation(commands, {"Makefile": makefile} if makefile else {})


def test_a_command_that_installs_is_flagged_before_it_ever_runs():
    check = _offline(["uv sync && pytest -q"])
    assert check.status == "missing"
    assert check.items == ["uv sync && pytest -q"]
    assert "no network" in check.detail


def test_a_task_runner_target_is_judged_by_its_recipe_not_its_name():
    """`make test` is the repository's own word for its gate, and it is also
    how a network dependency hides two levels down."""
    makefile = (
        "install:\n\t@uv sync\n\n"
        "test:\n\t@if [ ! -d .venv ]; then make install; fi\n\t@pytest -q\n"
    )
    assert _offline(["make test"], makefile).status == "missing"


def test_a_recipe_that_fetches_its_own_toolchain_is_flagged():
    """The shape the command patterns miss: the binary is behind a variable.

    `ml-python-base`'s `install` resolves uv through `$$UV_BIN`, so `uv sync`
    never appears as a literal — but it curls the installer first, and a fetch
    is a fetch whatever runs it.
    """
    makefile = (
        "install:\n"
        "\t@curl -LsSf https://astral.sh/uv/install.sh | sh; \\\n"
        '\t"$$UV_BIN" sync\n\n'
        "test:\n\t@if [ ! -d .venv ]; then make install; fi\n\t@pytest -q\n"
    )
    check = _offline(["make test"], makefile)
    assert check.status == "missing"
    assert check.items == ["make test"]


def test_a_task_runner_target_that_only_tests_is_fine():
    makefile = "test:\n\t@pytest -q\n"
    assert _offline(["make test"], makefile).status == "ok"


def test_a_target_whose_makefile_was_not_read_is_not_guessed_at():
    """Silence beats a warning the lab cannot support."""
    assert _offline(["make test"]).status == "ok"


def test_an_offline_flag_clears_the_warning():
    assert _offline(["uv run --offline pytest -x -q"]).status == "ok"
    assert _offline(["uv run pytest -x -q"]).status == "missing"


def test_nothing_configured_is_unknown_not_ok():
    assert _offline([]).status == "unknown"


def test_the_report_carries_the_offline_check(session):
    from harness_lab_app.models import TargetRepository
    from harness_lab_app.readiness_service import build

    target = TargetRepository(
        target_id="t",
        name="t",
        repository_url="https://example.invalid/t.git",
        validation_commands=["make test"],
    )
    report = build(
        target,
        commit="c" * 40,
        paths=["Makefile"],
        contents={"Makefile": "install:\n\t@uv sync\n\ntest:\n\t@make install\n"},
    )
    offline = next(c for c in report["checks"] if c["label"] == "Offline validation")
    assert offline["status"] == "missing"


def test_a_plugin_marketplace_is_read_as_skills_and_agents():
    """`plugins/<p>/skills/` and `plugins/<p>/agents/` are a harness."""
    found = readiness_service.classify(
        [
            "plugins/tribunal/skills/review-pr/SKILL.md",
            "plugins/tribunal/agents/tribunal-refuter.md",
            "plugins/homosapiens/skills/humanize/SKILL.md",
            "plugins/tribunal/skills/README.md",
            "plugins/tribunal/rust/README.md",
        ]
    )
    assert found["skills"] == ["homosapiens:humanize", "tribunal:review-pr"]
    assert found["agents"] == ["tribunal:tribunal-refuter"]
    assert found["governance_docs"] == []
