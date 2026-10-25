from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import harness_lab_app.validation as validation
from harness_lab_app.validation import (
    DEFAULT_VALIDATION_DEADLINE_SECONDS,
    _portable_timeout,
    build_validation_spec,
    collect_cost,
    collect_effort,
    command_deadline_seconds,
    compute_composite,
    describe_objective_for_judge,
    parse_objective,
)


def test_portable_timeout_adds_the_kill_escalation(monkeypatch):
    """`timeout N` alone cannot kill a child that survives SIGTERM.

    That is not hypothetical: SDL2 turns SIGTERM into an SDL_QUIT *event*, so
    the fourth run of experiment `...-on--2` sat wedged until it was killed by
    hand. `-k` escalates to SIGKILL, which nothing can catch.
    """
    monkeypatch.setattr(validation.shutil, "which", lambda _name: "/usr/bin/timeout")
    assert (
        _portable_timeout("BENCH_SMOKE=1 timeout 45 python main.py")
        == "BENCH_SMOKE=1 timeout -k 5 45 python main.py"
    )


def test_portable_timeout_uses_gtimeout_fallback(monkeypatch):
    monkeypatch.setattr(
        validation.shutil, "which", lambda name: None if name == "timeout" else "/x"
    )
    assert (
        _portable_timeout("timeout 45 python main.py")
        == "gtimeout -k 5 45 python main.py"
    )


def test_command_deadline_reads_the_rewritten_budget(monkeypatch):
    """The outer watchdog must still find the budget after `-k` is inserted."""
    monkeypatch.setattr(validation.shutil, "which", lambda _name: "/usr/bin/timeout")
    rendered = _portable_timeout("timeout 45 python main.py")
    assert command_deadline_seconds(rendered) == 45 + 5 + 30


def test_command_deadline_falls_back_when_the_command_states_none():
    assert (
        command_deadline_seconds("pytest tests/ -q")
        == DEFAULT_VALIDATION_DEADLINE_SECONDS
    )


def test_portable_timeout_drops_when_absent(monkeypatch):
    monkeypatch.setattr(validation.shutil, "which", lambda _name: None)
    assert _portable_timeout("a=1 timeout 45 python main.py") == "a=1 python main.py"


def _case(**kwargs):
    defaults = {"type": "build_from_scratch", "validation_commands": []}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_collect_cost_parses_usage_json(tmp_path: Path):
    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 200000,
                "output_tokens": 2000,
                "cost_usd": None,
                "time_minutes": 1.0,
                "steps": 8,
            }
        )
    )
    cost = collect_cost(tmp_path)
    assert cost["available"] is True
    assert cost["total_tokens"] == 202000
    assert cost["time_minutes"] == 1.0


def test_collect_cost_missing_usage_is_unavailable(tmp_path: Path):
    assert collect_cost(tmp_path) == {"available": False}


def test_collect_effort_uses_usage_steps_and_interventions(tmp_path: Path):
    (tmp_path / "usage.json").write_text(json.dumps({"steps": 8}))
    effort = collect_effort(tmp_path, interventions=2)
    assert effort == {
        "steps": 8,
        "turns": None,
        "tool_calls": None,
        "stop_reason": None,
        "interventions": 2,
        "continuations": 0,
    }


def test_collect_effort_separates_work_from_harness_restarts(tmp_path: Path):
    """One invocation of 50 turns is 1 step — the two must not be conflated."""
    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "steps": 1,
                "turns": 50,
                "tool_calls": 120,
                "stop_reason": "completion_signal",
            }
        )
    )
    effort = collect_effort(tmp_path, interventions=0)
    assert effort["steps"] == 1
    assert effort["turns"] == 50
    assert effort["tool_calls"] == 120
    assert effort["stop_reason"] == "completion_signal"


def test_collect_effort_records_the_continuation_chain_depth(tmp_path: Path):
    """A continued run was steered by a human between steps; effort says so."""
    (tmp_path / "usage.json").write_text(json.dumps({"steps": 3}))
    effort = collect_effort(tmp_path, interventions=0, continuations=2)
    assert effort == {
        "steps": 3,
        "turns": None,
        "tool_calls": None,
        "stop_reason": None,
        "interventions": 0,
        "continuations": 2,
    }


