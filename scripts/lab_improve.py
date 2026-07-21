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

from bench_audit import DEFAULT_AUDITOR_MODEL, call_gateway
from create_run import DATA_DIR, _load_env

# Target repos (override via env). Read git remotes if you fork these.
TEMPLATE_REPO = os.environ.get("IMPROVE_REPO_TEMPLATE", "marcosdh1987/ml-python-base")
HARNESS_REPO = os.environ.get(
    "IMPROVE_REPO_HARNESS", "marcosdh1987/harness-engineering-guide"
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

SYSTEM_PROMPT = """\
You improve an AI engineering harness. You are given evidence from ONE benchmark
run: the behavioral audit, the structured per-item verdicts (skills / governance
docs / subagents / hooks, each judged useful | redundant | improve | unknown),
and the run context.

Produce concrete, de-duplicated IMPROVEMENT PROPOSALS for the harness — only from
real signals: items judged "improve", required governance/skills that were NOT
followed, or gaps the audit calls out. Do NOT invent problems; if a run went
well with little signal, return few or zero proposals.

Each proposal targets a documentation or skill change (not the subject task).
Output ONLY a JSON array (no prose, no code fences). Each element:

{"title": "<imperative, <=80 chars>",
 "kind": "skill" | "doc" | "subagent" | "hook" | "process",
 "target": "<exact skill/doc/subagent name, or the process area>",
 "repo_hint": "template" | "harness",
 "body": "<markdown: the problem (with evidence from THIS run), the proposed
           change, and why it helps. 2-6 short paragraphs or bullets.>"}

repo_hint: "template" for skills, governance docs, subagents, hooks that ship in
the project template; "harness" for methodology / process / cross-cutting harness
guidance. Keep it to the highest-signal proposals (at most 6).
"""


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


def synthesize(run: dict, model: str) -> list[dict]:
    improve = [f for f in run["findings"] if f.get("verdict") == "improve"]
    findings_block = "\n".join(
        f"- {f.get('name')} ({f.get('kind')}): {f.get('note', '')}"
        for f in (improve or run["findings"])
    )
    user = (
        f"# Run: {run['run_id']}\n"
        f"harness={run['harness'] or '?'} model={run['executor_model']} "
        f"case={run['case']} prompt={run['prompt_variant']}\n\n"
        f"## Structured verdicts needing improvement\n{findings_block or '(none)'}\n\n"
        f"## Behavioral audit\n{run['audit_md'][:40000]}"
    )
    raw = call_gateway(model, SYSTEM_PROMPT, user)
    proposals = _parse_proposals(raw)
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


def generate(run_id: str, model: str) -> Path:
    run = load_run(run_id)
    print(f"→ Synthesizing improvement proposals for {run_id} (model {model})…")
    proposals = synthesize(run, model)
    doc = {
        "run_id": run_id,
        "context": {
            "case": run["case"],
            "harness": run["harness"],
            "executor_model": run["executor_model"],
        },
        "generator_model": model,
        "proposals": proposals,
    }
    out = DATA_DIR / run_id
    (out / "improvements.json").write_text(json.dumps(doc, indent=2))
    preview = [f"# Improvement proposals — {run_id}\n"]
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-K-004")
    parser.add_argument(
        "--model",
        default=os.environ.get("IMPROVE_MODEL", DEFAULT_AUDITOR_MODEL),
        help="Gateway model for the synthesis pass (default: the auditor model).",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="File the (edited) proposals as GitHub issues. Without this, only "
        "generates the editable proposals file (dry-run).",
    )
    args = parser.parse_args(argv)

    _load_env()
    if args.create:
        return create(args.run)
    generate(args.run, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
