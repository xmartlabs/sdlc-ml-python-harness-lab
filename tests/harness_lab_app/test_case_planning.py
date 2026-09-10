"""One case at a time, matched against what the runner actually has.

The lab used to answer "can this repository be evaluated?" with one question
about the repository: is its dominant language Python or Node. That conflates
two different facts. A Shell repository can propose a case that runs `pytest`;
a Python repository can propose a case that only checks whether AGENTS.md
exists. What these tests pin is the separation: a case's execution requirement
is derived from the case's own command, matched against the runner's declared
capabilities, and no case is blocked because of the language of the tree it
came from.

The two fixtures are modelled on the repositories that exposed the problem:
`talos` (Shell, hermetic bash tests, real commands) and `xl-skills-collection`
(Shell-classified, almost every case conceptual).
"""

from __future__ import annotations

import pytest

from harness_lab_app import repository_audit_service as svc
from harness_lab_app.executors import SubprocessRunExecutor
from harness_lab_app.models import TargetRepository
from harness_lab_app.runner_capabilities import RunnerCapability
from harness_lab_app.validation_service import ValidationService

# --------------------------------------------------------------------------
# Fixtures — virtual trees, the convention the audit tests already use
# --------------------------------------------------------------------------

TALOS_LIKE = {
    "CLAUDE.md": (
        "# talos\nPOSIX shell toolkit. Target runtime: bash 3.2 (macOS).\n"
        'Run `for t in tests/test_*.sh; do bash "$t"; done` before finishing.\n'
    ),
    "README.md": "# talos\nA shell toolkit.\n",
    "talos": '#!/usr/bin/env bash\nmain "$@"\n',
    "lib/parse.sh": "parse() { :; }\n",
    "lib/render.sh": "render() { :; }\n",
    "tests/test_parse.sh": "#!/usr/bin/env bash\nset -eu\n",
    "tests/test_render.sh": "#!/usr/bin/env bash\nset -eu\n",
    "benchmark/run.sh": "#!/usr/bin/env bash\n",
    "benchmark/model/run-model.sh": "#!/usr/bin/env bash\n",
}

XL_SKILLS_LIKE = {
    "AGENTS.md": "# Rules\nPick the kit that matches the task.\n",
    "README.md": "# Skills collection\n",
    "plugins/design-kit/AGENTS.md": "# Design kit rules\nFigma tokens are law.\n",
    "plugins/design-kit/README.md": "# Design kit\n",
    "plugins/design-kit/skills/figma-to-code/SKILL.md": (
        "---\nname: figma-to-code\ndescription: convert a frame\n---\n"
    ),
    "plugins/api-kit/AGENTS.md": "# API kit rules\n",
    "scripts/sync.sh": "#!/usr/bin/env bash\n",
    "docs/using-a-kit.md": "# Using a kit\n",
}


def _target(**over) -> TargetRepository:
    base = {
        "target_id": "talos",
        "name": "talos",
        "repository_url": "https://example.invalid/talos.git",
        "pinned_commit": "b" * 40,
        "validation_commands": [],
    }
    base.update(over)
    return TargetRepository(**base)


def _evidence(files: dict[str, str], **kwargs):
    return svc.collect_evidence(
        _target(), "b" * 40, sorted(files), lambda p: files.get(p, ""), **kwargs
    )


def _capabilities(**over) -> dict[str, RunnerCapability]:
    """The runner as the image really ships it, versions included."""
    table = {
        "python": RunnerCapability(
            "python", True, "3.11.9", "/usr/bin/python3", "probed"
        ),
        "node": RunnerCapability("node", True, "20.11.0", "/usr/bin/node", "probed"),
        "bash": RunnerCapability("bash", True, "5.2.37", "/usr/bin/bash", "probed"),
        "sh": RunnerCapability("sh", True, "", "/bin/sh", "probed"),
        "git": RunnerCapability("git", True, "2.39.5", "/usr/bin/git", "probed"),
        "jq": RunnerCapability("jq", True, "1.7.1", "/usr/bin/jq", "probed"),
    }
    table.update(over)
    return table


