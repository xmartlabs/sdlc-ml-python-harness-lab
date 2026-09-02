"""The audit, as a document a client can be handed.

Everything the guided audit produces — what the repository ships, what was
measured on it, what the lab is prepared to claim — assembled into one
markdown report. Three rules, inherited from the rest of the lab:

- **Facts, observations and judgements stay labelled.** A success rate carries
  its denominator; an LLM-judged score says it is a judgement.
- **Nothing is invented.** A capability the lab did not observe is "Not
  detected"; a number with no runs behind it does not appear.
- **Only accepted findings speak.** Suggestions the corpus raised but nobody
  signed do not enter a client-facing document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from harness_lab_app.models import Case, Finding, FindingStatus, Run, RunConfig


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _readiness_section(readiness: dict[str, Any]) -> list[str]:
    lines = ["## What the repository ships today", ""]
    mark = {"ok": "✓", "missing": "✗", "unknown": "?"}
    for check in readiness.get("checks", []):
        lines.append(
            f"- **{check['label']}** [{mark.get(check['status'], '?')}] "
            f"— {check['detail']}"
        )
    suggested = readiness.get("suggested_validation_commands") or []
    if suggested and not any(
        c["label"] == "Validation gate" and c["status"] == "ok"
        for c in readiness.get("checks", [])
    ):
        lines += [
            "",
            "Inferred from the repository's own files (not yet adopted): "
            + ", ".join(f"`{cmd}`" for cmd in suggested),
        ]
    return lines


def _evidence_section(session: Session, target_id: str) -> list[str]:
    configs = list(
        session.exec(select(RunConfig).where(RunConfig.target_repo_id == target_id))
    )
    config_ids = {c.id for c in configs}
    runs = [
        run
        for run in session.exec(select(Run)).all()
        if run.run_config_id in config_ids
    ]
    lines = ["## Evidence collected", ""]
    if not runs:
        lines.append(
            "No runs have been recorded against this repository yet — the "
            "sections below rest on reading it, not on measuring it."
        )
        return lines
    by_state: dict[str, int] = {}
    for run in runs:
        by_state[run.state.value] = by_state.get(run.state.value, 0) + 1
    lines.append(
        f"- **{len(runs)} run(s)** against this repository "
        f"({', '.join(f'{v} {k}' for k, v in sorted(by_state.items()))})."
    )

    experiment_ids = sorted({run.experiment_id for run in runs if run.experiment_id})
    if experiment_ids:
        from harness_lab_app.experiment_service import ExperimentService

        service = ExperimentService(session)
        for experiment_id in experiment_ids:
            try:
                summary = service.summarize(experiment_id)
            except Exception:
                continue
            lines += ["", f"### Experiment `{experiment_id}`", ""]
            lines.append(f"*{summary['question']}* — mode: {summary['mode']}.")
            for condition in summary["conditions"]:
                rate = condition.get("success_rate")
                scored = condition.get("scored_runs", 0)
                total = condition.get("runs", 0)
                rate_text = (
                    f"{rate * 100:.0f}% success over {scored} scored run(s)"
                    if rate is not None
                    else f"no objective score yet ({scored}/{total} scored)"
                )
                lines.append(f"- Arm **{condition['label']}**: {rate_text}.")
            verdict = summary.get("verdict") or {}
            if verdict.get("headline"):
                lines.append(
                    f"- Verdict: **{verdict['headline']}** — {verdict.get('claim', '')} "
                    f"(evidence: {verdict.get('evidence', '')})."
                )
    return lines


def _cases_section(session: Session, target_id: str) -> list[str]:
    cases = [
        case
        for case in session.exec(select(Case)).all()
        if target_id in (case.tags or [])
    ]
    lines = ["## Cases derived from this repository's own history", ""]
    if not cases:
        lines.append(
            "None yet. Cases are mined from real commits (a change and the "
            "tests that verified it), so the benchmark is this team's own work."
        )
        return lines
    for case in cases:
        gate = "objective gate" if case.validation_commands else "manual scoring"
        lines.append(f"- `{case.case_id}` — {case.name} ({gate}).")
    return lines


def _findings_section(session: Session, target_id: str) -> list[str]:
    findings = [
        finding
        for finding in session.exec(select(Finding)).all()
        if finding.target_repo_id == target_id
        and finding.status != FindingStatus.dismissed
    ]
    lines = ["## Findings on the record", ""]
    if not findings:
        lines.append(
            "None accepted yet. The lab surfaces *suggestions* from the run "
            "corpus, but nothing becomes a finding until a person signs it."
        )
        return lines
    for finding in findings:
        lines.append(
            f"- **[{finding.severity.value}] {finding.title}** "
            f"({finding.status.value}"
            + (f", by {finding.author}" if finding.author else "")
            + ")."
        )
        if finding.recommendation:
            lines.append(f"  - Recommended: {finding.recommendation}")
        if finding.evidence_run_ids:
            lines.append(f"  - Evidence: {len(finding.evidence_run_ids)} run(s).")
    return lines


def _next_steps(readiness: dict[str, Any]) -> list[str]:
    lines = ["## Recommended next steps", ""]
    status = {c["label"]: c["status"] for c in readiness.get("checks", [])}
    suggested = readiness.get("suggested_validation_commands") or []
    if status.get("Validation gate") != "ok":
        cmd = (
            f" (inferred: {', '.join(f'`{c}`' for c in suggested)})"
            if suggested
            else ""
        )
        lines.append(
            f"1. **Adopt an objective validation gate**{cmd} so every future run "
            "is scored by the repository's own tests instead of by hand."
        )
    if status.get("Agent rules file") != "ok":
        lines.append(
            "2. **Add an agent rules file** (`CLAUDE.md` / `AGENTS.md`): agents "
            "currently discover conventions by trial and error on every run."
        )
    if status.get("Skills") != "ok":
        lines.append(
            "3. **Trial a governed skill set**: the measurable path is a "
            "native-versus-injected experiment — this repository as it is, "
            "against the same repository with a harness injected, same cases, "
            "5+ repetitions per arm."
        )
    if len(lines) == 2:
        lines.append(
            "The basics are in place. The next lever is comparative: measure "
            "the current setup against a candidate change with a "
            "harness-evaluation experiment."
        )
    return lines


def build_report(session: Session, target: Any, readiness: dict[str, Any]) -> str:
    """The whole document, as markdown ready to paste or export."""
    commit = readiness.get("commit")
    header = [
        f"# Repository audit — {target.name}",
        "",
        f"`{target.repository_url}` at `{(commit or readiness.get('ref') or '?')[:12]}`"
        f" · generated {datetime.now(UTC).date().isoformat()} by Agentic Harness Lab",
        "",
        "Exploratory audit: it **describes** how this repository behaves under "
        "an agent today. Where a causal claim appears, it comes from a "
        "controlled experiment and says so; everything else is observation.",
        "",
    ]
    body = [
        *_readiness_section(readiness),
        "",
        *_cases_section(session, target.target_id),
        "",
        *_evidence_section(session, target.target_id),
        "",
        *_findings_section(session, target.target_id),
        "",
        *_next_steps(readiness),
        "",
        "## Method",
        "",
        "Deterministic facts (test outcomes, cost, tokens) are reported as "
        "measured. LLM-judged scores are labelled judgements. Comparisons "
        "under five repetitions per arm are reported as exploratory, "
        "whatever the difference looks like.",
    ]
    return "\n".join(header + body) + "\n"
