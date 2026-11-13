"""A case is refused when its checks could not measure the agent's work.

The two cases written from the talos audit ran four experiment arms between
them and produced numbers about themselves. Nothing in a result says so — a
false green looks exactly like a real one — so the shapes that produce them are
refused where the case is written. What these tests pin is that each rule fires
on the shape that actually happened, and stays quiet on the commands the lab
already ships.
"""

from __future__ import annotations

import pytest

from harness_lab_app.case_service import CaseService, CaseValidationError
from harness_lab_app.case_validation import (
    REASON_GLOB_RUNS_ONE_FILE,
    REASON_INTERPRETER_MISMATCH,
    REASON_IS_A_DOCUMENT,
    REASON_LOOP_SWALLOWS_FAILURES,
    REASON_NOT_RUNNABLE,
    REASON_TAUTOLOGICAL,
    REASON_UNANCHORED,
    anchor_workspace,
    check_case,
    check_command,
)
from harness_lab_app.models import CaseType
from harness_lab_app.schemas import CaseCreate

# --------------------------------------------------------------------------
# The rules, against the commands that made them necessary
# --------------------------------------------------------------------------


def test_a_loop_that_returns_only_its_last_status_is_refused():
    """Case 1 of the talos audit, verbatim.

    `for t in …; do bash "$t"; done` exits with the last script's status, so
    fifty-four failures ahead of one pass is a green check. Both arms recorded
    `task_validation_pass = 1` while the run's own stored detail read
    `rc=2 CONFIG ERROR`.
    """
    problem = check_command('for t in {workspace}/tests/test_*.sh; do bash "$t"; done')

    assert problem is not None
    assert problem.reason == REASON_LOOP_SWALLOWS_FAILURES
    # The suggestion is the same loop, fixed — not advice to go read something.
    assert "|| exit 1" in problem.suggestion
    assert check_command(problem.suggestion) is None


def test_a_repository_path_with_nothing_to_anchor_it_is_refused():
    """Case 2 of the talos audit: exit 127, and the file existed.

    Validation runs from the lab's own root, so `benchmark/model/run-model.sh`
    resolved there. The costly-model scenario the case was written to check was
    never executed, in either arm, in either experiment.
    """
    problem = check_command("bash benchmark/model/run-model.sh")

    assert problem is not None
    assert problem.reason == REASON_UNANCHORED
    assert problem.suggestion == "bash {workspace}/benchmark/model/run-model.sh"
    assert check_command(problem.suggestion) is None


def test_a_check_that_cannot_fail_is_refused():
    """The second half of case 2: a predicate over its own literals.

    `any(p in text for p in patterns)` with no file read matches whatever the
    command carries. It reported 0.5 in both arms — an artifact of the case.
    """
    problem = check_command(
        "python3 -c \"patterns=['cost']; import sys; "
        'sys.exit(0 if any(p for p in patterns) else 1)"'
    )

    assert problem is not None
    assert problem.reason == REASON_TAUTOLOGICAL


@pytest.mark.parametrize(
    "command",
    [
        # Imports the agent's module from the workspace and asserts on it.
        "python3 -c \"import sys; sys.path.insert(0,'{workspace}'); import pipe; "
        "s=pipe.summary(); sys.exit(0 if all(k in s for k in ('a','b')) else 1)\"",
        # Runs the agent's entry point and checks what it returned.
        'python3 -c "import subprocess,sys; r=subprocess.run([sys.executable,'
        "'{workspace}/main.py']); sys.exit(0 if any([r.returncode == 0]) else 1)\"",
    ],
)
def test_a_predicate_over_the_agents_own_code_is_a_real_check(command: str):
    """A generated Iris case lost its EDA check to this rule: `all(...)` with no
    file read, but over what the agent's module returned. Importing or running
    what the agent wrote is reading it."""
    assert check_command(command) is None


@pytest.mark.parametrize("command", ["true", "echo done", ":"])
def test_the_obvious_tautologies_are_refused(command: str):
    problem = check_command(command)

    assert problem is not None
    assert problem.reason == REASON_TAUTOLOGICAL


def test_prose_is_refused_as_prose():
    problem = check_command("the file should look right")

    assert problem is not None
    assert problem.reason == REASON_NOT_RUNNABLE


