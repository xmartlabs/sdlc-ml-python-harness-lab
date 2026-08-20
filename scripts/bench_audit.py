#!/usr/bin/env python3
"""Audit the behavior of a completed benchmark run.

An LLM auditor reads the artifacts a run already leaves in data/runs/<id>/ and
produces a *descriptive* behavioral diagnostic (not prescriptive) of how well the
opencode harness served the executor model, across four dimensions:

  1. Skill/rule usage    — which available skills/rules were invoked vs ignored
  2. Efficiency / loops  — repeated edits, wasted steps, thrashing, blind retries
  3. SDLC adherence      — did it follow plan -> implement -> test -> verify?
  4. Failure points      — where/why it got stuck: errors, crashes, abandonment

The diagnostic is written to data/runs/<id>/audit.md.

Run with:
  uv run python scripts/bench_audit.py --run tetris-v1-K-001
  uv run python scripts/bench_audit.py --run tetris-v1-K-001 --auditor-model gateway/gpt-5.5
  uv run python scripts/bench_audit.py --run tetris-v1-K-001 --dry-run

Requires env vars (can live in .env at repo root):
  GATEWAY_BASE_URL    e.g. http://localhost:4000/v1
  GATEWAY_TOKEN       bearer token for the OpenAI-compatible gateway
  AUDITOR_MODEL       optional; default gateway/gpt-5.5
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
)

from harness_lab_app.gateway_models import (
    GATEWAY_USER_AGENT,
    list_gateway_models,
    normalize_model_name,
)

# Per-artifact clip budgets (characters). Logs can be large in multi-step runs;
# we keep head+tail so both the setup and the final state are visible.
LOG_CLIP_CHARS = 60_000
DIFF_CLIP_CHARS = 40_000
RULES_CLIP_CHARS = 20_000
# LiteLLM model name (no opencode "gateway/" prefix — that's opencode-only).
# gpt-5.4-mini: current mini tier — ~6.7x cheaper than gpt-5.5 (~$0.04/audit
# vs ~$0.29) with enough judgment for log audits. Override per-run via the UI
# field or AUDITOR_MODEL; escalate to gpt-5.5 if audit depth feels shallow.
DEFAULT_AUDITOR_MODEL = "gpt-5.4-mini"

SYSTEM_PROMPT = """\
Sos un auditor de harness de agentes de código. Tu trabajo es leer los artefactos
de UNA corrida de benchmark (logs del agente, diff producido, uso, score y el
corpus de reglas que el agente tenía disponible) y producir un DIAGNÓSTICO DE
COMPORTAMIENTO descriptivo.

REGLA CENTRAL: describí, no prescribas. No propongas ediciones ni mejoras al
harness. Limitate a observar qué pasó, citando evidencia concreta del log
(frases, comandos, nombres de archivo, números de turno). Si no hay evidencia
para una dimensión, decilo explícitamente.

Estructurá la salida en Markdown EXACTAMENTE con estas cuatro secciones (en este
orden), más un cierre:

## Uso de skills/reglas
Qué skills/reglas del corpus de governance del harness indicado (ver CONTEXTO DEL
HARNESS) y del prompt el agente realmente invocó o siguió, y cuáles ignoró.

## Eficiencia / loops
Ediciones repetidas al mismo archivo, pasos desperdiciados, thrashing, vueltas en
círculo, reintentos ciegos. Relacioná con los turnos/tool calls del usage si ayuda.

## Adherencia al SDLC
¿Siguió el flujo plan -> implement -> test -> verify del prompt, o se lo salteó?
Dónde se desvió.

## Puntos de falla
Dónde y por qué se trabó: errores, crashes, permisos, comandos fallidos, abandono
de tarea.

## Observaciones notables
Bullets cortos con lo más relevante del comportamiento de esta corrida.