def test_collect_effort_falls_back_to_step_logs(tmp_path: Path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "step-1.log").write_text("x")
    (steps / "step-2.log").write_text("y")
    (tmp_path / "usage.json").write_text(json.dumps({}))
    effort = collect_effort(tmp_path, interventions=None)
    assert effort["steps"] == 2


def test_build_validation_spec_substitutes_live_workspace(tmp_path, monkeypatch):
    # No archived copy on disk → the live workspace path is used.
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    case = _case(validation_commands=["BENCH_SMOKE=1 python {workspace}/main.py"])
    spec = build_validation_spec(case, "tetris-v1-K-001")
    assert spec["strategy"] == "smoke"
    assert spec["commands"] == [
        ["sh", "-c", "BENCH_SMOKE=1 python workspace/tetris-v1-K-001/main.py"]
    ]


def test_build_validation_spec_prefers_archived_workspace(tmp_path, monkeypatch):
    # --fresh runs archive + remove the live workspace; validation must target
    # the archived copy under data/runs/<id>/workspace when it exists.
    (tmp_path / "tetris-v1-K-001" / "workspace").mkdir(parents=True)
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    case = _case(validation_commands=["BENCH_SMOKE=1 python {workspace}/main.py"])
    spec = build_validation_spec(case, "tetris-v1-K-001")
    assert spec["commands"] == [
        [
            "sh",
            "-c",
            "BENCH_SMOKE=1 python data/runs/tetris-v1-K-001/workspace/main.py",
        ]
    ]


def test_parse_objective_surfaces_swebench_verdict():
    results = [
        {
            "command": "python /app/scripts/swebench_validate.py --run r1",
            "exit_code": 0,
            "output": (
                "→ Running F2P (1) + P2P (18) …\n"
                "SWEBENCH: resolved=True F2P=1/1 P2P=18/18\n"
                "PASSED tests/test_config.py::test_config_from_file_toml\n"
            ),
        }
    ]
    objective = parse_objective(results, "tests")
    assert objective["passed"] is True
    assert objective["summary"] == "resolved=True F2P=1/1 P2P=18/18"
    assert objective["checks"][0]["detail"] == "resolved=True F2P=1/1 P2P=18/18"


def test_build_validation_spec_skips_prose_notes():
    case = _case(validation_commands=["Run the game and observe the window."])
    spec = build_validation_spec(case, "r1")
    assert spec["commands"] == []
    assert spec["strategy"] == "manual"


def test_build_validation_spec_tests_strategy_for_bugfix():
    case = _case(type="bugfix", validation_commands=["pytest {workspace}/tests"])
    spec = build_validation_spec(case, "r1")
    assert spec["strategy"] == "tests"


def test_parse_objective_all_pass():
    objective = parse_objective(
        [{"command": "smoke", "exit_code": 0, "output": "ok"}], "smoke"
    )
    assert objective["passed"] is True
    assert objective["checks"][0]["passed"] is True


def test_parse_objective_any_failure_fails():
    objective = parse_objective(
        [
            {"command": "a", "exit_code": 0, "output": ""},
            {"command": "b", "exit_code": 1, "output": "boom"},
        ],
        "tests",
    )
    assert objective["passed"] is False
    assert "1/2" in objective["summary"]


def test_parse_objective_extracts_pytest_summary():
    output = "===== 12 passed, 1 failed in 3.2s ====="
    objective = parse_objective(
        [{"command": "pytest", "exit_code": 1, "output": output}], "tests"
    )
    assert "passed" in objective["checks"][0]["detail"]


def test_parse_objective_no_commands_is_manual():
    objective = parse_objective([], "manual")
    assert objective["passed"] is None
    assert objective["checks"] == []


def test_compute_composite_requires_functional_score():
    assert compute_composite({"passed": True}, {}) is None


def test_compute_composite_uses_functional_when_objective_passes():
    composite = compute_composite({"passed": True}, {"functional_score": 8})
    assert composite == 8.0


def test_compute_composite_caps_when_objective_fails():
    # A high manual score is not credible when the smoke/tests fail.
    composite = compute_composite({"passed": False}, {"functional_score": 9})
    assert composite == 1.0


