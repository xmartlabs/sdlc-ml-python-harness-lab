"""Evaluators: turn a completed run's artifacts into declared, versioned scores.

Each evaluator owns one level of the evaluation model and nothing else, so a
level can be added, replaced or switched off without disturbing the others — the
reason DeepEval can later be an adapter rather than a platform.

Every value is emitted against a definition in :mod:`harness_lab_app.scores`.
That is enforced, not conventional: an undeclared metric has no direction, no
provenance and no version, so nothing downstream can read it correctly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, ClassVar

from harness_lab_app.scores import get_definition

_DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_DIFF_ADD = re.compile(r"^\+(?!\+\+ )", re.MULTILINE)
_DIFF_DEL = re.compile(r"^-(?!-- )", re.MULTILINE)


@dataclass
class ScoreValue:
    name: str
    value: float | bool | str | None
    reason: str = ""
    confidence: float | None = None

    def as_row(self) -> dict[str, Any]:
        """Materialize against the registry, so nothing undeclared is stored."""
        definition = get_definition(self.name)
        row: dict[str, Any] = {
            "name": definition.name,
            "version": definition.version,
            "category": definition.category,
            "provenance": definition.provenance,
            "source": definition.source,
            "reason": self.reason,
            "confidence": self.confidence,
            "value_num": None,
            "value_bool": None,
            "value_text": None,
        }
        if isinstance(self.value, bool):
            row["value_bool"] = self.value
        elif isinstance(self.value, (int, float)):
            row["value_num"] = float(self.value)
        elif self.value is not None:
            row["value_text"] = str(self.value)
        return row


@dataclass
class EvaluationContext:
    """Everything the evaluators read. Assembled once, by the service."""

    run_id: str
    validation: dict[str, Any] | None = None
    audit_findings: dict[str, Any] | None = None
    attribution: dict[str, Any] | None = None
    diff_text: str = ""
    # Recorded agent trajectory (steps, tool calls) for the trajectory level.
    trajectory: dict[str, Any] | None = None
    files_expected: tuple[str, ...] = ()
    files_forbidden: tuple[str, ...] = ()


def _fold(text: str) -> str:
    """Lowercase and strip accents, so "útil" and "util" are one verdict."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def changed_files(diff_text: str) -> list[str]:
    """Paths a unified diff touches."""
    return sorted({match.strip() for match in _DIFF_FILE.findall(diff_text or "")})


def path_matches(path: str, pattern: str) -> bool:
    """Prefix/suffix-tolerant match for a declared surface.

    A case names ``src/api/customers.py`` or ``migrations/``; the diff names a
    repo-relative path. Exact equality would miss both a directory rule and a
    path the repository nests one level deeper, so the rule is: a directory
    pattern matches its subtree, and a file pattern matches any path ending in
    it.
    """
    pattern = pattern.strip().lstrip("./")
    if not pattern:
        return False
    if pattern.endswith("/"):
        return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
    return path == pattern or path.endswith(f"/{pattern}")


class DeterministicEvaluator:
    """Level 1 — did it work, and did it stay where it was supposed to?"""

    id = "deterministic"

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        scores: list[ScoreValue] = []
        objective = (context.validation or {}).get("objective") or {}
        checks = objective.get("checks") or []
        if objective.get("passed") is not None:
            scores.append(
                ScoreValue(
                    "task_validation_pass",
                    bool(objective["passed"]),
                    reason=objective.get("summary", ""),
                )
            )
        if checks:
            passed = sum(1 for check in checks if check.get("passed"))
            scores.append(
                ScoreValue(
                    "test_pass_rate",
                    passed / len(checks),
                    reason=f"{passed}/{len(checks)} checks passed",
                )
            )

        touched = changed_files(context.diff_text)
        if context.files_expected:
            hit = [
                pattern
                for pattern in context.files_expected
                if any(path_matches(path, pattern) for path in touched)
            ]
            scores.append(
                ScoreValue(
                    "expected_files_modified",
                    len(hit) / len(context.files_expected),
                    reason=f"{len(hit)}/{len(context.files_expected)} expected paths touched",
                )
            )
        if context.files_forbidden:
            violations = [
                path
                for path in touched
                if any(
                    path_matches(path, pattern) for pattern in context.files_forbidden
                )
            ]
            scores.append(
                ScoreValue(
                    "forbidden_files_modified",
                    float(len(violations)),
                    reason=", ".join(violations[:5]) if violations else "none",
                )
            )
        return scores


class EfficiencyEvaluator:
    """Level 2 — what it cost to get there."""

    id = "efficiency"

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        validation = context.validation or {}
        cost = validation.get("cost") or {}
        effort = validation.get("effort") or {}
        scores: list[ScoreValue] = []
        for name, value in (
            ("total_tokens", cost.get("total_tokens")),
            ("cost_usd", cost.get("cost_usd")),
            ("wall_time_minutes", cost.get("time_minutes")),
            ("agent_steps", effort.get("steps")),
        ):
            if value is not None:
                scores.append(ScoreValue(name, float(value)))

        if context.diff_text:
            added = len(_DIFF_ADD.findall(context.diff_text))
            removed = len(_DIFF_DEL.findall(context.diff_text))
            touched = changed_files(context.diff_text)
            scores.append(
                ScoreValue(
                    "diff_lines_changed",
                    float(added + removed),
                    reason=f"+{added} -{removed}",
                )
            )
            scores.append(ScoreValue("files_changed", float(len(touched))))
        return scores