def _case(title: str, hint: str, version: str = "") -> dict[str, str]:
    return {
        "title": title,
        "why": "because the repository says so",
        "validation_hint": hint,
        "required_runtime_version": version,
    }


# --------------------------------------------------------------------------
# The repository's language stops deciding
# --------------------------------------------------------------------------


def test_a_shell_repository_is_no_longer_stopped_at_the_door():
    """talos: real bash tests, and a runner that has had bash all along.

    This is the case the whole change exists for. Nothing was installed — the
    runner image's Debian base has shipped bash 5.2.37 the entire time. What
    changed is that the classification table asks the capability list instead
    of naming two languages.
    """
    evidence = _evidence(TALOS_LIKE)

    assert evidence["runtime"]["supported"] == ["Shell"]
    assert evidence["runtime"]["runner_can_execute"] is True
    assert evidence["next_step"] == svc.NEXT_STEP_CONTINUE


def test_a_repository_the_runner_really_cannot_execute_still_stops():
    """The gate did not disappear; it stopped answering the wrong question."""
    evidence = _evidence(
        {
            "go.mod": "module example.com/x\n",
            "main.go": "package main\n",
            "AGENTS.md": "# Rules\n",
        }
    )

    assert evidence["runtime"]["runner_can_execute"] is False
    assert evidence["next_step"] == svc.NEXT_STEP_UNSUPPORTED


def test_the_evidence_carries_the_capabilities_it_judged_against():
    evidence = _evidence(TALOS_LIKE, capabilities=_capabilities())
    by_runtime = {item["runtime"]: item for item in evidence["runtime"]["capabilities"]}

    assert by_runtime["bash"]["available"] is True
    assert by_runtime["bash"]["version"] == "5.2.37"
    # A verdict a reader can re-derive: the facts it was reached with travel
    # with it, exactly as the rest of the evidence does.
    assert by_runtime["bash"]["source"] == "probed"


# --------------------------------------------------------------------------
# Per-case classification
# --------------------------------------------------------------------------


