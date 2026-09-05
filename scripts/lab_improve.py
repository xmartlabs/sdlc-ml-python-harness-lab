#!/usr/bin/env python3
"""Continuous-improvement cycle: turn a run's audit into GitHub improvement issues.

The lab tests harness/model combinations; the LLM audit already judges which
skills, governance docs, and subagents helped, were redundant, or need work. This
script synthesizes those signals into actionable **improvement proposals** and,
on your explicit approval, files them as GitHub issues in the repos that own the
harness — so the lab feeds a continuous-improvement loop for your template and
harness docs.

Flow (approval is running the create step yourself):

  1. Generate (dry-run, default): synthesize proposals with a dedicated LLM pass
     and write them to data/runs/<run>/improvements.json (+ .md preview). Edit
     that file freely — change the target `repo`, tweak titles/bodies, or delete
     proposals you don't want.

        make improve RUN=tetris-v1-K-004

  2. Create: file the (edited) proposals as issues via `gh` on your host.

        make improve RUN=tetris-v1-K-004 CREATE=1

Routing (fixed default, editable in the JSON): skills / governance docs /
subagents / hooks → the template repo; harness methodology / process → the
harness-guide repo.

Requires: a completed audit for the run (make bench-audit RUN=... or the Run
audit button), GATEWAY_BASE_URL/GATEWAY_TOKEN in .env, and `gh` authenticated.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench_audit import call_gateway
from create_run import DATA_DIR, _load_env

# Target repos. Canonical names match the Settings fields the web app reads
# (harness_lab_app.config); the IMPROVE_REPO_* spellings predate them and are
# still honoured so existing .env files keep working. This script deliberately
# does not import harness_lab_app — it runs from inside the runner container,
# which installs no web dependencies.
TEMPLATE_REPO = (
    os.environ.get("HARNESS_LAB_TEMPLATE_REPOSITORY")
    or os.environ.get("IMPROVE_REPO_TEMPLATE")
    or "xmartlabs/sdlc-ml-python-base"
)
HARNESS_REPO = (
    os.environ.get("HARNESS_LAB_GUIDE_REPOSITORY")
    or os.environ.get("IMPROVE_REPO_HARNESS")
    or "xmartlabs/sdlc-harness-engineering-guide"
)
ISSUE_LABEL = os.environ.get("IMPROVE_LABEL", "lab-improvement")

# Governance docs owned by the template repo (route there when named).
GOVERNANCE_DOCS = {
    "architecture.md",
    "standards.md",
    "domain-boundaries.md",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
}

# Strictness of the synthesis filter: how much evidence a proposal needs to
# exist and how many can come out of one run. "strict" = fewer, higher-signal
# proposals. Default balanced; override with IMPROVE_STRICTNESS / --strictness.
STRICTNESS_LEVELS = {
    "strict": {
        "max_proposals": 2,
        "bar": (
            "Only propose a change when THIS run shows a clear behavioral "
            "failure with direct evidence (wrong result, wasted work, broken "
            "workflow) that the targeted artifact plausibly caused."
        ),
    },
    "balanced": {
        "max_proposals": 4,
        "bar": (
            "Only propose changes backed by concrete evidence of friction or "
            "failure in THIS run; skip speculative polish."
        ),
    },
    "lenient": {
        "max_proposals": 6,
        "bar": (
            "Prefer evidenced problems, but clear improvement opportunities "
            "with weaker signals may be included."
        ),
    },
}
DEFAULT_STRICTNESS = "balanced"

# Outcome gate: a scored run at/above this (score or composite), with its
# objective passed, zero interventions, and zero "improve" verdicts is treated
# as "the harness worked for this case+model" — synthesis is skipped entirely
# so clean runs cannot slowly bias the template. Override the threshold with
# IMPROVE_GATE_MIN_SCORE; bypass the gate with IMPROVE_FORCE=1 / --force.
GATE_MIN_SCORE = float(os.environ.get("IMPROVE_GATE_MIN_SCORE", "8"))

SYSTEM_PROMPT_TEMPLATE = """\
You improve an AI engineering harness. You are given evidence from ONE benchmark
run: the measured outcome, the behavioral audit, the structured per-item
verdicts (skills / governance docs / subagents / hooks, each judged useful |
redundant | improve | unknown), and the run context.