Escribí en español. Sé concreto y conciso; cada observación debe poder rastrearse
a la evidencia.
"""

# Second, structured pass: one verdict per harness item (skill/doc/hook/
# subagent) so the UI can render a clean catalog with useful/redundant marks.
FINDINGS_SYSTEM_PROMPT = """\
Sos un auditor de harness de agentes de código. Recibís el catálogo de ítems del
harness disponibles en una corrida (skills, documentos de governance, hooks,
subagentes) y el diagnóstico de comportamiento ya escrito para esa corrida.

Emití UN veredicto por ítem del catálogo. Respondé SOLO con un array JSON
(sin texto antes ni después, sin markdown, sin code fences). Cada elemento:

{"name": "<nombre exacto del ítem del catálogo>",
 "kind": "skill" | "doc" | "hook" | "subagent",
 "used_evidence": "<evidencia concreta de uso en esta corrida, o cadena vacía>",
 "verdict": "useful" | "redundant" | "improve" | "unknown",
 "note": "<una frase en español justificando el veredicto>"}

Criterio: "useful" = hay evidencia de que el ítem aportó al resultado;
"redundant" = estuvo disponible pero no se usó y nada sugiere que faltara;
"improve" = se usó o se necesitó pero funcionó mal, confundió o quedó a medias;
"unknown" = no hay evidencia suficiente para juzgar (preferí unknown antes que
inventar). No inventes ítems fuera del catálogo dado.
"""

# Per-harness governance source + log characteristics, injected into the auditor
# system prompt so it judges the run against the RIGHT harness (not always
# opencode) and does not penalize thin logs from summary-only CLIs.
_HARNESS_PROFILE: dict[str, str] = {
    "opencode": (
        "opencode (opencode CLI). Corpus de governance: OPENCODE.md + el bloque de "
        "skills generado, .opencode/skills/*/SKILL.md, .github/{architecture,"
        "standards,domain-boundaries}.md y memory/*. Los logs son TRAZAS POR-TOOL "
        "(permission=read|edit|bash, agent=<modo>), así que la evidencia de "
        "tools/archivos/ediciones ES observable — su ausencia es señal real."
    ),
    "claude": (
        "claude (Claude Code CLI, auth nativa). Corpus de governance: CLAUDE.md + "
        ".claude/skills/*/SKILL.md + .github/* + memory/* (NO OPENCODE.md — eso es de "
        "opencode). IMPORTANTE: Claude Code emite un RESUMEN FINAL en lenguaje "
        "natural por paso, NO trazas por-tool ni diff, y usage.json trae "
        "tool_calls/turns/files_modified/diff_lines en null por diseño. Tratá la "
        "falta de trazas de tools, diffs y listas de archivos como una LIMITACIÓN "
        "DE LOGGING DEL HARNESS, nunca como evidencia de que el agente no trabajó; "
        "juzgá el comportamiento por los resúmenes."
    ),
    "codex": (
        "codex (OpenAI Codex CLI, auth nativa). Corpus de governance: AGENTS.md + sus "
        "skills + .github/* (NO OPENCODE.md). Igual que claude, codex emite salida "
        "tipo resumen, no trazas por-tool; la falta de tool_calls/diff en usage.json "
        "es una limitación de logging del harness, no una falla del agente."
    ),
}


# The adapter file whose rules the audited agent actually saw, per harness.
# Paths are relative to REPO_ROOT (the bench working tree mirrors the template's
# adapter layout). An unmapped harness falls back to OPENCODE.md with an
# explicit warning in the audit inputs.
_HARNESS_RULES_FILE: dict[str, str] = {
    "opencode": "OPENCODE.md",
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "antigravity": ".agents/rules/GEMINI.md",
}


def harness_from_metadata(metadata_text: str | None) -> str:
    """The harness id recorded in metadata.json (empty if absent)."""
    if not metadata_text:
        return ""
    try:
        return str(json.loads(metadata_text).get("harness", "") or "")
    except json.JSONDecodeError:
        return ""


def harness_from_log(log_text: str | None) -> str:
    """Fallback: read the harness from the run-log header (`# <harness> run started`).

    Covers runs whose metadata predates harness recording.
    """
    if not log_text:
        return ""
    match = re.search(r"^# (\w+) run started", log_text, re.MULTILINE)
    return match.group(1) if match else ""


def detect_harness(metadata_text: str | None, log_text: str | None) -> str:
    """Harness id from metadata, falling back to the run-log header."""
    return harness_from_metadata(metadata_text) or harness_from_log(log_text)


def harness_preamble(harness: str) -> str:
    """Harness-aware preamble prepended to the auditor system prompts."""
    profile = _HARNESS_PROFILE.get(harness) or (
        f"'{harness or 'desconocido'}'. Juzgalo contra el corpus de governance "
        "visible en los artefactos; si el log es sólo-resumen, no trates la falta "
        "de trazas de tools/diffs como prueba de que no hubo trabajo."
    )
    return (
        "CONTEXTO DEL HARNESS (crítico): la corrida fue conducida por el harness "
        f"{profile}\n\nEvaluá SOLO contra el corpus de governance de ESTE harness. "
        "Ítems del catálogo que pertenecen a otro harness (p. ej. OPENCODE.md en una "
        "corrida claude/codex) NO son aplicables: marcalos 'unknown' o 'redundant', "
        "nunca 'improve'.\n\n"
    )


VALID_FINDING_KINDS = {"skill", "doc", "hook", "subagent"}
VALID_FINDING_VERDICTS = {"useful", "redundant", "improve", "unknown"}


def harness_catalog_from_metadata(metadata_text: str | None) -> list[dict]:
    """Build the item catalog (skills/docs/hooks/subagents) from metadata.json."""
    if not metadata_text:
        return []
    try:
        meta = json.loads(metadata_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(meta, dict):
        return []
    items: list[dict] = []
    for name in meta.get("skills_bundle") or []:
        items.append({"name": str(name), "kind": "skill"})
    for name in meta.get("subagents") or []:
        items.append({"name": str(name), "kind": "subagent"})
    fingerprint = meta.get("harness_fingerprint") or {}
    for name in fingerprint.get("governance_docs") or {}:
        items.append({"name": str(name), "kind": "doc"})
    for name in fingerprint.get("hooks") or {}:
        items.append({"name": str(name), "kind": "hook"})
    return items


def build_findings_user_message(
    run_id: str, catalog: list[dict], audit_body: str
) -> str:
    catalog_lines = "\n".join(f"- {item['name']} ({item['kind']})" for item in catalog)
    return (
        f"# Corrida: {run_id}\n\n"
        f"## Catálogo de ítems del harness (emití un veredicto por cada uno)\n"
        f"{catalog_lines}\n\n"
        f"## Diagnóstico de comportamiento de la corrida\n{audit_body.strip()}"
    )


def parse_findings(text: str) -> dict:
    """Tolerant parse of the findings response into {"items": [...]}.

    Never raises: strips code fences, accepts a bare list or an {"items": []}
    wrapper, drops invalid entries, coerces unknown verdicts/kinds. On total
    failure returns {"items": [], "parse_error": <reason>} so the audit itself
    is never blocked by a malformed findings response.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop the opening fence line (``` or ```json) and a trailing fence.
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    # Fall back to the outermost array if the model wrapped it in prose.
    if not cleaned.startswith(("[", "{")):
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return {"items": [], "parse_error": f"invalid JSON: {exc}"}
    raw_items = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_items, list):
        return {"items": [], "parse_error": "response is not a JSON array"}
    items: list[dict] = []
    for entry in raw_items:
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        verdict = str(entry.get("verdict", "")).strip().lower()
        items.append(
            {
                "name": str(entry["name"]).strip(),
                "kind": kind if kind in VALID_FINDING_KINDS else "skill",
                "used_evidence": str(entry.get("used_evidence", "")).strip(),
                "verdict": verdict if verdict in VALID_FINDING_VERDICTS else "unknown",
                "note": str(entry.get("note", "")).strip(),
            }
        )
    return {"items": items}


