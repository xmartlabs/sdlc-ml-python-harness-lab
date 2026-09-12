"""The LLM reading of an ad-hoc comparison: the Compare table, read back as prose.

The Compare page lines up 2-4 runs and highlights the winning cell per row, but
it never says what the table means. This module assembles the same evidence the
page renders -- plus the run context the page cannot show -- and asks a model to
say, in a fixed set of sections, what the numbers say and what they do not.

A comparison has no server-side identity: it is a selection a person made in the
browser. :func:`comparison_key` hashes the sorted, de-duplicated run-id set, so
the same selection always resolves to the same stored reading no matter what
order the columns were added in, and regenerating overwrites it in place.

Written in English, unlike the experiment and repository auditors: this reading
renders inside the UI, and the lab's UI copy is English (see ADR-0060).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.attribution_service import AttributionService
from harness_lab_app.experiment_audit_service import (
    DETAIL_CLIP,
    RUN_METRICS,
    TERMINAL_STATES,
)
from harness_lab_app.models import CompareAudit, Validation, utc_now
from harness_lab_app.run_service import RunService
from harness_lab_app.score_service import ScoreService

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The picker's own bounds: one run is not a comparison, and past four the
#: table stops fitting on a screen and the reading stops being a reading.
MIN_RUNS = 2
MAX_RUNS = 4

#: Configuration fields that describe *what was varied*. The table shows these
#: columns but never names which of them actually differ, and "did it improve"
#: is only answerable against what changed.
_CONFIG_FIELDS = (
    "case_id",
    "prompt_variant",
    "harness",
    "harness_source",
    "planner_model",
    "executor_model",
    "max_steps",
    "target_repo_id",
    "target_ref",
    "skills_disabled",
    "skills_extra",
    "skills_invoke",
)

#: Higher is better for these; for the rest of `RUN_METRICS`, lower is.
_HIGHER_IS_BETTER = frozenset(
    {
        "task_validation_pass",
        "test_pass_rate",
        "rubric_score",
        "skill_invoked",
        "skill_invocations",
        "governance_read_coverage",
    }
)

COMPARE_AUDIT_SYSTEM_PROMPT = (
    "You are the auditor of a lab that measures whether an agent coding harness "
    "(governance docs, skills, prompt, hooks) helps a model. You are given the "
    "evidence behind an ad-hoc comparison a person assembled by hand: two to four "
    "runs placed side by side, what each one was configured with, what it produced, "
    "and what it cost. Write a short report in English, in Markdown, with EXACTLY "
    "these sections in this order:\n"
    "## Verdict\n"
    "One or two sentences: did anything improve, by how much, and how much weight "
    "this selection can carry. If the numbers that would settle it were not "
    "measured, say that first.\n"
    "## What was compared\n"
    "One line per run: its id and the configuration that identifies it.\n"
    "## What the numbers say\n"
    "Outcome and cost per run with the numbers exactly as given (pass/fail, "
    "test_pass_rate, cost in USD, billed tokens, minutes). A short table is fine. "
    "Name every value that was not measured instead of leaving it out.\n"
    "## What differed between these runs\n"
    "What was held constant and what varied, as explicit lists. Then, for each "
    "delta, whether the difference is one the varying configuration could plausibly "
    "explain.\n"
    "## Why this is not a causal claim\n"
    "In plain language: this is a hand-picked selection, not a controlled "
    "experiment; there is no repetition, no randomisation and no control arm; any "
    "difference may come from anything in the list of what varied, including the "
    "model's own run-to-run variation.\n"
    "## What to do next\n"
    "Two or three concrete steps that follow from the above -- what to measure, "
    "what to hold fixed, what to repeat.\n\n"
    "Rules: use ONLY the evidence given -- invent no causes and no numbers; copy "
    "numbers exactly as they appear; never write 'significant' or any "
    "statistical-test vocabulary; a null is NOT MEASURED and is never zero, never "
    "treat it as one and never compute a delta from it; with a single run per "
    "configuration this is exploratory and you must say so; do not repeat the same "
    "datum in two sections; include no secrets, no tokens and no filesystem paths."
)


def comparison_key(run_ids: Sequence[str]) -> str:
    """A stable id for a set of runs, independent of the order they were picked.

    Reordering the columns on the page must not orphan the reading that was
    already written for the same runs, so the key hashes the sorted, unique set.
    """
    return hashlib.sha256("\n".join(sorted(set(run_ids))).encode()).hexdigest()


def runs_finished(session: Session, run_ids: Sequence[str]) -> tuple[bool, int, int]:
    """(every run stopped, finished count, total count) for a selection.

    A reading of a run that is still executing would describe an outcome that
    does not exist yet, and it would be stored as though it did.
    """
    runs = RunService(session)
    total = 0
    finished = 0
    for run_id in dict.fromkeys(run_ids):
        total += 1
        if runs.get_run(run_id).state in TERMINAL_STATES:
            finished += 1
    return (bool(total) and finished == total, finished, total)


def _score_values(session: Session, run_id: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for score in ScoreService(session).list_scores(run_id):
        if score.name in RUN_METRICS:
            values[score.name] = (
                score.value_num
                if score.value_num is not None
                else score.value_bool
                if score.value_bool is not None
                else score.value_text
            )
    return values


def _config_facts(config: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for field in _CONFIG_FIELDS:
        value = getattr(config, field, None)
        facts[field] = sorted(value) if isinstance(value, list) else value
    facts["harness_source"] = str(facts["harness_source"])
    return facts


def _split_constant(configs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Which configuration fields are identical across the selection, and which vary."""
    held: dict[str, Any] = {}
    differed: list[str] = []
    for field in _CONFIG_FIELDS:
        values = [json.dumps(facts[field], sort_keys=True) for facts in configs]
        if len(set(values)) == 1:
            held[field] = configs[0][field]
        else:
            differed.append(field)
    return held, differed


