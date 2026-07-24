# Web UI guide — Agentic Harness Lab

The local web app (Vite on `http://localhost:5173`, FastAPI + Celery behind it)
is the operator surface for the lab: launch benchmark runs, watch them execute in
isolated containers, score them, and compare harnesses and models. This guide
walks the end-to-end flow and what each screen does.

Start it with `make app-up` (brings up api + worker + redis + frontend).

## The core loop

```
pick a Case → choose model + harness + prompt → Create run
   → watch it execute (logs stream live)
   → it finishes: SWE-bench auto-scores; others wait for a manual score
   → Compare runs across models / conditions
```

Everything the web app runs also has a CLI equivalent (`make bench-run …`); the
form shows the equivalent command as a reference. The web lane and the CLI are
two front-ends over the same runner (`scripts/bench_run.py`).

## Screens

### Dashboard
Landing overview of recent activity. Start here to see run state at a glance.

### Runs
Where you launch and track runs. The **New run** form:

- **Case** — what the agent is asked to do (see Cases). Selecting a case sets the
  available **Prompt variants**.
- **Harness** — the agent scaffold under test: `opencode` (containerized,
  gateway models) or `codex` / `claude` (run on your host CLI with native auth).
  To test claude/codex, follow the
  [claude/codex runbook](runbook-claude-codex.md); design in
  [harness-adapters.md](harness-adapters.md).
- **Planner / Executor model** — populated **live from your LiteLLM gateway**. Add
  a model to the gateway, run `make sync-gateway-models`, and it appears here (and
  becomes routable by opencode). No hand-edited lists. See
  [ADR-0012](adr/0012-dynamic-gateway-model-list.md).
- **Score mode** — web runs are `manual` (interactive is CLI-only). SWE-bench
  cases auto-score objectively regardless.
- **Max steps**, **Resume**, **Dry run** — run controls.
- **Skills** ablation — disable governed skills or enable experimental staged
  skills *for this run only*, to A/B the harness. A non-empty ablation makes the
  run a distinct arm (`condition_hash`). See [ADR-0011](adr/0011-per-run-skill-ablation-and-staging.md).

Create the run and you land on its **detail page**: live logs, artifacts, the
validation & score panel, attribution, LLM audit, and in-platform sandboxes
(web terminal / desktop) to poke at the produced workspace.

### Cases
The catalog of tasks. Three ways to get realistic cases:

- **tetris-v1** — the seeded pilot (build a game; manual score + smoke floor).
- **SWE-bench** — import a real GitHub issue+fix from SWE-bench Lite/Verified via
  the *Import SWE-bench* box or `make swebench-import INSTANCE=<id>`. These
  **auto-score objectively** (resolved → 10). Only a validated subset runs in the
  generic runtime — see [swebench-curated-set.md](swebench-curated-set.md).
- **Objective agentic cases** — dataset-free `build_from_scratch` tasks with
  exit-code validation, e.g. `benchmarks/cli-textstats/`
  (`make case-import TASK=…`). Great for trying a new model on a self-contained
  task with an objective floor.

### Skills
Two tiers: the **governed catalog** (read-only, shipped with the harness) and
**staged skills** (experimental SKILL.md files you can create, test per-run via
the ablation panel, and promote). The page includes a how-to for the A/B loop.

### Improvements
Cross-run worklist for the improvement cycle: after a run's proposals are
synthesized, the **Guardar** button on the run detail snapshots them here (one
row per proposal, with run date and template version for comparing iterations).
Select any subset — across runs and cases — and **Generar issue combinado**
consolidates them, via one LLM pass plus the sanitizer pipeline, into a single
prefilled ml-python-base issue. Details in
[improvement-cycle.md](improvement-cycle.md).

### Compare
Line up runs of the same case across models or harness conditions:

- Pick 2–4 runs; the **metrics table** shows case, models, prompt, objective
  result, final/composite score, tokens, cost, time, steps, and interventions —
  with the best cell per row highlighted.
- The **Leaderboard** aggregates all scored runs by *case × executor model* (mean
  and best final score), so you can see which model wins a case at a glance.

Typical use: run the same case with a new model and a previous one, then open
Compare to see the objective and cost deltas.

### Settings
Runtime facts (repo mount, SQLite location, Vite proxy) and the light/dark theme
toggle.

## Continuous-improvement cycle

The lab feeds improvements back to your harness repos: after a run's LLM audit,
`make improve RUN=<id>` synthesizes GitHub issue proposals from the "needs
improvement" signals (skills, governance docs, subagents), and `CREATE=1` files
them in `ml-python-base` / `harness-engineering-guide` on your approval. See
[improvement-cycle.md](improvement-cycle.md).

## Testing a new model — worked example

1. Register the model in your LiteLLM gateway (your ai-gateway).
2. `make sync-gateway-models` — it flows into `opencode.json` and the run form.
3. Runs → New run → pick a case (e.g. `pytest-dev__pytest-10051` or
   `cli-textstats-v1`), set **Executor model** to the new model, Create run.
4. Repeat with the previous model (same case + prompt).
5. Compare → select both runs → read the objective/score/cost deltas.