def resolve_runs_path(run_id: str) -> Path:
    """Map a run id to its runs.yaml by matching the known case prefixes.

    Registered benchmarks (e.g. ``tetris-v1``) return their real runs.yaml.
    SWE-bench (ADR-0009) and web-lane (ADR-0006) runs are never registered in a
    runs.yaml; rather than abort, return a non-existent placeholder so the caller
    falls through to ``resolve_or_synthesize_run``, which rebuilds the run from
    the run's ``metadata.json``. Mirrors ``bench_run.py`` setting ``runs_path``
    to a synthesis fallback for the repo-task lane.
    """
    for case, path in RUNS_FILES.items():
        if run_id.startswith(case):
            return path
    return DATA_DIR / run_id / "runs.yaml"


def clip(text: str, budget: int) -> str:
    """Clip text to budget chars keeping head+tail, marking the omission."""
    if len(text) <= budget:
        return text
    half = budget // 2
    omitted = len(text) - budget
    return text[:half] + f"\n\n[... {omitted} chars omitidos ...]\n\n" + text[-half:]


def read_text_if_exists(path: Path) -> str | None:
    if path.exists() and path.is_file():
        try:
            return path.read_text(errors="replace")
        except Exception:
            return None
    return None


def gather_logs(out_dir: Path) -> tuple[str, list[str]]:
    """Return (combined_log_text, sources). Prefers per-step logs, else opencode.log."""
    sources: list[str] = []
    parts: list[str] = []
    steps_dir = out_dir / "steps"
    step_logs = sorted(steps_dir.glob("step-*.log")) if steps_dir.is_dir() else []
    if step_logs:
        for sl in step_logs:
            txt = read_text_if_exists(sl)
            if txt:
                parts.append(f"===== {sl.name} =====\n{txt}")
                sources.append(str(sl.relative_to(REPO_ROOT)))
    else:
        txt = read_text_if_exists(out_dir / "opencode.log")
        if txt:
            parts.append(txt)
            sources.append(str((out_dir / "opencode.log").relative_to(REPO_ROOT)))
    return "\n\n".join(parts), sources