Produce concrete, de-duplicated IMPROVEMENT PROPOSALS for the harness — only
from real signals: items judged "improve", required governance/skills that were
NOT followed, or failures the audit demonstrates. {bar}

Calibration rules:
- Use the measured outcome. A run that passed its objective with a high score,
  no interventions, and no "improve" verdicts is evidence the harness WORKED
  for this case+model: return [] unless something concretely failed.
- "unknown" verdicts mean the harness could not observe usage (normal for
  summary-only harnesses). Treat them as neutral — never as gaps.
- "redundant" means unused for this case. That is expected — a template serves
  many cases. Never propose changes from redundancy alone.
- Do NOT propose changes whose main purpose is making behavior more observable
  or auditable (plan notes, read acknowledgements, checkpoints) unless the run
  concretely failed because that discipline was missing.
- Do NOT invent problems. An empty array is a good outcome, not a failure.

Each proposal targets a documentation or skill change (not the subject task).
Output ONLY a JSON array (no prose, no code fences), at most {max_proposals}
elements. Each element:

{{"title": "<imperative, <=80 chars>",
 "kind": "skill" | "doc" | "subagent" | "hook" | "process",
 "target": "<exact skill/doc/subagent name, or the process area>",
 "repo_hint": "template" | "harness",
 "body": "<markdown: the problem (with evidence from THIS run), the proposed
           change, and why it helps. 2-6 short paragraphs or bullets.>"}}

repo_hint: "template" for skills, governance docs, subagents, hooks that ship in
the project template; "harness" for methodology / process / cross-cutting harness
guidance. Keep it to the highest-signal proposals.
"""


def build_system_prompt(strictness: str) -> str:
    level = STRICTNESS_LEVELS[strictness]
    return SYSTEM_PROMPT_TEMPLATE.format(
        bar=level["bar"], max_proposals=level["max_proposals"]
    )


def route_repo(kind: str, target: str, repo_hint: str) -> str:
    """Default target repo (editable in the JSON afterwards)."""
    if kind == "process" or repo_hint == "harness":
        return HARNESS_REPO
    if kind == "doc" and target not in GOVERNANCE_DOCS and "harness" in target.lower():
        return HARNESS_REPO
    return TEMPLATE_REPO


def _read(path: Path, default: str = "") -> str:
    return path.read_text() if path.exists() else default


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_run(run_id: str) -> dict:
    out = DATA_DIR / run_id
    if not out.exists():
        sys.exit(f"❌ Run not found: {run_id} ({out})")
    audit_md = _read(out / "audit.md")
    findings = _read_json(out / "audit_findings.json")
    if not audit_md and not findings.get("items"):
        sys.exit(
            f"❌ No audit for {run_id}. Run `make bench-audit RUN={run_id}` "
            "(or the Run audit button) first."
        )
    meta = _read_json(out / "metadata.json")
    return {
        "run_id": run_id,
        "case": meta.get("case") or meta.get("benchmark_id", ""),
        "harness": meta.get("harness") or "",
        "executor_model": meta.get("executor_model", ""),
        "prompt_variant": meta.get("prompt_variant", ""),
        "audit_md": audit_md,
        "findings": findings.get("items", []),
    }


def _parse_proposals(text: str) -> list[dict]:
    """Tolerant parse of the LLM's JSON array (strips fences / surrounding prose)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if not cleaned.startswith("["):
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("title") and it.get("body"):
            out.append(it)
    return out


def load_outcome(run_id: str) -> dict | None:
    """Best-effort measured outcome from the lab DB (None when unavailable).

    The script stays runnable without the app (e.g. artifacts copied elsewhere):
    any import/DB failure just disables the outcome gate and the outcome block.
    """
    try:
        from sqlmodel import Session, select

        from harness_lab_app.db import engine
        from harness_lab_app.models import Run, Validation

        with Session(engine) as session:
            run = session.exec(select(Run).where(Run.run_id == run_id)).first()
            validation = session.exec(
                select(Validation).where(Validation.run_id == run_id)
            ).first()
    except Exception:
        return None
    if run is None and validation is None:
        return None
    objective = (validation.objective if validation else {}) or {}
    effort = (validation.effort if validation else {}) or {}
    return {
        "score": run.score if run else None,
        "interventions": run.interventions if run else None,
        "composite": validation.composite if validation else None,
        "objective_passed": objective.get("passed"),
        "steps": effort.get("steps"),
    }


def gate_reason(
    outcome: dict | None, improve_count: int, min_score: float = GATE_MIN_SCORE
) -> str | None:
    """Non-None reason ⇒ skip synthesis: the run met expectations as-is.

    Requires CONFIRMED goodness — an unscored run never gates (outcome fields
    missing/None fail the checks and synthesis proceeds normally).
    """
    if outcome is None or improve_count > 0:
        return None
    if outcome.get("objective_passed") is not True:
        return None
    if (outcome.get("interventions") or 0) != 0:
        return None
    scores = [
        v for v in (outcome.get("score"), outcome.get("composite")) if v is not None
    ]
    best = max(scores, default=None)
    if best is None or best < min_score:
        return None
    return (
        "Run met expectations for this case+model: objective passed, "
        f"score {best:g} >= {min_score:g}, 0 interventions, 0 'improve' "
        "findings. No proposals generated."
    )


def build_user_message(run: dict, outcome: dict | None) -> str:
    """Honest signal only: 'improve' verdicts + measured outcome + audit.

    When zero items were judged 'improve' that is said explicitly — the full
    verdict list (mostly redundant/unknown) is never dumped as if it needed
    improvement.
    """
    improve = [f for f in run["findings"] if f.get("verdict") == "improve"]
    findings_block = (
        "\n".join(
            f"- {f.get('name')} ({f.get('kind')}): {f.get('note', '')}" for f in improve
        )
        or "(none — 0 of the audited items were judged 'improve')"
    )
    if outcome is None:
        outcome_block = "(not scored/validated yet)"
    else:
        fmt = lambda v: "n/a" if v is None else str(v)  # noqa: E731
        outcome_block = (
            f"- objective passed: {fmt(outcome.get('objective_passed'))}\n"
            f"- manual score (0-10): {fmt(outcome.get('score'))}\n"
            f"- composite score: {fmt(outcome.get('composite'))}\n"
            f"- steps: {fmt(outcome.get('steps'))}\n"
            f"- human interventions: {fmt(outcome.get('interventions'))}"
        )
    return (
        f"# Run: {run['run_id']}\n"
        f"harness={run['harness'] or '?'} model={run['executor_model']} "
        f"case={run['case']} prompt={run['prompt_variant']}\n\n"
        f"## Measured outcome\n{outcome_block}\n\n"
        f"## Verdicts judged 'improve'\n{findings_block}\n\n"
        f"## Behavioral audit\n{run['audit_md'][:40000]}"
    )


def synthesize(
    run: dict,
    model: str,
    strictness: str = DEFAULT_STRICTNESS,
    outcome: dict | None = None,
) -> list[dict]:
    raw = call_gateway(
        model, build_system_prompt(strictness), build_user_message(run, outcome)
    )
    max_proposals = int(STRICTNESS_LEVELS[strictness]["max_proposals"])  # type: ignore[call-overload]
    proposals = _parse_proposals(raw)[:max_proposals]
    for p in proposals:
        p.setdefault("kind", "doc")
        p["repo"] = route_repo(
            p.get("kind", ""), p.get("target", ""), p.get("repo_hint", "")
        )
        p["created_issue_url"] = None
    return proposals


def issue_body(proposal: dict, run: dict) -> str:
    footer = (
        "\n\n---\n"
        f"_Proposed by the Agentic Harness Lab from run `{run['run_id']}` — "
        f"harness `{run['harness'] or '?'}`, model `{run['executor_model']}`, "
        f"case `{run['case']}`. Source: audit finding "
        f"({proposal.get('kind')}: {proposal.get('target', '')})._"
    )
    return proposal["body"].strip() + footer


def generate(
    run_id: str,
    model: str,
    strictness: str = DEFAULT_STRICTNESS,
    force: bool = False,
) -> Path:
    run = load_run(run_id)
    outcome = load_outcome(run_id)
    improve_count = sum(1 for f in run["findings"] if f.get("verdict") == "improve")
    reason = None if force else gate_reason(outcome, improve_count)
    if reason:
        print(f"✓ Outcome gate: {reason}")
        print("  (Bypass with IMPROVE_FORCE=1 / --force to synthesize anyway.)")
        proposals: list[dict] = []
    else:
        print(
            f"→ Synthesizing improvement proposals for {run_id} "
            f"(model {model}, strictness {strictness})…"
        )
        proposals = synthesize(run, model, strictness=strictness, outcome=outcome)
    doc = {
        "run_id": run_id,
        "context": {
            "case": run["case"],
            "harness": run["harness"],
            "executor_model": run["executor_model"],
        },
        "generator_model": model,
        "strictness": strictness,
        "gate": {
            "skipped": bool(reason),
            "reason": reason,
            "min_score": GATE_MIN_SCORE,
        },
        "proposals": proposals,
    }
    out = DATA_DIR / run_id
    (out / "improvements.json").write_text(json.dumps(doc, indent=2))
    preview = [f"# Improvement proposals — {run_id}\n"]
    if reason:
        preview.append(f"_{reason}_\n")
    for i, p in enumerate(proposals, 1):
        preview.append(
            f"## {i}. {p['title']}\n\n"
            f"- repo: `{p['repo']}`\n- kind: {p.get('kind')} · target: "
            f"{p.get('target', '')}\n\n{p['body'].strip()}\n"
        )
    (out / "improvements.md").write_text("\n".join(preview))

    print(f"\n{len(proposals)} proposal(s) → data/runs/{run_id}/improvements.json")
    for i, p in enumerate(proposals, 1):
        print(f"  {i}. [{p['repo']}] {p['title']}")
    print(
        "\nReview/edit improvements.json (change `repo`, edit, or delete), then:\n"
        f"  make improve RUN={run_id} CREATE=1"
    )
    return out / "improvements.json"


def _ensure_label(repo: str) -> None:
    subprocess.run(
        [
            "gh",
            "label",
            "create",
            ISSUE_LABEL,
            "-R",
            repo,
            "--force",
            "--color",
            "1D76DB",
            "--description",
            "Proposed by Agentic Harness Lab",
        ],
        capture_output=True,
        text=True,
    )


def create(run_id: str) -> int:
    out = DATA_DIR / run_id
    path = out / "improvements.json"
    if not path.exists():
        sys.exit(f"❌ No proposals. Run `make improve RUN={run_id}` first.")
    doc = _read_json(path)
    run = {"run_id": run_id, **doc.get("context", {})}
    pending = [p for p in doc["proposals"] if not p.get("created_issue_url")]
    if not pending:
        print("All proposals already have issues. Nothing to create.")
        return 0
    repos = sorted({p["repo"] for p in pending})
    print(f"Creating {len(pending)} issue(s) across {', '.join(repos)}…")
    for repo in repos:
        _ensure_label(repo)
    created = 0
    for p in pending:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "-R",
                p["repo"],
                "--title",
                p["title"],
                "--body",
                issue_body(p, run),
                "--label",
                ISSUE_LABEL,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ✗ {p['title']}: {result.stderr.strip()[-200:]}")
            continue
        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        p["created_issue_url"] = url
        created += 1
        print(f"  ✓ {url}")
    path.write_text(json.dumps(doc, indent=2))
    print(f"\nCreated {created}/{len(pending)} issue(s). URLs saved to {path.name}.")
    return 0 if created == len(pending) else 1


def main(argv: list[str] | None = None) -> int:
    # Imported here, not at module scope: this script runs from inside the
    # runner container, where the package is only importable via `bench_audit`.
    from harness_lab_app import llm_transport

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-K-004")
    parser.add_argument(
        "--model",
        default=os.environ.get("IMPROVE_MODEL") or llm_transport.default_audit_model(),
        help="Model for the synthesis pass (default: the auditor model).",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="File the (edited) proposals as GitHub issues. Without this, only "
        "generates the editable proposals file (dry-run).",
    )
    parser.add_argument(
        "--strictness",
        choices=sorted(STRICTNESS_LEVELS),
        default=os.environ.get("IMPROVE_STRICTNESS", DEFAULT_STRICTNESS),
        help="Evidence bar for proposals: strict = fewer, higher-signal; "
        "lenient = more permissive (default: balanced, or IMPROVE_STRICTNESS).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=os.environ.get("IMPROVE_FORCE") == "1",
        help="Bypass the outcome gate and synthesize even for a clean run "
        "(also IMPROVE_FORCE=1).",
    )
    args = parser.parse_args(argv)

    if args.strictness not in STRICTNESS_LEVELS:
        sys.exit(f"❌ Invalid strictness: {args.strictness}")
    _load_env()
    if args.create:
        return create(args.run)
    generate(args.run, args.model, strictness=args.strictness, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
