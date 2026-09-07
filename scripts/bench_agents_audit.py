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
    DIFF_CLIP_CHARS,
    RULES_CLIP_CHARS,
    call_gateway,
    clip,
    read_text_if_exists,
    resolve_runs_path,
)
from create_run import DATA_DIR, REPO_ROOT, _load_env, find_run

from harness_lab_app import llm_transport

GEMINI_RULES = REPO_ROOT / ".agents" / "rules" / "GEMINI.md"
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

SYSTEM_PROMPT = """\
Sos un editor de reglas de agentes para Google Antigravity (Gemini). Tu trabajo es
proponer ediciones CONCRETAS a las reglas del repo para el caso Google, en base a
cómo se comportó el modelo en UNA corrida de benchmark y a las reglas actuales.

Entradas que recibís: las reglas actuales (`.agents/rules/GEMINI.md`), el índice de
skills disponibles en `.agents/skills/`, y la evidencia de la corrida (diff del
código producido, transcript del agente si existe, notas del humano, score).

REGLA CENTRAL: sé PRESCRIPTIVO y accionable. No te limites a describir; proponé el
cambio. Para cada propuesta, citá la evidencia que la justifica (frase del
transcript/notas, patrón del diff, regla actual que falló). Diferenciá
explícitamente lo de ALTA CONFIANZA (evidencia clara) de las HIPÓTESIS A VALIDAR
(corazonada sin evidencia fuerte). Si una sección no tiene evidencia, decilo.

Estructurá la salida en Markdown EXACTAMENTE con estas secciones:

## Resumen
2-4 líneas: qué tan bien las reglas actuales guiaron al modelo en esta corrida.

## Ediciones propuestas a GEMINI.md
Lista priorizada. Por cada una: qué regla reescribir/agregar/quitar, el texto
sugerido (o el antes/después), y la evidencia.

## Ediciones propuestas a las skills
Skills que faltaron, que no se descubrieron/invocaron, que confundieron, o que
convendría crear/ajustar. Con evidencia.

## Gaps observados
Qué le faltó al modelo que las reglas no cubrían (instrucciones ausentes, ejemplos,
restricciones).

## Hipótesis a validar (baja confianza)
Ideas sin evidencia fuerte, marcadas como tales.

Escribí en español. Sé concreto; cada propuesta debe poder rastrearse a evidencia.
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
            "\n## Reglas actuales bajo revisión (.agents/rules/GEMINI.md)\n"
            + clip(inp["rules"].strip(), RULES_CLIP_CHARS)
        )
    if inp["skills"]:
        sections.append(
            "\n## Skills disponibles en .agents/skills/\n"
            + "\n".join(f"- {s}" for s in inp["skills"])
        )
    if inp["notes"]:
        sections.append(
            "\n## Observaciones del humano (notes.md)\n" + inp["notes"].strip()
        )
    if inp["transcript"]:
        sections.append(
            "\n## Transcript del agente (transcript.md)\n"
            + clip(inp["transcript"].strip(), RULES_CLIP_CHARS)
        )
    if inp["diff"]:
        sections.append(
            "\n## Código producido (diff.patch)\n```diff\n"
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
        help="Model alias for the auditor (default: chosen for the active transport).",
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
        args.auditor_model
        or os.environ.get("AUDITOR_MODEL")
        or llm_transport.default_audit_model()
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