def build_inputs(run: dict, out_dir: Path) -> dict:
    """Collect every artifact the auditor reads. Returns a structured dict."""
    metadata = read_text_if_exists(out_dir / "metadata.json")
    usage = read_text_if_exists(out_dir / "usage.json")
    diff = read_text_if_exists(out_dir / "diff.patch")
    prompt = read_text_if_exists(out_dir / "prompt.md")
    log_text, log_sources = gather_logs(out_dir)

    # The rules corpus must match the harness that drove the run — auditing a
    # claude/codex run against OPENCODE.md judges it by the wrong rulebook.
    harness = detect_harness(metadata, log_text)
    rules_name = _HARNESS_RULES_FILE.get(harness)
    rules_warning = ""
    if rules_name is None:
        rules_name = "OPENCODE.md"
        rules_warning = (
            f"harness '{harness or 'unknown'}' has no mapped rules file; "
            "defaulting to OPENCODE.md — treat rule-adherence findings with care"
        )
    rules = read_text_if_exists(REPO_ROOT / rules_name)

    return {
        "metadata": metadata,
        "usage": usage,
        "diff": diff,
        "prompt": prompt,
        "rules": rules,
        "rules_source": rules_name,
        "rules_warning": rules_warning,
        "harness": harness,
        "log_text": log_text,
        "log_sources": log_sources,
        "score": run.get("result_level"),
        "interventions": run.get("human_interventions"),
        "executor_model": run.get("executor_model"),
        "prompt_variant": run.get("prompt_variant"),
    }