def test_compute_composite_unknown_objective_does_not_cap():
    composite = compute_composite({"passed": None}, {"functional_score": 7})
    assert composite == 7.0


def _result(command: str, exit_code: int, output: str = "") -> dict:
    return {"command": command, "exit_code": exit_code, "output": output}


def test_a_timed_out_check_is_inconclusive_not_a_failure():
    """Exit 124 measured the lab, not the agent.

    All four runs of experiment `...-on--2` were recorded as functional
    failures on the strength of a `timeout` that never proved anything about
    the code. `passed` must say "we do not know", not "it does not work".
    """
    objective = parse_objective([_result("smoke", 124)], "smoke")
    assert objective["passed"] is None
    assert objective["checks"][0]["conclusive"] is False
    assert "inconclusive" in objective["summary"]


def test_a_real_failure_still_fails_even_beside_a_timeout():
    objective = parse_objective(
        [_result("assert", 1, "AssertionError"), _result("smoke", 124)], "smoke"
    )
    assert objective["passed"] is False


def test_all_checks_conclusive_and_passing_still_passes():
    objective = parse_objective([_result("a", 0), _result("b", 0)], "tests")
    assert objective["passed"] is True
    assert all(check["conclusive"] for check in objective["checks"])
    assert "inconclusive" not in objective["summary"]


def test_an_inconclusive_run_is_not_capped_at_one():
    """`compute_composite` caps a failing run at 1.0 — but this one did not fail.

    Capping on a timeout is how infrastructure entered the corpus as capability.
    """
    inconclusive = parse_objective([_result("smoke", 124)], "smoke")
    assert compute_composite(inconclusive, {"functional_score": 7}) == 7.0
    failed = parse_objective([_result("smoke", 1)], "smoke")
    assert compute_composite(failed, {"functional_score": 7}) == 1.0


def test_judge_evidence_names_the_command_its_code_and_its_verdict():
    """The judge used to receive eight words; give it the facts instead."""
    objective = parse_objective(
        [_result("pytest -q", 1, "1 failed, 2 passed"), _result("smoke", 124)], "tests"
    )
    text = describe_objective_for_judge(objective)
    assert "[FAIL] exit=1 pytest -q" in text
    # The reason rides along: "we never found out" and "we never found out
    # because the sandbox has no network" are different things to judge on.
    assert "[INCONCLUSIVE: timed out] exit=124 smoke" in text
    assert "neither confirms nor refutes" in text


def test_judge_evidence_is_silent_about_inconclusiveness_when_there_is_none():
    text = describe_objective_for_judge(parse_objective([_result("a", 0)], "tests"))
    assert "[PASS] exit=0 a" in text
    assert "INCONCLUSIVE" not in text


def test_billed_tokens_count_the_prompt_cache(tmp_path):
    """A harness is paid for in cache reads, not in `input_tokens`.

    Real numbers from the first bare-vs-full-harness experiment: input 89,
    output 8,553, cache_read 724,054. `total_tokens` keeps its historical
    meaning (ADR-0034); `billed_tokens` is the figure that moved with the cost.
    """
    from harness_lab_app.validation import collect_cost

    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 89,
                "output_tokens": 8553,
                "cache_read_tokens": 724054,
                "cache_creation_tokens": 126597,
                "cost_usd": 0.368,
            }
        )
    )

    cost = collect_cost(tmp_path)

    assert cost["total_tokens"] == 8642
    assert cost["billed_tokens"] == 89 + 8553 + 724054 + 126597
    assert cost["cache_read_tokens"] == 724054


def test_billed_tokens_are_absent_when_nothing_is_known(tmp_path):
    from harness_lab_app.validation import collect_cost

    (tmp_path / "usage.json").write_text(json.dumps({"cost_usd": 0.1}))

    cost = collect_cost(tmp_path)

    assert cost["total_tokens"] is None
    assert cost["billed_tokens"] is None


# --------------------------------------------------------------------------
# A target's gate runs inside the workspace (ADR-0062)
# --------------------------------------------------------------------------