@pytest.mark.parametrize(
    "command",
    [
        "make test",
        "cd {workspace} && pytest -q",
        'test "$(printf x | python {workspace}/main.py words)" = "1"',
        "git -C /app diff --quiet HEAD -- benchmarks/tetris-v2/tests",
        'for t in {workspace}/tests/test_*.sh; do bash "$t" || exit 1; done',
        "jq -e . {workspace}/manifest.json",
        "BENCH_SMOKE=1 timeout 45 python {workspace}/main.py",
    ],
)
def test_the_commands_the_lab_already_runs_are_left_alone(command: str):
    """The rules must reject shapes, not tighten the vocabulary."""
    assert check_command(command) is None


# --------------------------------------------------------------------------
# The case as a whole
# --------------------------------------------------------------------------


def test_commands_without_a_rubric_are_refused():
    """The mechanism that parked all four talos runs unscored.

    `_llm_judge_rubric` returns at its first guard when the rubric is empty,
    silently, and the run sits in `awaiting_manual_score` looking like a
    scoring backlog rather than the case defect it is.
    """
    problems = check_case(["make test"], scoring_rubric="", success_criteria={})

    assert any("scoring rubric" in message for message in problems)


def test_success_criteria_count_as_a_scale():
    assert check_case(["make test"], "", {"passes": "the suite is green"}) == []


def test_an_objectively_scored_case_needs_no_rubric():
    """SWE-bench's score is a fact, not an opinion: resolved or not."""
    assert check_case(["make test"], "", {}, case_type="swe_bench_instance") == []


def test_a_case_with_no_commands_at_all_needs_no_rubric_from_this_rule():
    assert check_case([], "", {}) == []


# --------------------------------------------------------------------------
# Enforced where cases are written
# --------------------------------------------------------------------------


def _payload(**over) -> CaseCreate:
    base = {
        "case_id": "talos-suite",
        "origin_target_id": "",
        "name": "The talos suite passes",
        "type": CaseType.repo_task,
        "prompt_variants": {"default": "Make the suite pass"},
        "validation_commands": [
            'for t in {workspace}/tests/test_*.sh; do bash "$t"; done'
        ],
        "scoring_rubric": "10 when every script passes.",
    }
    base.update(over)
    return CaseCreate(**base)


def test_creating_a_case_refuses_the_bad_command(session):
    with pytest.raises(CaseValidationError) as raised:
        CaseService(session).create_case(_payload())

    assert any("|| exit 1" in problem for problem in raised.value.problems)


def test_the_corrected_case_is_accepted(session):
    case = CaseService(session).create_case(
        _payload(
            validation_commands=[
                'for t in {workspace}/tests/test_*.sh; do bash "$t" || exit 1; done'
            ]
        )
    )

    assert case.case_id == "talos-suite"


def test_editing_a_case_is_held_to_the_same_contract(session):
    service = CaseService(session)
    service.create_case(
        _payload(validation_commands=["cd {workspace} && bash tests/run.sh"])
    )

    with pytest.raises(CaseValidationError):
        service.update_case("talos-suite", _payload())


def _route(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def test_the_api_answers_422_with_one_message_per_command(session):
    """422, not 404 and not 500: the request reached a real endpoint and was
    refused on its content, with each message naming a command and its fix."""
    from fastapi import HTTPException

    from harness_lab_app.api import create_app

    post = _route(create_app(), "/api/cases", "POST")

    with pytest.raises(HTTPException) as raised:
        post(
            _payload(
                validation_commands=[
                    'for t in {workspace}/tests/test_*.sh; do bash "$t"; done',
                    "bash benchmark/model/run-model.sh",
                ]
            ),
            session,
        )

    assert raised.value.status_code == 422
    assert len(raised.value.detail) == 2


# --------------------------------------------------------------------------
# The dry run — the empirical half of the same question
# --------------------------------------------------------------------------


class _ScriptedHandle:
    def __init__(self, exit_code: int, output: str = "") -> None:
        self._exit_code = exit_code
        self._output = output

    def iter_lines(self):
        if self._output:
            yield ("info", self._output)

    def stop(self) -> None:  # pragma: no cover - the dry run never stops one
        pass

    def wait(self) -> int:
        return self._exit_code


class _ScriptedExecutor:
    """Answers each phase with a fixed exit code, in the order they are run."""

    def __init__(self, by_phase: dict[str, list[int]]) -> None:
        self.by_phase = {phase: list(codes) for phase, codes in by_phase.items()}
        self.phases: list[str] = []

    def start(self, _run_id: str, _argv, *, hardened=False, phase="", **_kw):
        self.phases.append(phase)
        codes = self.by_phase.get(phase) or [0]
        return _ScriptedHandle(codes.pop(0) if codes else 0)


def _seed_case_and_target(session, commands: list[str]) -> None:
    from harness_lab_app.models import TargetRepository

    session.add(
        TargetRepository(
            target_id="talos",
            name="talos",
            repository_url="https://example.invalid/talos.git",
            pinned_commit="c" * 40,
        )
    )
    session.commit()
    CaseService(session).create_case(
        _payload(
            case_id="talos-dry",
            validation_commands=commands,
            origin_target_id="talos",
        )
    )


def _dry_run(session, executor):
    from harness_lab_app.tasks import _dry_run_case_body

    return _dry_run_case_body(session, "talos-dry", executor)


def test_a_check_that_fails_on_the_clean_tree_has_something_to_measure(session):
    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    record = _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [1]}))

    assert record["verdict"] == "ok"
    assert record["passed_on_clean"] is False


