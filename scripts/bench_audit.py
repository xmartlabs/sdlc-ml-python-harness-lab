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
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
    find_run,
)

# Per-artifact clip budgets (characters). Logs can be large in multi-step runs;
# we keep head+tail so both the setup and the final state are visible.
LOG_CLIP_CHARS = 60_000
DIFF_CLIP_CHARS = 40_000
RULES_CLIP_CHARS = 20_000
# LiteLLM model name (no opencode "gateway/" prefix — that's opencode-only).
DEFAULT_AUDITOR_MODEL = "gpt-5.5"
# Cloudflare in front of the gateway bans the default urllib user-agent
# (HTTP 1010 browser_signature_banned). Override with a real browser UA.
GATEWAY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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
Qué skills/reglas del corpus disponible (OPENCODE.md y el prompt) el agente
realmente invocó o siguió, y cuáles ignoró o nunca tocó.

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


def normalize_model_name(model: str) -> str:
    """Strip the opencode-only "gateway/" prefix so LiteLLM gets its real name.

    opencode addresses models as "<provider>/<model>" (e.g. gateway/gpt-5.5), but
    LiteLLM's /chat/completions expects the registered name without that provider
    prefix (e.g. gpt-5.5). Other prefixes (openai/...) are left untouched.
    """
    if model.startswith("gateway/"):
        return model[len("gateway/") :]
    return model


def list_gateway_models() -> list[str]:
    """Best-effort fetch of available model names from the gateway."""
    base = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
    token = os.environ.get("GATEWAY_TOKEN", "")
    if not base or not token:
        return []
    req = urllib.request.Request(
        f"{base}/models",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            data = json.loads(resp.read())
        return sorted(m["id"] for m in data.get("data", []))
    except Exception:
        return []


def resolve_runs_path(run_id: str) -> Path:
    """Map a run id to its runs.yaml by matching the known case prefixes."""
    for case, path in RUNS_FILES.items():
        if run_id.startswith(case):
            return path
    sys.exit(
        f"Could not resolve a runs.yaml for run '{run_id}'.\n"
        f"Known cases: {list(RUNS_FILES.keys())}"
    )


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
    rules = read_text_if_exists(REPO_ROOT / "OPENCODE.md")
    log_text, log_sources = gather_logs(out_dir)

    return {
        "metadata": metadata,
        "usage": usage,
        "diff": diff,
        "prompt": prompt,
        "rules": rules,
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
        sections.append(
            "\n## Corpus de reglas disponible (OPENCODE.md)\n"
            + clip(inp["rules"].strip(), RULES_CLIP_CHARS)
        )
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

    print(f"🔎 Auditing {args.run} with {auditor_model} ...")
    body = call_gateway(auditor_model, SYSTEM_PROMPT, user_msg)
    audit_md = render_audit(args.run, inp, auditor_model, body)
    audit_path = out_dir / "audit.md"
    audit_path.write_text(audit_md)
    print(f"✅ Wrote {audit_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
