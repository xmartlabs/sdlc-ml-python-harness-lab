"""DeepEval, used as an adapter and never as a platform.

The lab owns the evaluation model: which metrics exist, what they mean, which
direction is better and what version they are on all live in
:mod:`harness_lab_app.scores`. DeepEval computes three of those numbers. A
deployment without it is missing three scores; it is not a different lab.

Everything here is written around one rule: **a measurement that did not happen
is not a zero.** Each metric can be skipped for its own stated reason — no
DeepEval, no judge model, no trajectory, no declared expectation — and a skip
returns no score at all, so an aggregate can never read "we could not measure
this" as "this did badly".

Two honest deviations from DeepEval's own defaults, recorded because they change
what the numbers mean:

*``task_completion`` is judged by ``GEval``, not ``TaskCompletionMetric``.*
DeepEval's task-completion metric reads a live trace produced by its ``@observe``
decorators. The lab evaluates archived runs that executed in a container hours
earlier, so there is no live trace to read; the rubric below asks the same
question of the recorded trajectory instead.

*``step_efficiency`` has no DeepEval metric at all,* so it is a ``GEval`` rubric
too. Both are declared ``judgement`` in the registry, which is exactly what they
are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

#: Bumped when a rubric, a mapping or a metric choice changes. It travels with
#: every score this adapter produces, because a rubric edit makes the old and
#: new numbers different quantities.
ADAPTER_VERSION = "v1"

TASK_COMPLETION_RUBRIC = (
    "Given the task and the agent's recorded trajectory of tool calls, judge "
    "whether the agent actually accomplished the task. Reward a trajectory "
    "whose final state plausibly satisfies the task. Penalise one that stops "
    "early, abandons the task, or ends with the work still undone. Judge the "
    "trajectory that is shown; do not assume steps that are not recorded."
)

STEP_EFFICIENCY_RUBRIC = (
    "Judge whether the recorded steps were necessary to reach the outcome. "
    "Penalise repeated identical reads, edits later undone, aimless searching, "
    "and re-running the same failing command without changing anything. Do not "
    "penalise a long trajectory that is long because the task is large."
)


@dataclass
class MetricOutcome:
    """One metric's result, or the reason there isn't one."""

    name: str
    value: float | None = None
    reason: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    skipped_reason: str = ""

    @property
    def measured(self) -> bool:
        return self.value is not None


def deepeval_version() -> str:
    try:
        from importlib.metadata import version

        return version("deepeval")
    # Absence and a broken metadata entry are the same answer here: no version.
    except Exception:
        return ""


def judge_model() -> str:
    """The model name DeepEval should judge with, or "" when none is configured.

    Reuses the auditor's model rather than introducing a second one: two LLM
    judges on the same run, configured independently, is a way to produce two
    incomparable answers to one question.
    """
    return (
        os.environ.get("DEEPEVAL_MODEL") or os.environ.get("AUDITOR_MODEL") or ""
    ).strip()


def _import_deepeval() -> Any | None:
    try:
        from deepeval import metrics, test_case

        return (metrics, test_case)
    except ImportError:
        return None


def render_trajectory(trajectory: dict[str, Any]) -> str:
    """The trajectory as the judge reads it: numbered calls, one per line."""
    lines = [
        f"{step['index']}. {step['tool']}({step.get('input', '')})".rstrip()
        for step in trajectory.get("steps", [])
    ]
    if trajectory.get("truncated"):
        total = trajectory.get("step_count", len(lines))
        lines.insert(
            len(lines) // 2,
            f"... [{total - len(lines)} steps omitted from the middle] ...",
        )
    return "\n".join(lines)


def measure(trajectory: dict[str, Any] | None) -> list[MetricOutcome]:
    """Run the three trajectory metrics, skipping each for its own reason."""
    names = ("task_completion", "step_efficiency", "tool_correctness")

    def skip_all(reason: str) -> list[MetricOutcome]:
        return [MetricOutcome(name=name, skipped_reason=reason) for name in names]

    if not trajectory or not trajectory.get("steps"):
        return skip_all(
            "No agent trajectory was recorded for this run, so trajectory "
            "metrics have nothing to read."
        )

    modules = _import_deepeval()
    if modules is None:
        return skip_all(
            "DeepEval is not installed; trajectory scores were not computed. "
            "Install the `deepeval` extra to enable them."
        )
    metrics, test_case = modules

    rendered = render_trajectory(trajectory)
    case = test_case.LLMTestCase(
        input=trajectory.get("task") or "(the run's prompt was not recorded)",
        actual_output=trajectory.get("final_output") or rendered,
        tools_called=[
            test_case.ToolCall(name=name) for name in trajectory.get("tools_called", [])
        ],
        expected_tools=[
            test_case.ToolCall(name=name)
            for name in trajectory.get("expected_tools", [])
        ],
    )

    outcomes = [
        _tool_correctness(metrics, case, trajectory),
        *_judged(metrics, test_case, case, rendered),
    ]
    return sorted(outcomes, key=lambda outcome: names.index(outcome.name))


def _base_provenance(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    provenance = {
        "evaluator": "deepeval",
        "adapter_version": ADAPTER_VERSION,
        "deepeval_version": deepeval_version(),
    }
    provenance.update(extra or {})
    return provenance


def _tool_correctness(metrics: Any, case: Any, trajectory: dict[str, Any]):
    if not trajectory.get("expected_tools"):
        return MetricOutcome(
            name="tool_correctness",
            skipped_reason=(
                "The case declares no expected tools, so there is nothing to "
                "judge correctness against."
            ),
        )
    metric = metrics.ToolCorrectnessMetric()
    try:
        metric.measure(case)
    # A judge that fails has not scored zero; it has not scored.
    except Exception as error:
        return MetricOutcome(
            name="tool_correctness",
            skipped_reason=f"DeepEval raised while measuring: {error!r}",
        )
    return MetricOutcome(
        name="tool_correctness",
        value=float(metric.score),
        reason=str(getattr(metric, "reason", "") or ""),
        provenance=_base_provenance(
            {
                "metric": "ToolCorrectnessMetric",
                "expected_tools": list(trajectory["expected_tools"]),
                "deterministic": True,
            }
        ),
    )


_GEVAL = (
    ("task_completion", TASK_COMPLETION_RUBRIC),
    ("step_efficiency", STEP_EFFICIENCY_RUBRIC),
)


def _judged(metrics: Any, test_case: Any, case: Any, rendered: str):
    model = judge_model()
    if not model:
        reason = (
            "No judge model is configured (set DEEPEVAL_MODEL or AUDITOR_MODEL); "
            "LLM-judged trajectory metrics were not computed."
        )
        return [MetricOutcome(name=name, skipped_reason=reason) for name, _ in _GEVAL]

    # The judge reads the trajectory, so it has to be in the field the judge is
    # pointed at. `actual_output` already holds it when the run recorded no
    # closing message; when it does, append rather than replace it.
    judged_case = test_case.LLMTestCase(
        input=case.input,
        actual_output=(
            case.actual_output
            if case.actual_output == rendered
            else f"{case.actual_output}\n\n--- recorded trajectory ---\n{rendered}"
        ),
        tools_called=case.tools_called,
        expected_tools=case.expected_tools,
    )

    outcomes = []
    for name, rubric in _GEVAL:
        metric = metrics.GEval(
            name=name,
            criteria=rubric,
            evaluation_params=[
                test_case.LLMTestCaseParams.INPUT,
                test_case.LLMTestCaseParams.ACTUAL_OUTPUT,
            ],
            model=model,
        )
        try:
            metric.measure(judged_case)
        # A judge that fails has not scored zero; it has not scored.
        except Exception as error:
            outcomes.append(
                MetricOutcome(
                    name=name,
                    skipped_reason=f"DeepEval raised while measuring: {error!r}",
                )
            )
            continue
        outcomes.append(
            MetricOutcome(
                name=name,
                value=float(metric.score),
                reason=str(getattr(metric, "reason", "") or ""),
                provenance=_base_provenance(
                    {"metric": "GEval", "model": model, "rubric": rubric}
                ),
            )
        )
    return outcomes
