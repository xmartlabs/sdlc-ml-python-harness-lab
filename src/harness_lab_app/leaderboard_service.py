"""Read-only aggregation feeding the Leaderboard screen.

Flattens every terminal run into one row carrying its experimental condition
(harness, models, prompt variant, per-run harness version) plus its outcome
(objective resolved, score, cost). The frontend groups and filters client-side —
run counts are small, and flat rows keep every filter combination possible.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Case, Run, RunConfig, RunState, Validation

if TYPE_CHECKING:
    from collections.abc import Mapping

# Rows only make sense once a run has finished executing (failed runs count as
# attempts — hiding them would inflate every % resolved).
_TERMINAL_STATES = {
    RunState.completed,
    RunState.awaiting_manual_score,
    RunState.failed,
}

# Official-leaderboard reference rows (curated, hand-updated). Tracked file.
REFERENCE_FILE = "reference/swebench_official.json"


def harness_version(meta: dict) -> str:
    """Friendly per-run harness version for the leaderboard axis.

    Precedence: the adopted release ref recorded at run time (``template_ref``,
    written by bench_run) > the harness fingerprint hash (older runs) > the lab
    commit > "". Display metadata — never raises.
    """
    ref = str(meta.get("template_ref") or "")
    if ref:
        return ref
    fingerprint = (meta.get("harness_fingerprint") or {}).get("harness_hash") or ""
    if fingerprint:
        return str(fingerprint).removeprefix("sha256:")[:8]
    return str(meta.get("lab_git_commit") or "")[:8]


def chain_depth(run_id: str, parents: Mapping[str, str | None]) -> int:
    """How many times a human steered this branch (ADR-0026).

    0 for an original run, 1 for its continuation, 2 for a continuation of that.
    A run whose parent was force-deleted stops the walk where the chain becomes
    unknowable rather than reporting a depth it cannot support. The visited set
    guards against a cycle that a hand-edited database could introduce.
    """
    depth = 0
    seen = {run_id}
    current = parents.get(run_id)
    while current and current not in seen:
        depth += 1
        seen.add(current)
        current = parents.get(current)
    return depth


class LeaderboardService:
    def __init__(self, session: Session):
        self.session = session
        self.runs_dir = get_settings().data_runs_dir

    def _read_metadata(self, run_id: str) -> dict:
        """Best-effort metadata.json read — parse errors degrade to {}."""
        path = self.runs_dir / run_id / "metadata.json"
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def list_rows(self) -> list[dict[str, Any]]:
        """One flat row per terminal run that actually attempted the case.

        Runs the lab itself broke are excluded rather than ranked. K-043/044/045
        died in under a second because the runner image was missing, having
        asked the model nothing — yet they counted as three attempts against it.
        An attempt count that includes runs which never started is worse than no
        count at all, because it looks authoritative.
        """
        from harness_lab_app.run_failures import is_unmeasured

        runs = [
            run
            for run in self.session.exec(select(Run))
            if run.state in _TERMINAL_STATES and not is_unmeasured(run.failure_reason)
        ]
        # Built over *every* run, not just terminal ones: a chain can pass
        # through a run that is filtered out of the rows below, and the depth
        # must still count it.
        parents = {
            row.run_id: row.parent_run_id for row in self.session.exec(select(Run))
        }
        configs = {config.id: config for config in self.session.exec(select(RunConfig))}
        cases = {case.case_id: case for case in self.session.exec(select(Case))}
        validations = {
            validation.run_id: validation
            for validation in self.session.exec(select(Validation))
        }

        rows: list[dict[str, Any]] = []
        for run in runs:
            config = configs.get(run.run_config_id)
            if config is None:  # orphaned run — nothing to rank it by
                continue
            case = cases.get(config.case_id)
            validation = validations.get(run.run_id)
            objective = validation.objective if validation else {}
            cost = validation.cost if validation else {}
            effort = validation.effort if validation else {}
            meta = self._read_metadata(run.run_id)
            rows.append(
                {
                    "run_id": run.run_id,
                    "case_id": config.case_id,
                    "case_type": str(case.type) if case else "",
                    "harness": config.harness,
                    "prompt_variant": config.prompt_variant,
                    "executor_model": config.executor_model,
                    "planner_model": config.planner_model,
                    "harness_version": harness_version(meta),
                    # True/False = objective verdict; None = no objective
                    # validation (manual-score cases) — excluded from % resolved.
                    "resolved": objective.get("passed"),
                    "score": run.score,
                    "composite": validation.composite if validation else None,
                    "cost_usd": cost.get("cost_usd"),
                    "total_tokens": cost.get("total_tokens"),
                    "steps": effort.get("steps"),
                    # Lineage: a continued run was steered by a human between
                    # steps, so it does not compete with a one-shot attempt.
                    # The dashboard hides chain_depth > 0 by default.
                    "parent_run_id": run.parent_run_id,
                    "chain_depth": chain_depth(run.run_id, parents),
                    "state": str(run.state),
                    "created_at": run.created_at.isoformat(),
                }
            )
        return rows

    def list_reference(self) -> list[dict[str, Any]]:
        """Curated official-leaderboard rows (missing/unreadable file → [])."""
        path = get_settings().data_runs_dir.parent / REFERENCE_FILE
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return doc if isinstance(doc, list) else []