def test_a_static_case_is_not_blocked_by_the_language_of_the_tree():
    """xl-skills: most cases are a reading, and a reading needs no runtime."""
    case = svc.classify_case(
        _case(
            "The right AGENTS.md applies",
            "Check that plugins/design-kit/AGENTS.md is the one in force",
        ),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_STATIC
    assert case["runtime"] == ""


def test_a_bash_case_is_runnable_because_the_runner_has_bash():
    case = svc.classify_case(
        _case("The suite passes", 'for t in tests/test_*.sh; do bash "$t"; done'),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_RUNNABLE
    assert case["runtime"] == "bash"
    assert case["runtime_version"] == "5.2.37"


# --------------------------------------------------------------------------
# The hint contract — a command or a note, never both
# --------------------------------------------------------------------------


def test_a_command_wrapped_in_prose_is_read_as_a_command():
    """The talos audit's own hints looked like this, and were filed as static.

    A model shown a repository full of markdown writes the command in
    backticks. The executability heuristic reads the backticks and says "prose",
    so a repository of real, verifiable bash commands reported an execution
    coverage of "none" and every case it proposed came back unrunnable.
    """
    case = svc.classify_case(
        _case("The suite passes", "Ejecutar `bash tests/run.sh` y confirmar que pasa"),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_RUNNABLE
    # Anchored on the way out: the audit asks the model for a command relative
    # to the repository root, and `{workspace}` is what that root is called at
    # validation time. Without the translation the wizard called this runnable
    # and then refused to save it.
    assert case["validation_command"] == "bash {workspace}/tests/run.sh"
    assert "{workspace}" in case["validation_repair"]
    # Nothing the model wrote is thrown away; it just stops being run.
    assert "confirmar que pasa" in case["validation_note"]


def test_a_bare_command_carries_no_note():
    command, note = svc.normalize_hint("bash tests/run.sh")

    assert (command, note) == ("bash tests/run.sh", "")


def test_a_written_check_stays_a_written_check():
    command, note = svc.normalize_hint("Comprobar que existe un AGENTS.md en la raíz")

    assert command == ""
    assert note.startswith("Comprobar")


def test_a_hint_that_only_mentions_a_file_is_not_a_command():
    """`AGENTS.md` in backticks is a filename, not something to execute."""
    case = svc.classify_case(
        _case("Rules are present", "Comprobar que existe `AGENTS.md`"), _capabilities()
    )

    assert case["status"] == svc.CASE_STATIC
    assert case["validation_command"] == ""


def test_a_backticked_command_the_runner_lacks_is_blocked_not_static():
    case = svc.classify_case(
        _case("The image builds", "Correr `docker build .` y ver que termina"),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_BLOCKED
    assert case["runtime"] == "docker"


def test_a_posix_tool_the_image_ships_is_runnable():
    """`jq` is a capability, not a convenience.

    Every talos test died with a configuration error because the runner image
    had no jq. A case that needs it must be answerable — and, when it is
    missing, blocked rather than promised.
    """
    case = svc.classify_case(_case("jq is present", "jq --version"), _capabilities())

    assert case["status"] == svc.CASE_RUNNABLE
    assert case["runtime"] == "jq"

    without_jq = {
        name: capability for name, capability in _capabilities().items() if name != "jq"
    }
    assert (
        svc.classify_case(_case("jq is present", "jq --version"), without_jq)["status"]
        == svc.CASE_BLOCKED
    )


def test_a_command_naming_a_runtime_the_lab_will_never_have_is_blocked_not_static():
    """`docker build .` is not a check somebody can settle by reading.

    It does not look executable to the run-time heuristic — `docker` is not a
    command the validation lane knows how to launch, and the lane has no docker
    socket on purpose. Calling it static would file it as already answered,
    which is the one wrong answer: nothing is ever going to check it.
    """
    case = svc.classify_case(
        _case("The container image builds", "docker build ."), _capabilities()
    )

    assert case["status"] == svc.CASE_BLOCKED
    assert case["runtime"] == "docker"
    assert "no tiene" in case["reason"]


def test_prose_that_merely_mentions_a_runtime_stays_static():
    """The leading binary is the requirement; a word in a sentence is not."""
    case = svc.classify_case(
        _case("Docs are honest", "Review that the go module layout is documented"),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_STATIC


def test_a_case_needing_a_runtime_the_runner_lacks_is_blocked_alone():
    """One blocked case is one blocked case, not a stopped audit."""
    plan = svc.plan_cases(
        [
            _case("Shell suite", "bash tests/test_parse.sh"),
            _case("Python helper", "pytest -x -q"),
            _case("Benchmark", "bash benchmark/run.sh"),
            _case("Go tooling", "go test ./..."),
            _case("Rust tooling", "cargo test"),
        ],
        _capabilities(),
    )

    assert plan["runnable"] == 3
    assert plan["blocked"] == 2
    assert plan["coverage"] == svc.COVERAGE_PARTIAL
    assert [case["status"] for case in plan["cases"]] == [
        svc.CASE_RUNNABLE,
        svc.CASE_RUNNABLE,
        svc.CASE_RUNNABLE,
        svc.CASE_BLOCKED,
        svc.CASE_BLOCKED,
    ]


def test_one_repository_can_hold_bash_python_and_static_cases_at_once():
    """A mixed tree is the point: the case decides, not the census."""
    plan = svc.plan_cases(
        [
            _case("Shell suite", 'for t in tests/test_*.sh; do bash "$t"; done'),
            _case("Python suite", "uv run pytest -x -q"),
            _case(
                "Every kit has a README", "Review that each plugin ships a README.md"
            ),
        ],
        _capabilities(),
    )

    assert [case["runtime"] for case in plan["cases"]] == ["bash", "python", ""]
    assert plan["coverage"] == svc.COVERAGE_FULL


def test_coverage_is_none_only_when_nothing_at_all_can_be_settled():
    plan = svc.plan_cases([_case("Go tooling", "go test ./...")], _capabilities())

    assert plan["coverage"] == svc.COVERAGE_NONE


def test_an_audit_with_no_cases_reports_no_coverage_rather_than_full():
    assert svc.plan_cases([], _capabilities())["coverage"] == svc.COVERAGE_NONE


# --------------------------------------------------------------------------
# Runtime version awareness
# --------------------------------------------------------------------------


def test_bash_three_two_against_bash_five_is_partial_and_never_certified():
    """talos targets bash 3.2; the runner has 5.2.37. Both facts are reported.

    The lab does not provision bash 3.2 and does not pretend the run proves
    anything about it. Executable yes, exact target runtime no.
    """
    case = svc.classify_case(
        _case(
            "The suite passes on the target runtime", "bash tests/test_parse.sh", "3.2"
        ),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_RUNNABLE
    assert case["required_runtime_version"] == "3.2"
    assert case["compatibility"] == "partial"
    assert "certif" not in case["reason"].lower()


def test_a_matching_version_is_reported_as_exact():
    case = svc.classify_case(
        _case("The suite passes", "bash tests/test_parse.sh", "5.2"), _capabilities()
    )

    assert case["compatibility"] == "exact"


def test_a_model_invented_version_cannot_promote_a_blocked_case():
    case = svc.classify_case(
        _case("Go tooling", "go test ./...", "1.22"), _capabilities()
    )

    assert case["status"] == svc.CASE_BLOCKED


def test_a_version_that_is_not_a_version_is_dropped_rather_than_printed():
    case = svc.classify_case(
        _case("The suite passes", "bash tests/test_parse.sh", "3.2; rm -rf /"),
        _capabilities(),
    )

    assert case["required_runtime_version"] == ""
    assert case["compatibility"] == "unknown"


# --------------------------------------------------------------------------
# Folding the plan back into the evidence — one direction only
# --------------------------------------------------------------------------


def test_a_stopped_audit_reopens_when_some_case_can_still_be_settled():
    evidence = {
        "next_step": svc.NEXT_STEP_UNSUPPORTED,
        "next_step_reason": "Detected Go.",
        "runtime": {},
    }
    plan = svc.plan_cases(
        [_case("AGENTS.md is in force", "Review the rules file at the root")],
        _capabilities(),
    )

    updated = svc.apply_case_plan(evidence, plan)

    assert updated["next_step"] == svc.NEXT_STEP_CONTINUE
    assert "lenguaje dominante" in updated["next_step_reason"]


def test_a_plan_where_nothing_can_be_settled_leaves_the_stop_standing():
    evidence = {
        "next_step": svc.NEXT_STEP_UNSUPPORTED,
        "next_step_reason": "Detected Go.",
        "runtime": {},
    }
    plan = svc.plan_cases([_case("Go tooling", "go test ./...")], _capabilities())

    updated = svc.apply_case_plan(evidence, plan)

    assert updated["next_step"] == svc.NEXT_STEP_UNSUPPORTED
    assert updated["next_step_reason"] == "Detected Go."


def test_a_case_plan_can_never_close_a_door_the_evidence_left_open():
    """ADR-0035, in the direction that matters: the model proposed these cases.

    A repository with no harness at all stays at `no_harness` however the
    proposed cases classify — otherwise a model's five blocked suggestions
    would be enough to overturn a fact about the tree.
    """
    evidence = {
        "next_step": svc.NEXT_STEP_NO_HARNESS,
        "next_step_reason": "No harness.",
        "runtime": {},
    }
    plan = svc.plan_cases([_case("Go tooling", "go test ./...")], _capabilities())

    assert svc.apply_case_plan(evidence, plan)["next_step"] == svc.NEXT_STEP_NO_HARNESS


def test_the_report_names_the_runner_the_coverage_and_each_case():
    evidence = _evidence(TALOS_LIKE, capabilities=_capabilities())
    findings = {
        "summary": "A shell toolkit.",
        "harness": {"verdict": "completo", "signals": [], "gaps": []},
        "standards": [],
        "use_cases": [
            _case("The suite passes", "bash tests/test_parse.sh", "3.2"),
            _case("Every kit has a README", "Review that each plugin ships a README"),
        ],
    }
    plan = svc.plan_cases(findings["use_cases"], _capabilities())

    markdown = svc.render_markdown(
        findings, svc.apply_case_plan(evidence, plan), model="m", raw="{}"
    )

    assert "`bash 5.2.37` ✓" in markdown
    assert "Cobertura de ejecución: **total**" in markdown
    assert "Ejecutable por el runner" in markdown
    assert "Estático (sin ejecución)" in markdown


# --------------------------------------------------------------------------
# Shell actually executing, through the real executor
# --------------------------------------------------------------------------


class _Case:
    """The two attributes `build_validation_spec` reads off a case."""

    def __init__(self, commands: list[str]):
        self.validation_commands = commands
        self.type = "multi_step"


def _run(commands: list[str]) -> dict:
    """Run real commands through the host lane of the real executor.

    Pinned to :class:`SubprocessRunExecutor` on purpose. `get_executor()` would
    pick whatever backend the developer's machine is configured for, and a test
    whose verdict depends on whether docker happens to be running is not a test.
    The path under exercise — which interpreter argv[0] names, what the exit
    code becomes — is the same on both lanes.
    """
    from harness_lab_app.validation import build_validation_spec

    spec = build_validation_spec(_Case(commands), "shell-run")
    service = ValidationService(session=None, executor=SubprocessRunExecutor())  # type: ignore[arg-type]
    return service._run_objective(spec, "shell-run")


def test_a_shell_command_that_succeeds_passes():
    objective = _run(['bash -c "echo hello"'])

    assert objective["passed"] is True
    assert objective["checks"][0]["exit_code"] == 0


def test_a_shell_command_that_fails_fails():
    objective = _run(["bash -c 'exit 1'"])

    assert objective["passed"] is False
    assert objective["checks"][0]["exit_code"] == 1


def test_both_streams_of_a_shell_command_are_captured():
    objective = _run(["bash -c 'echo to-stdout; echo to-stderr >&2'"])
    detail = objective["checks"][0]["detail"]

    assert "to-stdout" in detail
    assert "to-stderr" in detail


def test_a_shell_command_that_never_returns_is_inconclusive_not_failed(monkeypatch):
    """ADR-0048: a check that did not measure is not a failure.

    The deadline is shortened rather than waited out; what is real here is the
    rest — a live `bash` child, the watchdog's `stop()`, and the 124 that makes
    `parse_objective` call the check inconclusive instead of failed.
    """
    from harness_lab_app import validation_service as module

    monkeypatch.setattr(module, "command_deadline_seconds", lambda *_a, **_k: 1)

    objective = _run(["bash -c 'sleep 30'"])

    assert objective["passed"] is None
    assert objective["checks"][0]["conclusive"] is False
    assert objective["checks"][0]["exit_code"] == 124


def test_the_shell_test_loop_runs_every_script_in_the_tree(tmp_path):
    """talos's own suite, written the way its CLAUDE.md writes it."""
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_one.sh").write_text("exit 0\n")
    (suite / "test_two.sh").write_text("exit 0\n")

    objective = _run(
        [f'cd {tmp_path} && for t in tests/test_*.sh; do bash "$t" || exit 1; done']
    )

    assert objective["passed"] is True, objective["checks"]


def test_a_failing_script_in_the_loop_fails_the_whole_check(tmp_path):
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_one.sh").write_text("exit 0\n")
    (suite / "test_two.sh").write_text("exit 3\n")

    objective = _run(
        [f'cd {tmp_path} && for t in tests/test_*.sh; do bash "$t" || exit 1; done']
    )

    assert objective["passed"] is False


def test_a_command_runs_in_the_directory_it_names(tmp_path):
    (tmp_path / "marker.txt").write_text("here\n")

    objective = _run([f"cd {tmp_path} && test -f marker.txt"])

    assert objective["passed"] is True


def test_a_command_using_bash_syntax_is_handed_to_bash_not_dash():
    """`/bin/sh` in the runner image is dash, which cannot parse `[[`.

    Run under `sh -c` this exits non-zero with a syntax error — a failure that
    says nothing at all about the repository under test.
    """
    from harness_lab_app.validation import build_validation_spec

    spec = build_validation_spec(_Case(["[[ -n x ]] && echo ok"]), "shell-run")

    assert spec["commands"][0][0] == "bash"
    assert _run(["[[ -n x ]] && echo ok"])["passed"] is True


def test_a_python_command_still_runs_under_sh_exactly_as_before():
    """The Python and Node lanes must not notice this change at all."""
    from harness_lab_app.validation import build_validation_spec

    spec = build_validation_spec(
        _Case(["pytest -x -q", "npm test", "make test"]), "shell-run"
    )

    assert [command[0] for command in spec["commands"]] == ["sh", "sh", "sh"]


def test_python_and_node_commands_still_execute_and_report():
    python = _run(["python3 -c 'print(1)'"])
    node = _run(["sh -c 'exit 0'"])

    assert python["passed"] is True
    assert node["passed"] is True


def test_the_validation_environment_carries_no_lab_secrets(monkeypatch, tmp_path):
    """The validation lane runs the audited repository's own commands.

    It never calls a model, so it has no business holding the gateway token,
    the GitHub token or the Langfuse keys that `build_run_environment` carries
    for the agent lane.
    """
    from harness_lab_app import executors

    class _Settings:
        repo_root = tmp_path
        docker_host_alias = "host.docker.internal"
        host_repo_root = "/host/repo"
        runner_image = "harness-lab-runner:latest"
        execution_backend = "docker"
        data_runs_dir = tmp_path / "data" / "runs"
        validation_network = "none"

    (tmp_path / ".env").write_text("GATEWAY_TOKEN=sk-secret\nGITHUB_TOKEN=ghp_x\n")
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings())

    env = executors.sanitized_run_environment()

    assert "GATEWAY_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert "sk-secret" not in " ".join(env.values())
    # And a host PATH must never reach a Linux container.
    assert "PATH" not in env


@pytest.mark.parametrize(
    ("phase", "expected"),
    [("validate", "none"), ("setup", None), ("provision", None)],
)
def test_only_the_validate_phase_loses_its_network(
    phase: str, expected: str | None, monkeypatch, tmp_path
):
    """`git clone` and `npm ci` need egress; running the suite does not."""
    from harness_lab_app import executors

    class _Settings:
        repo_root = tmp_path
        validation_network = "none"

    monkeypatch.setattr(executors, "get_settings", lambda: _Settings())

    options = executors.hardened_container_options(phase)

    assert options.get("network_mode") == expected
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges"]


def test_a_glob_handed_to_an_interpreter_becomes_the_loop_it_meant():
    """The talos audit's own first hint, from a clean database.

    `bash tests/test_*.sh` reads as "run the suite" and means "run whichever
    file sorts first, with the other fifty-four as its arguments" — verified
    against two real scripts: the second never executes. Proposing that as a
    runnable case would measure one script and call it a suite.
    """
    case = svc.classify_case(
        _case("Ejecutar la suite de tests Shell", "bash tests/test_*.sh"),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_RUNNABLE
    assert (
        case["validation_command"]
        == 'for t in {workspace}/tests/test_*.sh; do bash "$t" || exit 1; done'
    )
    # Repaired, and said so — before anything runs.
    assert "bucle" in case["validation_repair"]


def test_a_command_that_needs_no_repair_says_nothing():
    case = svc.classify_case(_case("Gate", "make test"), _capabilities())

    assert case["validation_command"] == "make test"
    assert case["validation_repair"] == ""


def test_the_audit_only_proposes_commands_the_case_editor_would_accept():
    """The two lanes must not disagree: `runnable` then 422 is the worst answer."""
    from harness_lab_app.case_validation import check_command

    hints = [
        "bash tests/test_*.sh",
        "bash benchmark/run.sh",
        "bash benchmark/model/run-model.sh",
        'for t in tests/test_*.sh; do bash "$t"; done',
        "make test",
    ]
    for hint in hints:
        row = svc.classify_case(_case("t", hint), _capabilities())
        command = row["validation_command"]
        if not command:
            continue
        assert check_command(command) is None, (hint, command)


def test_a_hint_listing_files_to_check_is_static_not_a_command():
    """A backticked path is the thing to look at, not the thing to run."""
    case = svc.classify_case(
        _case(
            "Comprobar el pipeline de skills",
            "Comprobar que existen `skills/talos-spec/SKILL.md` y `skills/talos-plan/SKILL.md`.",
        ),
        _capabilities(),
    )

    assert case["status"] == svc.CASE_STATIC
    assert case["validation_command"] == ""
