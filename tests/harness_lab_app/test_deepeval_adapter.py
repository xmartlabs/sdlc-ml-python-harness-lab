"""The DeepEval seam: three metrics, and never a zero for something unmeasured.

DeepEval is an optional dependency and is not installed in this repository, so a
stand-in package is injected into ``sys.modules``. That is the point of the
adapter: the lab depends on a narrow surface (``ToolCorrectnessMetric``,
``GEval``, ``LLMTestCase``) and everything else is the lab's own.
"""

from __future__ import annotations

import json
import sys
import types
from typing import ClassVar

import pytest

from harness_lab_app import (
    deepeval_adapter,
    models as _models,  # noqa: F401  (registers the tables for `session`)
)
from harness_lab_app.deepeval_adapter import ADAPTER_VERSION, measure, render_trajectory
from harness_lab_app.evaluation import DeepEvalEvaluator, EvaluationContext

METRICS = ("task_completion", "step_efficiency", "tool_correctness")


def event(*blocks) -> str:
    """One claude stream-json assistant event."""
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def tool_use(name, **payload) -> dict:
    return {"type": "tool_use", "name": name, "input": payload}


def trajectory(**overrides):
    base = {
        "source": "claude_events",
        "task": "Fix the failing parser test.",
        "steps": [
            {"index": 1, "tool": "Read", "input": "src/parser.py"},
            {"index": 2, "tool": "Edit", "input": "src/parser.py"},
            {"index": 3, "tool": "Bash", "input": "pytest -q"},
        ],
        "step_count": 3,
        "truncated": False,
        "tools_called": ["Read", "Edit", "Bash"],
        "expected_tools": ["Edit", "Bash"],
        "final_output": "Fixed.",
    }
    base.update(overrides)
    return base


class FakeMetric:
    """Records how it was constructed, then reports a fixed score."""

    calls: ClassVar[list] = []

    def __init__(self, score=0.75, reason="looks fine", raises=None, **kwargs):
        self.score = score
        self.reason = reason
        self._raises = raises
        self.kwargs = kwargs
        self.measured_case = None

    def measure(self, case):
        if self._raises is not None:
            raise self._raises
        self.measured_case = case
        FakeMetric.calls.append(self)


