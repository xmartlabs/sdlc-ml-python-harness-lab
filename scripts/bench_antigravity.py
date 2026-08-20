#!/usr/bin/env python3
"""Prepare a human-driven Antigravity benchmark run.

Antigravity (Google's Gemini IDE / panel) is human-driven — there is no headless
CLI like opencode. This script prepares everything a human needs, then stops:

1. Registers the run (status -> running, scaffolds report.md).
2. Creates workspace/<run-id>/.
3. Generates prompt.md (resolved from the run's prompt_variant, e.g. tetris_sdlc_v1).
4. Scaffolds transcript.md and notes.md for human capture (graceful degradation:
   the prescriptive audit later uses whatever is present).
5. Copies the prompt to the clipboard and prints instructions to open Antigravity.

It does NOT touch the opencode flow: no BENCH_CONTEXT.md, no opencode launch, no
gateway virtual key (Antigravity uses Google's models, not the LiteLLM gateway).

When done in Antigravity, close the run with:
    make bench-close RUN=<run-id> SCORE=<0-10> INTERVENTIONS=<N>

Then get prescriptive suggestions for the .agents rules with:
    make bench-agents-audit RUN=<run-id>

Run with:
    make bench-antigravity RUN=tetris-v1-N-001
    uv run python scripts/bench_antigravity.py --run tetris-v1-N-001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench_run import generate_prompt, load_task_yaml, resolve_prompt_file
from bench_start import WORKSPACE_ROOT, copy_to_clipboard
from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
    find_run,
    scaffold_report,
    set_status_running,
)

TRANSCRIPT_TEMPLATE = """\
# Antigravity transcript — {run_id}

> Pegá acá el transcript / walkthrough del Agent Manager de Antigravity si lo
> podés exportar (lo que sea: la conversación, la lista de tareas, los pasos).
> Si no podés exportarlo, dejá este archivo vacío y completá notes.md — el audit
> degrada elegante y usa lo que esté presente.

"""

NOTES_TEMPLATE = """\
# Observaciones humanas — {run_id}

Completá lo que puedas observar mientras corrés Antigravity. Esto alimenta el
audit prescriptivo de `.agents/rules/GEMINI.md` y las skills.

## ¿Leyó/aplicó las reglas?
- ¿Mencionó o siguió `.agents/rules/GEMINI.md` (governance, runtime rules)?:
- ¿Qué skills descubrió o invocó?:
- ¿Qué skills relevantes ignoró o nunca tocó?:

## Desvíos de las reglas
- ¿Dónde se desvió del SDLC (plan -> implement -> test -> verify)?:
- ¿Creó/modificó archivos fuera de `workspace/{run_id}/`?:

## Fricciones
- ¿Dónde se trabó o necesitó intervención humana?:
- Intervenciones (qué tuviste que corregir a mano):

## Otros
- Screenshots dejadas en este dir (paths):
- Notas libres:
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--run", required=True, help="run_id, e.g. tetris-v1-N-001")
    p.add_argument(
        "--benchmark", default="tetris-v1", help="benchmark_id (default: tetris-v1)"
    )
    p.add_argument(
        "--prompt",
        default=None,
        help="Short prompt name override (naive | incremental | conductor | sdlc). "
        "Auto-detected from the run's prompt_variant if omitted.",
    )
    p.add_argument(
        "--no-launch",
        action="store_true",
        help="No-op (kept for parity with bench-start; this script never launches "
        "a tool — Antigravity is opened by hand).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    runs_path = RUNS_FILES.get(args.benchmark)
    if runs_path is None:
        sys.exit(f"Unknown benchmark '{args.benchmark}'. Known: {list(RUNS_FILES)}")

    run = find_run(runs_path, args.run)
    if run.get("status") == "completed":
        sys.exit(f"Run {args.run} is already completed. Use a new run_id.")

    task = load_task_yaml(runs_path)
    prompt_short, prompt_source = resolve_prompt_file(task, args.prompt, run)

    # 1. Register run (report + status)
    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (out_dir / "report.md").exists():
        scaffold_report(run, out_dir)
    set_status_running(runs_path, args.run)

    # 2. Create workspace
    workspace = WORKSPACE_ROOT / args.run
    workspace.mkdir(parents=True, exist_ok=True)

    # 3. Generate prompt (same header/format as the opencode flow, for comparability)
    prompt_path = generate_prompt(
        args.run,
        prompt_short,
        prompt_source,
        run.get("planner_model"),
        run.get("executor_model"),
        run.get("langfuse_session_id", ""),
        out_dir,
    )

    # 4. Scaffold human-capture templates (only if absent — don't clobber edits)
    transcript_path = out_dir / "transcript.md"
    if not transcript_path.exists():
        transcript_path.write_text(TRANSCRIPT_TEMPLATE.format(run_id=args.run))
    notes_path = out_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(NOTES_TEMPLATE.format(run_id=args.run))

    # 5. Copy prompt to clipboard
    clipped = copy_to_clipboard(prompt_path.read_text())

    # 6. Print summary + instructions (no tool is launched)
    rel_out = out_dir.relative_to(REPO_ROOT)
    print()
    print("=" * 66)
    print(f"  bench-antigravity: {args.run}")
    print("=" * 66)
    print(f"  Workspace:   workspace/{args.run}/")
    print(f"  Prompt:      {rel_out}/prompt.md  (variant: {prompt_short})")
    print(f"  Transcript:  {rel_out}/transcript.md  (paste the Agent Manager one)")
    print(f"  Notes:       {rel_out}/notes.md  (fill in your observations)")
    print()
    print("-" * 66)
    print("  NEXT STEP — open Antigravity by hand")
    print("-" * 66)
    print("  1. Open the project in Antigravity (IDE or panel).")
    print("     It reads .agents/rules/GEMINI.md + .agents/skills/ automatically.")
    if clipped:
        print("  2. Paste the prompt: Cmd+V  (already on the clipboard \u2713)")
    else:
        print(f"  2. Copy the prompt: cat {rel_out}/prompt.md | pbcopy")
        print("     then paste it into Antigravity.")
    print(f"  3. Do the task inside workspace/{args.run}/")
    print(f"  4. Paste the transcript into {rel_out}/transcript.md, fill notes.md")
    print()
    print("  When you are done:")
    print(f"    make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>")
    print(f"    make bench-agents-audit RUN={args.run}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
