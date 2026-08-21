#!/usr/bin/env python3
"""Prescriptive audit of the .agents (Antigravity/Gemini) rules from a run.

Given a human-driven Antigravity benchmark run, an LLM reads how the model
behaved (diff, transcript, human notes, score) AGAINST the current rules
(.agents/rules/GEMINI.md + the skills index) and proposes concrete edits to
improve those rules for the Google case.

Unlike scripts/bench_audit.py (descriptive diagnostic of opencode runs), this is
PRESCRIPTIVE: it tells you what to change in GEMINI.md and the skills.

Inputs degrade gracefully — it uses whatever the run left in data/runs/<id>/
(diff.patch, transcript.md, notes.md). The diagnostic is written to
data/runs/<id>/agents_audit.md.

Run with:
    make bench-agents-audit RUN=tetris-v1-N-001
    uv run python scripts/bench_agents_audit.py --run tetris-v1-N-001 --dry-run

Requires env vars (can live in .env at repo root):
    GATEWAY_BASE_URL    e.g. https://.../v1
    GATEWAY_TOKEN       bearer token for the OpenAI-compatible gateway
    AUDITOR_MODEL       optional; default a strong cloud model
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench_audit import (
    DEFAULT_AUDITOR_MODEL,
    DIFF_CLIP_CHARS,
    RULES_CLIP_CHARS,
    call_gateway,
    clip,
    read_text_if_exists,
    resolve_runs_path,
)
from create_run import DATA_DIR, REPO_ROOT, _load_env, find_run

GEMINI_RULES = REPO_ROOT / ".agents" / "rules" / "GEMINI.md"
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

SYSTEM_PROMPT = """\
You are an agent-rules editor for Google Antigravity (Gemini). Your job is to
propose CONCRETE edits to this repository's rules for the Google case, based on how
the model behaved in ONE benchmark run and on the current rules.

Inputs you receive: the current rules (`.agents/rules/GEMINI.md`), the index of
skills available under `.agents/skills/`, and the run's evidence (diff of the code
produced, the agent transcript if one exists, human notes, score).

CENTRAL RULE: be PRESCRIPTIVE and actionable. Do not merely describe; propose the
change. For every proposal, cite the evidence that justifies it (a phrase from the
transcript or notes, a pattern in the diff, the current rule that failed). Separate
HIGH CONFIDENCE items (clear evidence) explicitly from HYPOTHESES TO VALIDATE
(hunches without strong evidence). If a section has no evidence, say so.

Structure the output as Markdown with EXACTLY these sections:

## Summary
2-4 lines: how well the current rules guided the model in this run.

## Proposed edits to GEMINI.md
A prioritized list. For each: which rule to rewrite, add, or remove, the suggested
text (or a before/after), and the evidence.

## Proposed edits to the skills
Skills that were missing, that were not discovered or invoked, that confused the
model, or that are worth creating or adjusting. With evidence.

## Observed gaps
What the model lacked that the rules did not cover (missing instructions, examples,
constraints).

## Hypotheses to validate (low confidence)
Ideas without strong evidence, marked as such.