def _deltas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every metric against the leftmost run -- the baseline a reader's eye starts from.

    A metric missing on either side is reported with ``measured: false`` and no
    delta rather than a zero: the page renders both as an em dash, and the whole
    point of the reading is that those two are not the same claim.
    """
    baseline = rows[0]
    deltas: list[dict[str, Any]] = []
    for row in rows[1:]:
        for name in RUN_METRICS:
            before = baseline["scores"].get(name)
            after = row["scores"].get(name)
            if before is None and after is None:
                continue
            comparable = isinstance(before, (int, float)) and isinstance(
                after, (int, float)
            )
            delta = {
                "metric": name,
                "baseline_run_id": baseline["run_id"],
                "run_id": row["run_id"],
                "baseline": before,
                "value": after,
                "measured": comparable,
                "change": (after - before) if comparable else None,
                "direction": "higher_is_better"
                if name in _HIGHER_IS_BETTER
                else "lower_is_better",
            }
            deltas.append(delta)
    return deltas


def build_evidence(session: Session, run_ids: Sequence[str]) -> dict[str, Any]:
    """Everything the auditor is allowed to read, as one JSON-able document."""
    runs = RunService(session)
    attribution = AttributionService(session)

    ordered = list(dict.fromkeys(run_ids))
    rows: list[dict[str, Any]] = []
    configs: list[dict[str, Any]] = []
    for run_id in ordered:
        run = runs.get_run(run_id)
        config = runs.get_run_config(run.run_config_id)
        validation = session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        objective = (validation.objective if validation else None) or {}
        subjective = (validation.subjective if validation else None) or {}
        effort = (validation.effort if validation else None) or {}
        # A check that did not measure is not a check that failed (ADR-0062):
        # served under `failed_checks` it reads to the auditor as the run's
        # fault rather than the sandbox's.
        failed_checks = [
            {
                "command": str(check.get("command") or check.get("name") or "")[-120:],
                "exit_code": check.get("exit_code"),
                "conclusive": check.get("conclusive", True),
                "detail": str(check.get("detail") or "").strip()[-DETAIL_CLIP:],
            }
            for check in objective.get("checks") or []
            if not check.get("passed") and check.get("conclusive", True)
        ]
        inconclusive_checks = [
            {
                "command": str(check.get("command") or check.get("name") or "")[-120:],
                "exit_code": check.get("exit_code"),
                "reason": check.get("inconclusive_reason"),
                "detail": str(check.get("detail") or "").strip()[-DETAIL_CLIP:],
            }
            for check in objective.get("checks") or []
            if check.get("conclusive") is False
        ]
        usage: dict[str, Any] = {}
        attributed = attribution.get_attribution(run_id)
        if attributed is not None:
            harness = attributed.harness_usage or {}
            available_docs = list(harness.get("governance_docs_available") or [])
            read_docs = list(harness.get("governance_docs_read") or [])
            available_skills = list(harness.get("skills_available") or [])
            consulted = list(harness.get("skills_consulted") or [])
            usage = {
                "governance_docs_read": read_docs,
                "governance_docs_not_read": [
                    doc for doc in available_docs if doc not in read_docs
                ],
                "skills_invoked": consulted,
                "skills_asked_for": sorted(config.skills_invoke),
                "skills_asked_for_but_not_invoked": [
                    skill
                    for skill in sorted(config.skills_invoke)
                    if skill not in consulted
                ],
                "skills_available_never_opened": [
                    skill for skill in available_skills if skill not in consulted
                ],
                "governance_source": harness.get("governance_source", ""),
            }
        facts = _config_facts(config)
        configs.append(facts)
        rows.append(
            {
                "run_id": run_id,
                "state": str(run.state),
                "stop_reason": effort.get("stop_reason"),
                "config": facts,
                "objective": objective.get("summary"),
                "failed_checks": failed_checks,
                "inconclusive_checks": inconclusive_checks,
                "judge": {
                    "score": subjective.get("functional_score"),
                    "source": subjective.get("score_source"),
                    "rationale": str(subjective.get("notes") or "")[:DETAIL_CLIP],
                },
                "scores": _score_values(session, run_id),
                "usage": usage,
            }
        )

    held_constant, differed = _split_constant(configs)
    return {
        "comparison_key": comparison_key(ordered),
        "selection": "ad-hoc: these runs were picked by hand, not generated as an "
        "experiment with arms and repetitions",
        "baseline_run_id": ordered[0] if ordered else None,
        "run_ids": ordered,
        "held_constant": held_constant,
        "differed": differed,
        "runs": rows,
        "deltas": _deltas(rows) if rows else [],
    }


def render_audit_prompt(evidence: dict[str, Any]) -> str:
    """The user message: the evidence, verbatim, as JSON the model can quote."""
    return (
        "Evidence behind this comparison (JSON). Every number you cite has to "
        "come from here.\n\n```json\n"
        + json.dumps(evidence, indent=1, ensure_ascii=False, default=str)
        + "\n```"
    )


class CompareAuditService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, key: str) -> CompareAudit | None:
        return self.session.exec(
            select(CompareAudit).where(CompareAudit.comparison_key == key)
        ).first()

    def _upsert(self, key: str, run_ids: Sequence[str]) -> CompareAudit:
        audit = self.get(key)
        if audit is None:
            audit = CompareAudit(comparison_key=key, run_ids=list(run_ids))
            self.session.add(audit)
        return audit

    def _commit(self, audit: CompareAudit) -> CompareAudit:
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def mark_running(
        self, key: str, run_ids: Sequence[str], auditor_model: str = ""
    ) -> CompareAudit:
        """Flag the reading as in flight, keeping any previous text visible.

        The old markdown is deliberately left in place: while a regeneration is
        running the reader still has the reading they asked to replace.
        """
        audit = self._upsert(key, run_ids)
        audit.status = "running"
        audit.auditor_model = auditor_model
        audit.error = None
        return self._commit(audit)

    def store_result(
        self,
        key: str,
        run_ids: Sequence[str],
        markdown: str,
        evidence: dict[str, Any],
        model: str,
    ) -> CompareAudit:
        audit = self._upsert(key, run_ids)
        audit.status = "done"
        audit.markdown = markdown
        audit.evidence_json = evidence
        audit.auditor_model = model
        audit.error = None
        return self._commit(audit)

    def store_error(self, key: str, run_ids: Sequence[str], error: str) -> CompareAudit:
        audit = self._upsert(key, run_ids)
        audit.status = "failed"
        audit.error = error[:2000]
        return self._commit(audit)