def build_user_message(run_id: str, inp: dict) -> str:
    """Assemble the single user message fed to the auditor."""
    sections = [
        f"# Corrida a auditar: {run_id}",
        f"- Modelo ejecutor: {inp['executor_model']}",
        f"- Prompt variant: {inp['prompt_variant']}",
        f"- Score: {inp['score']}",
        f"- Human interventions: {inp['interventions']}",
        f"- Fuentes de log: {', '.join(inp['log_sources']) or 'ninguna'}",
    ]
    if inp["metadata"]:
        sections.append(
            "\n## metadata.json\n```json\n" + inp["metadata"].strip() + "\n```"
        )
    if inp["usage"]:
        sections.append("\n## usage.json\n```json\n" + inp["usage"].strip() + "\n```")
    if inp["rules"]:
        rules_header = f"\n## Corpus de reglas disponible ({inp['rules_source']})\n"
        if inp.get("rules_warning"):
            rules_header += f"> ⚠️ {inp['rules_warning']}\n"
        sections.append(rules_header + clip(inp["rules"].strip(), RULES_CLIP_CHARS))
    if inp["prompt"]:
        sections.append(
            "\n## Prompt entregado al agente (prompt.md)\n"
            + clip(inp["prompt"].strip(), RULES_CLIP_CHARS)
        )
    if inp["diff"]:
        sections.append(
            "\n## Código producido (diff.patch)\n```diff\n"
            + clip(inp["diff"].strip(), DIFF_CLIP_CHARS)
            + "\n```"
        )
    if inp["log_text"]:
        sections.append(
            "\n## Log de comportamiento del agente\n```\n"
            + clip(inp["log_text"].strip(), LOG_CLIP_CHARS)
            + "\n```"
        )
    return "\n".join(sections)