def test_a_check_that_passes_before_the_agent_starts_is_tautological(session):
    """The verdict that would have stopped both talos cases before they ran."""
    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    record = _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [0]}))

    assert record["verdict"] == "tautological"
    assert record["passed_on_clean"] is True


def test_a_broken_checkout_settles_nothing_rather_than_accusing_the_case(session):
    """ADR-0048: a check that did not measure is not a failure."""
    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    record = _dry_run(session, _ScriptedExecutor({"provision": [1]}))

    assert record["verdict"] == "inconclusive"


def test_a_check_that_timed_out_is_inconclusive_not_evidence_of_anything(session):
    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    record = _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [124]}))

    assert record["verdict"] == "inconclusive"


def test_an_unanchored_check_that_exits_127_settles_nothing(session):
    """127 on the clean tree used to read as "the case has something to measure".

    It is the shell saying the command does not exist — a fact about the case,
    not about whether the case can distinguish work from no work (ADR-0062).
    """
    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    record = _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [127]}))

    assert record["verdict"] == "inconclusive"
    assert record["checks"][-1]["inconclusive_reason"] == "not_found"


def test_every_check_is_asked_even_after_one_of_them_fails(session):
    """The question is whether *any* check can fail, so none may go unasked."""
    _seed_case_and_target(
        session,
        ["bash {workspace}/tests/a.sh", "bash {workspace}/tests/b.sh"],
    )

    executor = _ScriptedExecutor({"provision": [0], "validate": [1, 0]})
    record = _dry_run(session, executor)

    assert executor.phases.count("validate") == 2
    assert record["verdict"] == "ok"


def test_the_verdict_is_stored_on_the_case(session):
    import json

    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])
    _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [0]}))

    case = CaseService(session).get_case("talos-dry")
    stored = json.loads(case.metadata_json["dry_run"])

    assert stored["verdict"] == "tautological"
    assert stored["commit"] == "c" * 40


def test_a_tautological_case_cannot_be_launched(session):
    """The refusal that matters: it is at the door of the experiment."""
    from harness_lab_app.experiment_service import ExperimentError, ExperimentService

    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])
    _dry_run(session, _ScriptedExecutor({"provision": [0], "validate": [0]}))

    with pytest.raises(ExperimentError, match="passed their own checks"):
        ExperimentService(session)._refuse_tautological_cases(["talos-dry"])


def test_a_case_that_was_never_dry_run_is_not_refused(session):
    """This gate refuses a known defect; it does not demand a ceremony."""
    from harness_lab_app.experiment_service import ExperimentService

    _seed_case_and_target(session, ["bash {workspace}/tests/run.sh"])

    ExperimentService(session)._refuse_tautological_cases(["talos-dry"])


# --------------------------------------------------------------------------
# A glob is not a suite
# --------------------------------------------------------------------------


def test_an_interpreter_handed_a_glob_is_refused():
    """Verified against two real scripts: only the first one runs.

    `bash tests/test_a.sh tests/test_b.sh` runs test_a.sh with test_b.sh as its
    `$1`. The exit code is the first script's, so a suite of fifty-five is
    measured by whichever file sorts first.
    """
    problem = check_command("bash {workspace}/tests/test_*.sh")

    assert problem is not None
    assert problem.reason == REASON_GLOB_RUNS_ONE_FILE
    assert (
        problem.suggestion
        == 'for t in {workspace}/tests/test_*.sh; do bash "$t" || exit 1; done'
    )
    assert check_command(problem.suggestion) is None