Write in English. Be concrete; every proposal must be traceable to evidence.
"""


def list_agents_skills() -> list[str]:
    """Skill names available to Antigravity under .agents/skills/."""
    if not AGENTS_SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in AGENTS_SKILLS_DIR.iterdir() if p.is_dir())


def build_inputs(run: dict, out_dir: Path) -> dict:
    """Collect the rules under review plus whatever run evidence exists."""
    return {
        "rules": read_text_if_exists(GEMINI_RULES),
        "skills": list_agents_skills(),
        "diff": read_text_if_exists(out_dir / "diff.patch"),
        "transcript": read_text_if_exists(out_dir / "transcript.md"),
        "notes": read_text_if_exists(out_dir / "notes.md"),
        "score": run.get("result_level"),
        "interventions": run.get("human_interventions"),
        "executor_model": run.get("executor_model"),
        "prompt_variant": run.get("prompt_variant"),
    }


def build_user_message(run_id: str, inp: dict) -> str:
    sections = [
        f"# Corrida Antigravity a auditar: {run_id}",
        f"- Modelo: {inp['executor_model']}",
        f"- Prompt variant: {inp['prompt_variant']}",
        f"- Score: {inp['score']}",
        f"- Human interventions: {inp['interventions']}",
    ]
    if inp["rules"]:
        sections.append(
            "\n## Current rules under review (.agents/rules/GEMINI.md)\n"
            + clip(inp["rules"].strip(), RULES_CLIP_CHARS)
        )
    if inp["skills"]:
        sections.append(
            "\n## Skills available under .agents/skills/\n"
            + "\n".join(f"- {s}" for s in inp["skills"])
        )
    if inp["notes"]:
        sections.append("\n## Human observations (notes.md)\n" + inp["notes"].strip())
    if inp["transcript"]:
        sections.append(
            "\n## Agent transcript (transcript.md)\n"
            + clip(inp["transcript"].strip(), RULES_CLIP_CHARS)
        )
    if inp["diff"]:
        sections.append(
            "\n## Code produced (diff.patch)\n```diff\n"
            + clip(inp["diff"].strip(), DIFF_CLIP_CHARS)
            + "\n```"
        )
    return "\n".join(sections)


def render_audit(run_id: str, inp: dict, auditor_model: str, body: str) -> str:
    today = _dt.date.today().isoformat()
    header = "\n".join(
        [
            f"# Agents audit (prescriptivo) — {run_id}",
            "",
            f"- Run id: {run_id}",
            f"- Model under test: {inp['executor_model']}",
            f"- Prompt variant: {inp['prompt_variant']}",
            f"- Score: {inp['score']}",
            f"- Human interventions: {inp['interventions']}",
            f"- Auditor model: {auditor_model}",
            f"- Date: {today}",
            "",
            "---",
            "",
        ]
    )
    return header + body.strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-N-001")
    p.add_argument(
        "--auditor-model",
        default=None,
        help=f"Gateway model alias for the auditor (default: {DEFAULT_AUDITOR_MODEL})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected inputs and the assembled request; do not call the "
        "gateway or write agents_audit.md",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    auditor_model = (
        args.auditor_model or os.environ.get("AUDITOR_MODEL") or DEFAULT_AUDITOR_MODEL
    )

    runs_path = resolve_runs_path(args.run)
    run = find_run(runs_path, args.run)

    out_dir = DATA_DIR / args.run
    if not out_dir.is_dir():
        sys.exit(
            f"❌ No artifacts found at {out_dir.relative_to(REPO_ROOT)}/\n"
            f"   Run it first: make bench-antigravity RUN={args.run}"
        )

    inp = build_inputs(run, out_dir)
    if not inp["rules"]:
        sys.exit(
            f"❌ Rules file not found at {GEMINI_RULES.relative_to(REPO_ROOT)}.\n"
            f"   Generate the .agents view first: make setup-antigravity-skills"
        )
    if not (inp["diff"] or inp["transcript"] or inp["notes"]):
        sys.exit(
            f"❌ No run evidence in {out_dir.relative_to(REPO_ROOT)}/ "
            f"(expected diff.patch, transcript.md or notes.md). Nothing to audit."
        )

    user_msg = build_user_message(args.run, inp)

    if args.dry_run:
        print("🔍 DRY RUN — no gateway call, no file written\n")
        print(f"  Run:           {args.run}")
        print(f"  Auditor model: {auditor_model}")
        print(f"  Out dir:       {out_dir.relative_to(REPO_ROOT)}/")
        print(f"  Score:         {inp['score']}")
        print(f"  Skills found:  {len(inp['skills'])}")
        print("  Evidence detected:")
        for name in ["rules", "diff", "transcript", "notes"]:
            print(f"    {name:11s} {'✓' if inp[name] else '—'}")
        print(f"\n  User message size: {len(user_msg)} chars")
        print(
            f"  Would write:   {(out_dir / 'agents_audit.md').relative_to(REPO_ROOT)}"
        )
        return 0

    print(f"🔎 Auditing .agents rules from {args.run} with {auditor_model} ...")
    body = call_gateway(auditor_model, SYSTEM_PROMPT, user_msg)
    audit_md = render_audit(args.run, inp, auditor_model, body)
    audit_path = out_dir / "agents_audit.md"
    audit_path.write_text(audit_md)
    print(f"✅ Wrote {audit_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
