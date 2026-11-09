"""One quick page of local-model results: tests run here, and real cases run.

Two tables, kept apart on purpose:

- **Speed tests** (this page's profiles): which model and configuration reached
  which tokens per second on which machine. Ranked by seconds per agent step,
  the number a long run actually pays.
- **Case runs**: lab runs whose executor is a local model, grouped by model and
  case. Ranked by the pass rate of the graded tests.

They are never merged into one score: a speed test says how fast a setup is, a
case run says whether it does the work, and the second also depends on the
harness and the prompt. Speed rows are only comparable within one machine,
which every row names.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlmodel import Session, select

from harness_lab_app.local_tuning.history import summarize
from harness_lab_app.local_tuning.reference_benchmarks import match_reference
from harness_lab_app.models import (
    LocalMachine,
    Run,
    RunConfig,
    RunState,
    TuningProfile,
    Validation,
)

_LOCAL_PREFIXES = ("lmstudio/", "ollama/", "gateway/lmstudio", "gateway/ollama")
_FINISHED = (RunState.completed, RunState.awaiting_manual_score, RunState.failed)


def is_local_model(model: str | None) -> bool:
    return bool(model) and str(model).startswith(_LOCAL_PREFIXES)


def _check_pass_rate(objective: dict) -> tuple[int, int]:
    """(passed, total) over the graded checks; the tamper guard counts like any other."""
    checks = [c for c in objective.get("checks") or [] if c.get("conclusive", True)]
    return sum(1 for c in checks if c.get("passed")), len(checks)


def speed_ranking(session: Session) -> list[dict[str, Any]]:
    """Every saved test, fastest first (lowest seconds per agent step)."""
    machines = {m.id: m.name for m in session.exec(select(LocalMachine)).all()}
    rows = []
    for profile in session.exec(select(TuningProfile)).all():
        summary = summarize(
            profile.results or [], profile.served_config, profile.estimate
        )
        rows.append(
            {
                "profile_id": profile.id,
                "machine": machines.get(profile.machine_id, f"#{profile.machine_id}"),
                "machine_id": profile.machine_id,
                "model_id": profile.model_id,
                "label": profile.label,
                "server_kind": profile.server_kind,
                "recommended": profile.recommended,
                "created_at": profile.created_at,
                "headroom": _speed_headroom(profile),
                "reference_model": match_reference(profile.model_id),
                **summary,
            }
        )
    rows.sort(
        key=lambda row: (
            row["seconds_per_step"] is None,
            row["seconds_per_step"] or 0.0,
        )
    )
    return rows


def _speed_headroom(profile: TuningProfile) -> dict[str, Any] | None:
    """The write-speed headroom the speed test recorded, if any."""
    for result in profile.results or []:
        if isinstance(result, dict) and result.get("name") == "speed":
            return (result.get("detail") or {}).get("headroom")
    return None


def case_ranking(session: Session) -> list[dict[str, Any]]:
    """Local-model case runs grouped by (model, harness, case, arm), best first."""
    configs = {c.id: c for c in session.exec(select(RunConfig)).all()}
    validations = {v.run_id: v for v in session.exec(select(Validation)).all()}
    triples = [
        (run, configs[run.run_config_id], validations.get(run.run_id))
        for run in session.exec(select(Run)).all()
        if run.state in _FINISHED and run.run_config_id in configs
    ]
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "runs": 0,
            "passed_runs": 0,
            "validated_runs": 0,
            "checks_passed": 0,
            "checks_total": 0,
            "minutes": [],
            "conditions": set(),
        }
    )
    for run, config, validation in triples:
        if not is_local_model(config.executor_model):
            continue
        # One row per arm: pooling a bare and a full-harness arm would average
        # two conditions into a number neither of them produced.
        arm = run.condition_label or config.prompt_variant or ""
        key = (
            config.executor_model or "",
            config.harness or "",
            config.case_id or "",
            arm,
        )
        group = groups[key]
        group["runs"] += 1
        if run.condition_label:
            group["conditions"].add(run.condition_label)
        if run.started_at and run.ended_at:
            group["minutes"].append(
                (run.ended_at - run.started_at).total_seconds() / 60
            )
        objective = (validation.objective if validation else None) or {}
        if objective:
            group["validated_runs"] += 1
            if objective.get("passed"):
                group["passed_runs"] += 1
            passed, total = _check_pass_rate(objective)
            group["checks_passed"] += passed
            group["checks_total"] += total
    rows = []
    for (model, harness, case_id, arm), group in groups.items():
        minutes = sorted(group["minutes"])
        rows.append(
            {
                "model": model,
                "harness": harness,
                "case_id": case_id,
                "arm": arm,
                "conditions": sorted(group["conditions"]),
                "runs": group["runs"],
                "validated_runs": group["validated_runs"],
                "passed_runs": group["passed_runs"],
                "test_pass_rate": (
                    group["checks_passed"] / group["checks_total"]
                    if group["checks_total"]
                    else None
                ),
                "median_minutes": minutes[len(minutes) // 2] if minutes else None,
            }
        )
    rows.sort(
        key=lambda row: (
            row["test_pass_rate"] is None,
            -(row["test_pass_rate"] or 0.0),
            row["median_minutes"] or 0.0,
        )
    )
    return rows


def local_results(session: Session) -> dict[str, Any]:
    return {"speed": speed_ranking(session), "cases": case_ranking(session)}