def test_a_glob_inside_a_loop_is_the_correct_shape_and_stays():
    assert (
        check_command('for t in {workspace}/tests/*.sh; do bash "$t" || exit 1; done')
        is None
    )


def test_a_single_script_is_not_a_glob():
    assert check_command("bash {workspace}/tests/run.sh") is None


def test_a_glob_that_is_an_argument_rather_than_the_script_is_left_alone():
    """`pytest {workspace}/tests/test_*.py` is pytest's own argument list."""
    assert check_command("pytest {workspace}/tests/test_*.py") is None


# --------------------------------------------------------------------------
# Anchoring — a translation, not a guess
# --------------------------------------------------------------------------


def test_anchoring_names_the_repository_the_path_belongs_to():
    assert anchor_workspace("bash tests/run.sh") == "bash {workspace}/tests/run.sh"


def test_anchoring_leaves_a_command_that_already_says_where_it_is():
    for command in (
        "bash {workspace}/tests/run.sh",
        "make test",
        "git -C /app diff --quiet HEAD",
        "cd {workspace} && pytest -q",
    ):
        assert anchor_workspace(command) == command


def test_anchoring_reaches_a_path_inside_a_loop():
    assert (
        anchor_workspace('for t in tests/*.sh; do bash "$t" || exit 1; done')
        == 'for t in {workspace}/tests/*.sh; do bash "$t" || exit 1; done'
    )


# --------------------------------------------------------------------------
# A file is not a command
# --------------------------------------------------------------------------


def test_a_path_to_a_document_is_refused_as_what_it_is():
    """From a real talos audit: `{workspace}/skills/talos-spec/SKILL.md`.

    The hint said "check that these SKILL.md files exist" — a reader's check.
    The backticked path passed the executability heuristic on the strength of
    a slash, the placeholder short-circuited every other question, and the
    wizard proposed handing a markdown file to `sh -c`.
    """
    problem = check_command("{workspace}/skills/talos-spec/SKILL.md")

    assert problem is not None
    assert problem.reason == REASON_IS_A_DOCUMENT


@pytest.mark.parametrize(
    "command",
    [
        "test -s {workspace}/skills/talos-spec/SKILL.md",
        "grep -q 'name:' {workspace}/skills/talos-spec/SKILL.md",
        "bash {workspace}/tests/run.sh",
        "{workspace}/scripts/check.sh",
    ],
)
def test_a_command_that_reads_or_runs_a_file_is_still_a_command(command: str):
    assert check_command(command) is None


# --------------------------------------------------------------------------
# An interpreter handed a file it cannot run fails on every run
# --------------------------------------------------------------------------


def test_pytest_on_a_shell_script_is_refused_with_the_right_interpreter():
    """`pytest {workspace}/tests/test_armed_hook.sh -q` exited 4 on both arms of
    a talos experiment and nothing had refused it."""
    problem = check_command("pytest {workspace}/tests/test_armed_hook.sh -q")
    assert problem is not None
    assert "pytest" in problem.reason
    assert "shell/JS" in problem.reason
    assert problem.suggestion == "bash {workspace}/tests/test_armed_hook.sh"


def test_bash_on_a_python_file_and_node_on_a_shell_file_are_refused():
    assert (
        check_command("bash {workspace}/x.py").suggestion == "python3 {workspace}/x.py"
    )
    assert (
        check_command("node {workspace}/run.sh").suggestion == "bash {workspace}/run.sh"
    )
    assert (
        check_command("python3 {workspace}/app.test.js").suggestion
        == "node {workspace}/app.test.js"
    )


def test_matching_interpreters_and_inline_scripts_are_not_mismatches():
    assert check_command("bash {workspace}/tests/test_x.sh") is None
    assert check_command("pytest {workspace}/tests/test_x.py -q") is None
    assert check_command("node {workspace}/x.test.js") is None
    # The path lives inside a quoted script: bash runs python, it does not read x.py.
    assert check_command('bash -c "python3 {workspace}/x.py"') is None
    assert (
        check_command(
            "python3 -c \"import sys; sys.exit(0 if open('{workspace}/o.sh') else 1)\""
        )
        is None
    )


def test_the_mismatch_reason_is_formatted_not_a_template():
    problem = check_command("pytest {workspace}/t.sh")
    assert "{kind}" not in problem.reason and "{interpreter}" not in problem.reason
    assert REASON_INTERPRETER_MISMATCH.split("{")[0] in problem.reason