def test_a_target_command_runs_inside_the_workspace(tmp_path, monkeypatch):
    """`make test` from a target row means *its* Makefile, not the lab's.

    Without the `cd` every target-paired run validated the lab: all eight runs
    of `audit-ml-python-base` recorded `0/2 checks passed` from the lab's own
    `make test`, and the experiment read as "the harness did not help".
    """
    (tmp_path / "run-1" / "workspace").mkdir(parents=True)
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    spec = build_validation_spec(_case(), "run-1", ["make test"])
    assert spec["commands"] == [
        ["sh", "-c", "cd data/runs/run-1/workspace && make test"]
    ]
    assert spec["cwd"] == "data/runs/run-1/workspace"


def test_a_target_command_renders_the_workspace_as_dot(tmp_path, monkeypatch):
    """It is already there; the lab-relative path would nest under itself."""
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    spec = build_validation_spec(_case(), "run-1", ["pytest {workspace}/tests"])
    assert spec["commands"][0][-1] == "cd workspace/run-1 && pytest ./tests"


def test_a_case_command_is_never_prefixed(tmp_path, monkeypatch):
    """Case commands anchor on {workspace} themselves, and some also use
    lab-relative paths a `cd` would break."""
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    case = _case(validation_commands=["python {workspace}/main.py"])
    spec = build_validation_spec(case, "run-1")
    assert spec["commands"] == [["sh", "-c", "python workspace/run-1/main.py"]]
    assert spec["cwd"] is None


# --------------------------------------------------------------------------
# A check the environment failed is not a verdict (ADR-0062)
# --------------------------------------------------------------------------


def test_a_missing_tool_reported_by_make_is_inconclusive():
    """make exits 2 when a recipe's binary is missing, so the code cannot tell.

    This is the exact shape that scored eight runs as agent failures: the
    hardened lane has no ruff on PATH, `make test` runs `format`, and the lab
    stored `0/2 checks passed`.
    """
    objective = parse_objective(
        [
            _result(
                "make test",
                2,
                "make[1]: *** [Makefile:98: format] Error 127\n"
                "make: *** [Makefile:149: test] Error 2",
            )
        ],
        "tests",
    )
    assert objective["passed"] is None
    assert objective["checks"][0]["conclusive"] is False
    assert objective["checks"][0]["inconclusive_reason"] == "not_found"
    assert "command not found" in objective["summary"]


def test_exit_127_is_inconclusive_even_without_output():
    """The dry run scripts exit codes and captures no output."""
    objective = parse_objective([_result("nope", 127)], "tests")
    assert objective["passed"] is None
    assert objective["checks"][0]["inconclusive_reason"] == "not_found"


def test_a_dns_failure_is_inconclusive():
    """`network_mode="none"` on the validate lane is deliberate; blaming the
    agent for it is not."""
    objective = parse_objective(
        [
            _result(
                "uv run pytest -x -q",
                2,
                "  Caused by: dns error\n"
                "  Caused by: failed to lookup address information: "
                "Temporary failure in name resolution",
            )
        ],
        "tests",
    )
    assert objective["passed"] is None
    assert objective["checks"][0]["inconclusive_reason"] == "no_network"
    assert "no network" in objective["summary"]


def test_no_such_file_on_exit_1_stays_a_real_failure():
    """The agent not having written the file is the whole point of the check."""
    objective = parse_objective(
        [_result("python main.py", 1, "No such file or directory: 'main.py'")],
        "smoke",
    )
    assert objective["passed"] is False
    assert objective["checks"][0]["conclusive"] is True


def test_a_not_found_string_inside_a_passing_output_is_ignored():
    objective = parse_objective(
        [_result("pytest", 0, "test_missing_key_is_not_found PASSED")], "tests"
    )
    assert objective["passed"] is True
    assert objective["checks"][0]["conclusive"] is True


def test_a_real_failure_beside_an_infrastructure_one_still_fails():
    """A sibling that could not run does not excuse an assertion that did."""
    objective = parse_objective(
        [_result("assert", 1, "AssertionError"), _result("make test", 127)], "tests"
    )
    assert objective["passed"] is False