def install_fake_deepeval(monkeypatch, *, tool_score=1.0, geval_score=0.8, raises=None):
    """Put a minimal, honest stand-in for DeepEval on the import path."""
    FakeMetric.calls = []

    class ToolCall:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return isinstance(other, ToolCall) and other.name == self.name

    class LLMTestCase:
        def __init__(self, input, actual_output, tools_called=(), expected_tools=()):
            self.input = input
            self.actual_output = actual_output
            self.tools_called = list(tools_called)
            self.expected_tools = list(expected_tools)

    class LLMTestCaseParams:
        INPUT = "input"
        ACTUAL_OUTPUT = "actual_output"

    test_case = types.ModuleType("deepeval.test_case")
    test_case.LLMTestCase = LLMTestCase
    test_case.ToolCall = ToolCall
    test_case.LLMTestCaseParams = LLMTestCaseParams

    metrics = types.ModuleType("deepeval.metrics")
    metrics.ToolCorrectnessMetric = lambda **kw: FakeMetric(
        score=tool_score, reason="tools matched", raises=raises, **kw
    )
    metrics.GEval = lambda **kw: FakeMetric(
        score=geval_score, reason="judged", raises=raises, **kw
    )

    package = types.ModuleType("deepeval")
    package.metrics = metrics
    package.test_case = test_case

    for name, module in {
        "deepeval": package,
        "deepeval.metrics": metrics,
        "deepeval.test_case": test_case,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return metrics


@pytest.fixture
def judge(monkeypatch):
    monkeypatch.setenv("DEEPEVAL_MODEL", "test-judge-model")
    return "test-judge-model"


def uninstall_deepeval(monkeypatch):
    """Make ``import deepeval`` fail the way an absent package does."""
    for name in list(sys.modules):
        if name == "deepeval" or name.startswith("deepeval."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "deepeval", None)


# --- skips: every one of these must yield no score, not a zero ------------


def test_no_trajectory_skips_every_metric_without_scoring_zero(judge):
    outcomes = measure(None)

    assert [outcome.name for outcome in outcomes] == list(METRICS)
    assert all(outcome.value is None for outcome in outcomes)
    assert all("no agent trajectory" in o.skipped_reason.lower() for o in outcomes)


def test_a_trajectory_with_no_steps_is_treated_as_no_trajectory(judge):
    outcomes = measure(trajectory(steps=[], step_count=0))
    assert all(not outcome.measured for outcome in outcomes)


def test_deepeval_absent_skips_with_an_actionable_reason(monkeypatch, judge):
    uninstall_deepeval(monkeypatch)

    outcomes = measure(trajectory())

    assert all(not outcome.measured for outcome in outcomes)
    assert all("not installed" in outcome.skipped_reason for outcome in outcomes)


def test_no_judge_model_skips_only_the_judged_metrics(monkeypatch):
    install_fake_deepeval(monkeypatch)
    monkeypatch.delenv("DEEPEVAL_MODEL", raising=False)
    monkeypatch.delenv("AUDITOR_MODEL", raising=False)

    by_name = {outcome.name: outcome for outcome in measure(trajectory())}

    # Tool correctness is deterministic — it needs no judge and still runs.
    assert by_name["tool_correctness"].measured
    assert not by_name["task_completion"].measured
    assert "DEEPEVAL_MODEL" in by_name["step_efficiency"].skipped_reason


def test_auditor_model_is_reused_as_the_judge(monkeypatch):
    install_fake_deepeval(monkeypatch)
    monkeypatch.delenv("DEEPEVAL_MODEL", raising=False)
    monkeypatch.setenv("AUDITOR_MODEL", "auditor-model")

    by_name = {outcome.name: outcome for outcome in measure(trajectory())}

    assert by_name["task_completion"].provenance["model"] == "auditor-model"


def test_a_case_declaring_no_expected_tools_skips_only_tool_correctness(
    monkeypatch, judge
):
    install_fake_deepeval(monkeypatch)

    by_name = {o.name: o for o in measure(trajectory(expected_tools=[]))}

    assert not by_name["tool_correctness"].measured
    assert "no expected tools" in by_name["tool_correctness"].skipped_reason
    assert by_name["task_completion"].measured


def test_a_raising_metric_is_a_skip_never_a_zero(monkeypatch, judge):
    install_fake_deepeval(monkeypatch, raises=RuntimeError("judge unreachable"))

    outcomes = measure(trajectory())

    assert all(outcome.value is None for outcome in outcomes)
    assert all("raised while measuring" in o.skipped_reason for o in outcomes)


# --- measurement and provenance -------------------------------------------


def test_a_supported_trajectory_scores_all_three_with_provenance(monkeypatch, judge):
    install_fake_deepeval(monkeypatch, tool_score=1.0, geval_score=0.8)

    by_name = {outcome.name: outcome for outcome in measure(trajectory())}

    assert by_name["tool_correctness"].value == 1.0
    assert by_name["task_completion"].value == 0.8
    assert by_name["step_efficiency"].value == 0.8

    judged = by_name["step_efficiency"].provenance
    assert judged["evaluator"] == "deepeval"
    assert judged["adapter_version"] == ADAPTER_VERSION
    assert judged["metric"] == "GEval"
    assert judged["model"] == judge
    # The rubric travels with the score: editing it makes old and new values
    # different quantities, and that has to be visible on the value.
    assert judged["rubric"] == deepeval_adapter.STEP_EFFICIENCY_RUBRIC

    deterministic = by_name["tool_correctness"].provenance
    assert deterministic["metric"] == "ToolCorrectnessMetric"
    assert deterministic["deterministic"] is True
    assert deterministic["expected_tools"] == ["Edit", "Bash"]


def test_the_judge_is_shown_the_trajectory_alongside_the_final_message(
    monkeypatch, judge
):
    install_fake_deepeval(monkeypatch)

    measure(trajectory())

    judged = [m for m in FakeMetric.calls if m.kwargs.get("criteria")]
    shown = judged[0].measured_case.actual_output
    assert "Fixed." in shown
    assert "2. Edit(src/parser.py)" in shown


def test_render_marks_the_gap_when_a_trajectory_was_clipped():
    rendered = render_trajectory(
        trajectory(
            steps=[{"index": 1, "tool": "Read", "input": "a.py"}],
            step_count=50,
            truncated=True,
        )
    )
    assert "49 steps omitted" in rendered


# --- the evaluator that wraps it ------------------------------------------


def test_evaluator_emits_registry_rows_for_measured_metrics(monkeypatch, judge):
    install_fake_deepeval(monkeypatch)
    evaluator = DeepEvalEvaluator()

    scores = evaluator.evaluate(EvaluationContext(run_id="r1", trajectory=trajectory()))

    assert sorted(score.name for score in scores) == sorted(METRICS)
    assert evaluator.skipped == {}
    assert evaluator.unavailable_reason == ""

    row = next(score for score in scores if score.name == "tool_correctness").as_row()
    # Storable, and carrying both kinds of provenance: what kind of claim it is
    # (the registry's `provenance`) and which instrument made it.
    assert row["category"] == "trajectory"
    assert row["provenance"] == "judgement"
    assert row["provenance_json"]["adapter_version"] == ADAPTER_VERSION
    assert row["value_num"] == 1.0


def test_evaluator_returns_nothing_and_explains_itself_without_a_trajectory(judge):
    evaluator = DeepEvalEvaluator()

    assert evaluator.evaluate(EvaluationContext(run_id="r1", trajectory=None)) == []
    assert set(evaluator.skipped) == set(METRICS)
    assert "no agent trajectory" in evaluator.unavailable_reason.lower()


# --- the score reaches storage with its provenance intact -----------------


def test_a_measured_trajectory_is_persisted_with_its_provenance(
    monkeypatch, tmp_path, session, judge
):
    """§12's other half: the score has to survive the trip to the database."""
    from types import SimpleNamespace

    from harness_lab_app import score_service as score_service_module
    from harness_lab_app.models import Case, CaseType, Run, RunConfig
    from harness_lab_app.score_service import ScoreService

    install_fake_deepeval(monkeypatch, tool_score=1.0, geval_score=0.6)

    session.add(
        Case(
            case_id="c1",
            name="Parser fix",
            type=CaseType.bugfix,
            description="Fix the failing parser test.",
            prompt_variants={"baseline": "Fix the failing parser test."},
            validation_commands=["pytest -q"],
            files_expected=["src/parser.py"],
        )
    )
    config = RunConfig(case_id="c1", prompt_variant="baseline")
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add(Run(run_id="run-1", run_config_id=config.id))
    session.commit()

    runs_dir = tmp_path / "runs"
    steps = runs_dir / "run-1" / "steps"
    steps.mkdir(parents=True)
    (steps / "step-01.events.jsonl").write_text(
        "\n".join(
            [
                event(tool_use("Edit", file_path="/app/src/parser.py")),
                event(tool_use("Bash", command="pytest -q")),
            ]
        )
    )
    monkeypatch.setattr(
        score_service_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs_dir, repo_root=tmp_path),
    )

    stored = ScoreService(session).evaluate_run("run-1", profile="with_trajectory")
    by_name = {score.name: score for score in stored}

    assert by_name["task_completion"].value_num == 0.6
    assert by_name["task_completion"].category == "trajectory"
    assert by_name["task_completion"].provenance == "judgement"
    assert by_name["task_completion"].provenance_json["adapter_version"] == (
        ADAPTER_VERSION
    )
    # Expected tools were derived from what the case declares, not guessed.
    assert by_name["tool_correctness"].provenance_json["expected_tools"] == [
        "Edit",
        "Bash",
    ]


def test_a_run_with_no_recorded_steps_stores_no_trajectory_scores(
    monkeypatch, tmp_path, session, judge
):
    from types import SimpleNamespace

    from harness_lab_app import score_service as score_service_module
    from harness_lab_app.models import Case, CaseType, Run, RunConfig
    from harness_lab_app.score_service import ScoreService

    install_fake_deepeval(monkeypatch)

    session.add(Case(case_id="c1", name="Quiet", type=CaseType.bugfix))
    config = RunConfig(case_id="c1", prompt_variant="baseline")
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add(Run(run_id="run-2", run_config_id=config.id))
    session.commit()

    runs_dir = tmp_path / "runs"
    (runs_dir / "run-2").mkdir(parents=True)
    monkeypatch.setattr(
        score_service_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs_dir, repo_root=tmp_path),
    )

    stored = ScoreService(session).evaluate_run("run-2", profile="with_trajectory")

    # No zeros stood in for the metrics that could not be measured.
    assert not set(METRICS) & {score.name for score in stored}
