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

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
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


def _env_or_dotenv(name: str) -> str:
    """A setting from the process env, falling back to the repo's `.env`.

    The API and worker containers do not export `.env` into their environment;
    every other in-process LLM caller (the auditor, the SWE judge) reads it
    through `read_env_file_value`, so the DeepEval judge does the same.
    """
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        from harness_lab_app.config import read_env_file_value

        return (read_env_file_value(name) or "").strip()
    except Exception:
        return ""


def _gateway_config() -> tuple[str, str]:
    """(base_url, token) for the OpenAI-compatible gateway, or ("", "")."""
    base = _env_or_dotenv("GATEWAY_BASE_URL")
    token = _env_or_dotenv("GATEWAY_TOKEN")
    if not base or not token:
        return ("", "")
    if Path("/.dockerenv").exists():
        try:
            from harness_lab_app.config import get_settings
            from harness_lab_app.executors import rewrite_localhost

            base = rewrite_localhost(base, get_settings().docker_host_alias)
        except Exception:
            pass
    return (base.rstrip("/"), token)


#: Setting DEEPEVAL_MODEL to one of these turns the judged metrics off — the
#: cost lever for a lab that wants deterministic scores only.
_DISABLED = frozenset({"none", "off", "disabled", "0", "false"})


def judge_model() -> str:
    """The model DeepEval judges with, or "" when judging is off.

    Resolution order, each level read from the env and then from `.env`:

    1. ``DEEPEVAL_MODEL`` — explicit, and ``none``/``off`` disables judging.
    2. ``AUDITOR_MODEL`` — reuse the auditor's model rather than introducing a
       second judge: two LLM judges on one run, configured independently, is a
       way to produce two incomparable answers to one question.
    3. The auditor's *default* model, when the gateway is configured — so a lab
       that already audits through its gateway gets trajectory judging with no
       extra configuration at all.
    """
    explicit = _env_or_dotenv("DEEPEVAL_MODEL")
    if explicit:
        return "" if explicit.lower() in _DISABLED else explicit
    auditor = _env_or_dotenv("AUDITOR_MODEL")
    if auditor:
        return auditor
    base, token = _gateway_config()
    if base and token:
        try:
            from harness_lab_app.harness_lifecycle.judge import DEFAULT_JUDGE_MODEL

            return DEFAULT_JUDGE_MODEL
        except Exception:
            return "gpt-5.4-mini"
    return ""


def _extract_json(text: str) -> str:
    """The JSON object inside a judge's reply, shorn of fences and prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _gateway_chat(base: str, token: str, model: str, prompt: str) -> str:
    """One OpenAI-compatible chat call against the lab's gateway."""
    from harness_lab_app.gateway_models import GATEWAY_USER_AGENT, normalize_model_name

    payload = {
        "model": normalize_model_name(model),
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare bans the default urllib user-agent (error 1010).
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:  # nosec B310
        data = json.loads(response.read())
    return str(data["choices"][0]["message"]["content"])


def _gateway_judge(model: str) -> Any:
    """A DeepEval judge that speaks to the lab's gateway, or the bare name.

    DeepEval's default judge path goes straight to OpenAI with OPENAI_API_KEY.
    This lab's judge models are served by its own gateway — the same endpoint
    and credentials the auditor uses — so the judge has to be a custom model.
    When the gateway is not configured the bare model name is returned and
    DeepEval routes it however it normally would.
    """
    base, token = _gateway_config()
    if not base or not token:
        return model
    try:
        from deepeval.models.base_model import DeepEvalBaseLLM
    except ImportError:
        return model

    class _GatewayJudge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            self._name = model
            super().__init__(model)

        def load_model(self) -> None:  # pragma: no cover - trivial
            return None

        def get_model_name(self) -> str:
            return self._name

        def generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
            text = _gateway_chat(base, token, self._name, prompt)
            if schema is None:
                return text
            cleaned = _extract_json(text)
            try:
                return schema.model_validate_json(cleaned)
            except Exception:
                return schema(**json.loads(cleaned))

        async def a_generate(self, prompt: str, schema: Any = None, **kw: Any) -> Any:
            return self.generate(prompt, schema=schema, **kw)

    return _GatewayJudge()


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

    judge = _gateway_judge(model)
    outcomes = []
    for name, rubric in _GEVAL:
        metric = metrics.GEval(
            name=name,
            criteria=rubric,
            evaluation_params=[
                test_case.LLMTestCaseParams.INPUT,
                test_case.LLMTestCaseParams.ACTUAL_OUTPUT,
            ],
            model=judge,
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
                    {
                        "metric": "GEval",
                        "model": model,
                        "rubric": rubric,
                        "judge_transport": (
                            "gateway" if judge is not model else "provider-default"
                        ),
                    }
                ),
            )
        )
    return outcomes