def test_the_summary_counts_inconclusive_checks_by_reason():
    objective = parse_objective(
        [_result("ok", 0), _result("a", 127), _result("b", 127), _result("c", 124)],
        "tests",
    )
    assert "1/4 checks passed" in objective["summary"]
    assert "3 inconclusive (command not found (2), timed out)" in objective["summary"]


def test_a_summary_where_nothing_settled_does_not_lead_with_a_score():
    """`0/2 checks passed` is true, and reads as a verdict.

    It is what made two unmeasured runs look like two failures on the page —
    the reader saw the same headline as before the classification existed.
    """
    objective = parse_objective([_result("make test", 127), _result("b", 124)], "tests")
    assert objective["passed"] is None
    assert objective["summary"] == (
        "nothing measured: all 2 check(s) were inconclusive "
        "(command not found, timed out)"
    )
    assert "checks passed" not in objective["summary"]


def test_the_judge_reads_why_a_check_did_not_measure():
    objective = parse_objective([_result("uv run pytest", 127)], "tests")
    text = describe_objective_for_judge(objective)
    assert "[INCONCLUSIVE: command not found]" in text
    assert "settled nothing (command not found)" in text


# --- validation dependencies (ADR-0071) -----------------------------------------


def test_a_case_that_declares_validation_requirements_gets_a_setup_step(
    tmp_path, monkeypatch
):
    """eda-ml-iris (2026-10-23): ten runs failed `pytest` at collection because
    the clean, offline validation container had no pandas. The case names its
    graders' dependencies; the lab installs them, with network, before the
    offline checks run."""
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    case = _case(
        validation_commands=["pytest {workspace}/test_x.py -q"],
        metadata_json={
            "validation_requirements": ["pandas==2.2.3", "scikit-learn==1.5.2"]
        },
    )
    spec = build_validation_spec(case, "iris-K-001")
    setup = spec["setup"]
    assert len(setup) == 1
    script = setup[0][-1]
    assert "uv pip install" in script and "--target" in script
    assert "python -m pip install" in script  # fallback where there is no uv
    assert "pandas==2.2.3 scikit-learn==1.5.2;" in script
    deps = "data/runs/iris-K-001/validation-deps"
    assert deps in script
    # Every offline check sees the installed packages.
    assert all(
        f'PYTHONPATH="$PWD/{deps}' in command[-1] for command in spec["commands"]
    )


def test_a_case_without_validation_requirements_has_no_setup_step(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    spec = build_validation_spec(
        _case(validation_commands=["pytest {workspace}/t.py -q"]), "r-K-001"
    )
    assert spec["setup"] == []
    assert "PYTHONPATH" not in spec["commands"][0][-1]


def test_an_unsafe_requirement_is_refused_not_shell_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness_lab_app.validation.get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    case = _case(
        validation_commands=["pytest {workspace}/t.py -q"],
        metadata_json={"validation_requirements": ["pandas; rm -rf /", "numpy==2.1.3"]},
    )
    script = build_validation_spec(case, "r-K-001")["setup"][0][-1]
    assert "rm -rf /" not in script and "pandas" not in script
    assert " numpy==2.1.3;" in script


def test_a_missing_third_party_module_is_inconclusive():
    """No module named 'joblib' in the grader's environment says nothing about
    the agent's code."""
    output = (
        "ImportError while importing test module 'test_pipeline_iris_eda.py'.\n"
        "E   ModuleNotFoundError: No module named 'joblib'\n"
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!"
    )
    assert validation.classify_inconclusive(2, output) == "missing_dependency"


def test_a_missing_module_the_agent_should_have_written_is_still_its_failure():
    """clinic.py not written is the agent's failure — the whole point of the check."""
    output = "E   ModuleNotFoundError: No module named 'clinic'\n"
    assert validation.classify_inconclusive(1, output, own_modules={"clinic"}) is None


def test_a_module_from_the_workspace_is_never_called_a_missing_dependency(tmp_path):
    (tmp_path / "ml_pipeline.py").write_text("x = 1\n")
    output = "E   ModuleNotFoundError: No module named 'ml_pipeline'\n"
    own = validation.workspace_modules(tmp_path)
    assert "ml_pipeline" in own
    assert validation.classify_inconclusive(1, output, own_modules=own) is None
