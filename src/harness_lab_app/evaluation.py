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
from dataclasses import dataclass, field
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
    # Who computed it and with what. `provenance` on the definition says what
    # *kind* of claim this is; this says which instrument made it, so a rubric
    # or judge-model change is visible on the value rather than only in a
    # changelog.
    provenance_detail: dict[str, Any] = field(default_factory=dict)

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
            "provenance_json": dict(self.provenance_detail),
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
    # The skills this run's experiment is about and this run could see:
    # staged (`skills_extra`), asked for (`skills_invoke`), or removed in a
    # sibling arm. Empty for a run no skill question is being asked of.
    skills_under_test: tuple[str, ...] = ()


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
        # Only checks that actually produced a verdict. A timed-out or killed
        # command measured the harness, not the code, and averaging it in as a
        # zero is infrastructure entering the corpus as capability (ADR-0046).
        conclusive = [check for check in checks if check.get("conclusive") is not False]
        if conclusive:
            passed = sum(1 for check in conclusive if check.get("passed"))
            reason = f"{passed}/{len(conclusive)} checks passed"
            skipped = len(checks) - len(conclusive)
            if skipped:
                reason += f" ({skipped} inconclusive, excluded)"
            scores.append(
                ScoreValue("test_pass_rate", passed / len(conclusive), reason=reason)
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


class RubricJudgeEvaluator:
    """The judge's rubric level, as a score row rather than a run field only.

    Until now the judge's number lived in ``Validation.subjective`` and
    ``Run.score`` and never reached the experiment summary — so two equivalent
    implementations scored 10 and 2 on identical evidence, and nothing on the
    experiment page could show it. Recorded as JUDGEMENT provenance: it rides
    in the deltas table, never in the success rate.
    """

    id = "llm_judge"

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        validation = context.validation or {}
        subjective = validation.get("subjective") or {}
        if subjective.get("score_source") != "llm_judge":
            return []
        score = subjective.get("functional_score")
        if score is None:
            return []
        notes = str(subjective.get("notes") or "")
        return [ScoreValue("rubric_score", float(score), reason=notes[:400])]


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
            ("billed_tokens", cost.get("billed_tokens")),
            ("cache_read_tokens", cost.get("cache_read_tokens")),
            ("output_tokens", cost.get("output_tokens")),
            ("cost_usd", cost.get("cost_usd")),
            ("wall_time_minutes", cost.get("time_minutes")),
            ("model_minutes", cost.get("model_minutes")),
            ("agent_steps", effort.get("steps")),
            # The work measures. `agent_steps` counts harness restarts, so on
            # its own it reads a loop-protocol difference as an agent
            # difference; these two do not.
            ("agent_turns", effort.get("turns")),
            ("agent_tool_calls", effort.get("tool_calls")),
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


class StepRewardEvaluator:
    """Level 2b — which step made the progress, and what came after (ADR-0076).

    Reads the trajectory the validation service built by grading every step's
    snapshot with the case's own checks. Emits nothing for a run that left no
    per-step record: an old run is "not measured", never "zero".
    """

    id = "step_reward"

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        objective = (context.validation or {}).get("objective") or {}
        trajectory = objective.get("per_step") or {}
        if not trajectory.get("measured"):
            return []
        steps = trajectory.get("steps") or []
        curve = " → ".join(
            "?" if step.get("progress") is None else f"{step['progress']:.0%}"
            for step in steps
        )
        detail = {
            "sources": trajectory.get("sources", []),
            "unmeasured_steps": trajectory.get("unmeasured_steps", 0),
        }
        scores = [
            ScoreValue(
                "progress_auc",
                float(trajectory["progress_auc"]),
                reason=f"progress per step: {curve}",
                provenance_detail=detail,
            ),
            ScoreValue(
                "step_regressions",
                float(trajectory.get("step_regressions") or 0),
                reason=f"progress per step: {curve}",
                provenance_detail=detail,
            ),
        ]
        resolved_at = trajectory.get("steps_to_resolve")
        if resolved_at is not None:
            scores.append(
                ScoreValue(
                    "steps_to_resolve",
                    float(resolved_at),
                    reason=f"all checks passing after step {resolved_at} of "
                    f"{len(steps)}",
                    provenance_detail=detail,
                )
            )
            cost = trajectory.get("cost_to_resolve_usd")
            if cost is not None:
                scores.append(
                    ScoreValue(
                        "cost_to_resolve_usd",
                        float(cost),
                        reason=f"spend through step {resolved_at}",
                        provenance_detail=detail,
                    )
                )
            # Money first; tokens when the run was free (self-hosted), so a
            # local model's wasted tail is still visible.
            share = trajectory.get("post_resolution_cost_share")
            basis = "cost_usd"
            if share is None:
                share = trajectory.get("post_resolution_token_share")
                basis = "billed_tokens"
            if share is not None:
                scores.append(
                    ScoreValue(
                        "post_resolution_cost_share",
                        float(share),
                        reason=f"{share:.0%} of {basis} spent after step {resolved_at}",
                        provenance_detail=detail | {"basis": basis},
                    )
                )
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
                    reason=(
                        f"parsed from step logs (a lower bound); denominator is "
                        f"the run's {attribution.get('governance_source') or 'own'}"
                        f" governance surface"
                    ),
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


class SkillUsageEvaluator:
    """Level 4 — were the skills under test actually opened? (ADR-0041)

    An **observation**, parsed from the step record: a ``Skill`` tool call
    naming the skill, or any read of its ``SKILL.md``. It exists because a
    with-skills arm whose agent never touched the skill compares a menu, not a
    treatment — and nothing else in the pipeline can tell the two apart.

    Emits nothing when the run is not about any skill, and nothing when no
    trajectory was recorded: "not observed" must not be stored as "not used".
    """

    id = "skill_usage"

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        under_test = list(context.skills_under_test)
        if not under_test or context.trajectory is None:
            return []
        counts: dict[str, int] = dict(context.trajectory.get("skill_invocations") or {})
        # Calls the harness rejected. Not invocations — the agent got nothing
        # back — but the one fact that turns "never opened" into "could not
        # open": a skill the CLI never registered fails every call this way.
        errors: dict[str, int] = {
            name: count
            for name, count in dict(
                context.trajectory.get("skill_call_errors") or {}
            ).items()
            if name in under_test
        }
        invoked = [name for name in under_test if counts.get(name, 0) > 0]
        total = sum(counts.get(name, 0) for name in under_test)
        detail = {
            "skills_under_test": under_test,
            "invoked": invoked,
            "failed_calls": errors,
            "source": context.trajectory.get("source", ""),
        }
        failed_note = (
            "; "
            + ", ".join(
                f"{name}: {count} call(s) failed" for name, count in errors.items()
            )
            + " — the harness rejected the skill (not registered?)"
            if errors
            else ""
        )
        return [
            ScoreValue(
                "skill_invoked",
                len(invoked) / len(under_test),
                reason=(
                    f"{', '.join(invoked) if invoked else 'none'} of "
                    f"{', '.join(under_test)} invoked (Skill call or SKILL.md "
                    f"read in the step record){failed_note}"
                ),
                provenance_detail=detail,
            ),
            ScoreValue(
                "skill_invocations",
                float(total),
                reason=(
                    ", ".join(f"{name} x{counts[name]}" for name in invoked) or "none"
                )
                + failed_note,
                provenance_detail=detail,
            ),
        ]


class DeepEvalEvaluator:
    """Level 3 — trajectory metrics, computed by DeepEval when it is installed.

    An **adapter**, deliberately not a platform. The lab owns the evaluation
    model: DeepEval computes three of the numbers in it, against definitions
    this repository declares, and a deployment without it is missing three
    scores rather than behaving differently.

    A missing dependency, a missing judge model, a run with no recorded
    trajectory and a case with no declared expectation are all reported as
    *skips*, never as zero scores — "we did not measure this" and "this scored
    badly" are different claims and an aggregate must never confuse them. The
    reasons are readable on :attr:`skipped`, one per metric.
    """

    id = "deepeval"

    def __init__(self) -> None:
        #: Set by :meth:`evaluate` so callers can explain an empty result.
        self.unavailable_reason: str = ""
        #: ``{metric name: why it produced no value}`` for the last evaluation.
        self.skipped: dict[str, str] = {}

    def evaluate(self, context: EvaluationContext) -> list[ScoreValue]:
        from harness_lab_app.deepeval_adapter import measure

        outcomes = measure(context.trajectory)
        self.skipped = {
            outcome.name: outcome.skipped_reason
            for outcome in outcomes
            if not outcome.measured
        }
        # One line for a caller that only wants to say why the section is empty.
        self.unavailable_reason = (
            "; ".join(sorted(set(self.skipped.values()))) if self.skipped else ""
        )
        return [
            ScoreValue(
                outcome.name,
                outcome.value,
                reason=outcome.reason,
                provenance_detail=outcome.provenance,
            )
            for outcome in outcomes
            if outcome.measured
        ]


# Named sets of evaluators. A profile is what an experiment declares, so two
# arms are always scored by the same instruments.
PROFILES: dict[str, tuple[str, ...]] = {
    # Trajectory metrics ride in the default: the evaluator skips gracefully
    # (no library, no judge, no trajectory → no score, never a zero), so a lab
    # without DeepEval scores exactly as it did before. Set DEEPEVAL_MODEL=off
    # to keep the default deterministic-plus-audit only.
    # `skill_usage` is an observation, so it rides in every profile that
    # reads the record at all: a skill comparison without it cannot tell a
    # treatment from a menu (ADR-0041).
    "default": (
        "deterministic",
        "llm_judge",
        "efficiency",
        "step_reward",
        "skill_usage",
        "harness_audit",
        "deepeval",
    ),
    "with_trajectory": (
        "deterministic",
        "llm_judge",
        "efficiency",
        "step_reward",
        "skill_usage",
        "harness_audit",
        "deepeval",
    ),
    "outcome_only": ("deterministic",),
    "no_judgement": ("deterministic", "efficiency", "step_reward", "skill_usage"),
}

EVALUATORS = {
    evaluator.id: evaluator
    for evaluator in (
        DeterministicEvaluator(),
        RubricJudgeEvaluator(),
        EfficiencyEvaluator(),
        StepRewardEvaluator(),
        SkillUsageEvaluator(),
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