def call_gateway(model: str, system: str, user: str) -> str:
    base = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
    token = os.environ.get("GATEWAY_TOKEN", "")
    if not base or not token:
        sys.exit(
            "❌ GATEWAY_BASE_URL or GATEWAY_TOKEN not set. "
            "Add them to .env or run with --dry-run."
        )
    url = f"{base}/chat/completions"
    payload = {
        "model": normalize_model_name(model),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The gateway sits behind Cloudflare, which bans the default
            # "Python-urllib/x.y" user-agent (error 1010). Send a normal UA.
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        msg = f"❌ Gateway HTTP {e.code}: {body}"
        if e.code == 400 and "model" in body.lower():
            available = list_gateway_models()
            if available:
                msg += "\n\nAvailable models (use the name without 'gateway/'):\n"
                msg += "\n".join(f"  {m}" for m in available)
        sys.exit(msg)
    except Exception as e:
        sys.exit(f"❌ Gateway request failed: {e}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        sys.exit(f"❌ Unexpected gateway response: {json.dumps(data)[:500]}")


def render_audit(run_id: str, inp: dict, auditor_model: str, body: str) -> str:
    today = _dt.date.today().isoformat()
    header = "\n".join(
        [
            f"# Audit — {run_id}",
            "",
            f"- Run id: {run_id}",
            f"- Executor model: {inp['executor_model']}",
            f"- Prompt variant: {inp['prompt_variant']}",
            f"- Score: {inp['score']}",
            f"- Human interventions: {inp['interventions']}",
            f"- Auditor model: {auditor_model}",
            f"- Date: {today}",
            f"- Log sources: {', '.join(inp['log_sources']) or 'none'}",
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
    p.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-K-001")
    p.add_argument(
        "--auditor-model",
        default=None,
        help=f"Gateway model alias for the auditor (default: {DEFAULT_AUDITOR_MODEL})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected inputs and the assembled request; do not call the "
        "gateway or write audit.md",
    )
    return p.parse_args(argv)


def resolve_or_synthesize_run(runs_path: Path, run_id: str, out_dir: Path) -> dict:
    """Return the run dict from runs.yaml, or synthesize one from metadata.json.

    Web-lane runs (ADR-0006) are not registered in runs.yaml. The auditor only
    needs a few optional context fields (executor model, prompt variant, score);
    the full metadata.json is fed into the prompt anyway, so a minimal dict is
    enough. Mirrors bench_run.resolve_or_synthesize_run.
    """
    if runs_path.exists():
        data = yaml.safe_load(runs_path.read_text()) or {}
        for run in data.get("runs", []):
            if run.get("run_id") == run_id:
                return run
    synthesized: dict = {"run_id": run_id}
    meta_path = out_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
        if isinstance(meta, dict):
            condition = meta.get("model_condition") or {}
            synthesized["executor_model"] = condition.get("executor_model") or meta.get(
                "executor_model"
            )
            synthesized["prompt_variant"] = meta.get("prompt_variant")
    return synthesized


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    auditor_model = (
        args.auditor_model or os.environ.get("AUDITOR_MODEL") or DEFAULT_AUDITOR_MODEL
    )

    runs_path = resolve_runs_path(args.run)
    out_dir = DATA_DIR / args.run
    run = resolve_or_synthesize_run(runs_path, args.run, out_dir)
    if not out_dir.is_dir():
        sys.exit(
            f"❌ No artifacts found at {out_dir.relative_to(REPO_ROOT)}/\n"
            f"   Execute the run first (e.g. make bench-run ...) so it leaves logs."
        )

    inp = build_inputs(run, out_dir)
    if not inp["log_text"]:
        sys.exit(
            f"❌ No log found in {out_dir.relative_to(REPO_ROOT)}/ "
            f"(expected opencode.log or steps/step-*.log). Nothing to audit."
        )

    user_msg = build_user_message(args.run, inp)

    if args.dry_run:
        print("🔍 DRY RUN — no gateway call, no file written\n")
        print(f"  Run:           {args.run}")
        print(f"  Auditor model: {auditor_model}")
        print(f"  Out dir:       {out_dir.relative_to(REPO_ROOT)}/")
        print(f"  Score:         {inp['score']}")
        print(f"  Log sources:   {', '.join(inp['log_sources']) or 'none'}")
        print("  Artifacts detected:")
        for name in ["metadata", "usage", "diff", "prompt", "rules"]:
            print(f"    {name:10s} {'✓' if inp[name] else '—'}")
        print(f"\n  User message size: {len(user_msg)} chars")
        print(f"  Would write:   {(out_dir / 'audit.md').relative_to(REPO_ROOT)}")
        return 0

    harness = inp["harness"]
    preamble = harness_preamble(harness)
    print(f"🔎 Auditing {args.run} ({harness or 'harness?'}) with {auditor_model} ...")
    body = call_gateway(auditor_model, preamble + SYSTEM_PROMPT, user_msg)
    audit_md = render_audit(args.run, inp, auditor_model, body)
    audit_path = out_dir / "audit.md"
    audit_path.write_text(audit_md)
    print(f"✅ Wrote {audit_path.relative_to(REPO_ROOT)}")

    # Second, structured pass (per-item verdicts). Best-effort by design: the
    # audit is already written, so findings trouble must never fail the run.
    catalog = harness_catalog_from_metadata(inp["metadata"])
    findings: dict = {"items": []}
    if catalog:
        try:
            findings_text = call_gateway(
                auditor_model,
                preamble + FINDINGS_SYSTEM_PROMPT,
                build_findings_user_message(args.run, catalog, body),
            )
            findings = parse_findings(findings_text)
        except SystemExit as exc:
            # call_gateway sys.exit()s on HTTP trouble; downgrade to a note.
            findings = {"items": [], "parse_error": f"gateway error: {exc}"}
    else:
        findings = {"items": [], "parse_error": "no harness catalog in metadata.json"}
    findings_path = out_dir / "audit_findings.json"
    findings_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2))
    print(
        f"✅ Wrote {findings_path.relative_to(REPO_ROOT)} "
        f"({len(findings['items'])} item verdicts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