class HarnessBehaviorEvaluator:
    """Level 4 — what the auditor judged about the harness's own contribution.

    Emits **judgements**, labelled as such. The auditor reads logs; it does not
    observe skill invocation, so these are defensible opinions and must never be
    rendered beside a test result as though they were the same kind of thing.
    """

    id = "harness_audit"

    # The auditor prompt is written in Spanish and emits verdicts like "útil".
    # Matching is done on an accent-stripped, lowercased form so "útil" and
    # "util" are the same verdict — without that, every Spanish "útil" fell
    # through to "unclear" and skill_adherence silently under-reported.
    _USEFUL: ClassVar[set[str]] = {"util", "useful", "used"}
    _REDUNDANT: ClassVar[set[str]] = {
        "redundante",
        "redundant",
        "unused",
        "no usado",
        "no_usado",
    }

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        items = (context.audit_findings or {}).get("items") or []
        if not items:
            return []
        useful = sum(1 for item in items if self._bucket(item) == "useful")
        redundant = sum(1 for item in items if self._bucket(item) == "redundant")
        scores = [
            ScoreValue("harness_items_useful", float(useful)),
            ScoreValue("harness_items_redundant", float(redundant)),
            ScoreValue(
                "skill_adherence",
                useful / len(items),
                reason=f"{useful}/{len(items)} available items judged used",
            ),
        ]

        attribution = context.attribution or {}
        read = attribution.get("governance_docs_read")
        available = attribution.get("governance_docs_available")
        if read is not None and available:
            scores.append(
                ScoreValue(
                    "governance_read_coverage",
                    len(read) / len(available)
                    if isinstance(read, list)
                    else float(read) / float(len(available)),
                    reason="parsed from step logs (a lower bound)",
                )
            )
        return scores

    def _bucket(self, item: dict[str, Any]) -> str:
        verdict = _fold(str(item.get("verdict", "")))
        if any(token in verdict for token in self._USEFUL):
            return "useful"
        if any(token in verdict for token in self._REDUNDANT):
            return "redundant"
        return "unclear"


class DeepEvalEvaluator:
    """Level 3 — trajectory metrics, computed by DeepEval when it is installed.

    An **adapter**, deliberately not a platform. The lab owns the evaluation
    model: DeepEval computes three of the numbers in it, against definitions
    this repository declares, and a deployment without it is missing three
    scores rather than behaving differently.

    A missing dependency is reported, never silently treated as a zero score —
    "we did not measure this" and "this scored badly" are different claims and
    an aggregate must never confuse them.
    """

    id = "deepeval"

    #: Set by :meth:`evaluate` so callers can explain an empty result.
    unavailable_reason = ""

    def _metrics(self):
        try:
            from deepeval.metrics import (
                TaskCompletionMetric,
                ToolCorrectnessMetric,
            )
        except ImportError:
            return None
        return {
            "task_completion": TaskCompletionMetric,
            "tool_correctness": ToolCorrectnessMetric,
        }

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        metrics = self._metrics()
        if metrics is None:
            self.unavailable_reason = (
                "DeepEval is not installed; trajectory scores were not computed. "
                "Install the `deepeval` extra to enable them."
            )
            return []
        trajectory = (context.trajectory or {}).get("steps") or []
        if not trajectory:
            self.unavailable_reason = (
                "No agent trajectory was recorded for this run, so trajectory "
                "metrics have nothing to read."
            )
            return []
        # Wiring the recorded trajectory into DeepEval's test-case shape is the
        # remaining work; the seam, the declarations and the honest empty result
        # are what this phase commits to.
        self.unavailable_reason = (
            "DeepEval is installed but the trajectory adapter is not wired yet."
        )
        return []


# Named sets of evaluators. A profile is what an experiment declares, so two
# arms are always scored by the same instruments.
PROFILES: dict[str, tuple[str, ...]] = {
    "default": ("deterministic", "efficiency", "harness_audit"),
    "with_trajectory": ("deterministic", "efficiency", "harness_audit", "deepeval"),
    "outcome_only": ("deterministic",),
    "no_judgement": ("deterministic", "efficiency"),
}

EVALUATORS = {
    evaluator.id: evaluator
    for evaluator in (
        DeterministicEvaluator(),
        EfficiencyEvaluator(),
        HarnessBehaviorEvaluator(),
        DeepEvalEvaluator(),
    )
}


def run_profile(profile: str, context: EvaluationContext) -> list[ScoreValue]:
    """Run every evaluator in ``profile`` against ``context``."""
    if profile not in PROFILES:
        raise KeyError(
            f"Unknown evaluation profile '{profile}'. Known: "
            f"{', '.join(sorted(PROFILES))}."
        )
    scores: list[ScoreValue] = []
    for evaluator_id in PROFILES[profile]:
        scores.extend(EVALUATORS[evaluator_id].evaluate(context))
    return scores
