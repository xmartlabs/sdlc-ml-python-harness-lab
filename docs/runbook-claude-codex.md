# Runbook — testing with the claude and codex harnesses

How to run benchmark cases against the **claude** (Claude Code) and **codex**
(OpenAI Codex) harnesses. These drive their own host CLI with your native login,
so the lab runs them in **host mode** (subprocess backend), not the containerized
`make app-up` used for opencode / SWE-bench. Design details live in
[harness-adapters.md](harness-adapters.md); this is the step-by-step.

## 0. Prerequisites (once)

- The CLIs installed and **logged in** on your machine:
  - `claude` (Claude Code) — logged into your Claude account (OAuth).
  - `codex` (OpenAI Codex) — logged into your OpenAI account.
- Verify they're on your PATH: `command -v claude codex`.
- `~/.local/bin` (or wherever they live) must be on the PATH of the shell you
  start `make app-host` from — the worker inherits it to find the CLI.

## 1. Start host mode (one command)

```bash
make app-host
```

Brings up redis + the Celery worker + the API + the frontend on the host, with
`HARNESS_LAB_EXECUTION_BACKEND=subprocess`. Ctrl-C stops everything. Open
<http://localhost:5173>.

> opencode and SWE-bench runs use the **other** mode (`make app-up`, Docker).
> You run one mode at a time — pick by the harness you're testing.

## 2. Create a run

On the **Runs** page → New run:

1. **Case** — e.g. `tetris-v1` (game build, manual score + objective smoke) or
   `cli-textstats-v1` (pure-stdlib CLI, fully objective — a good clean probe).
2. **Harness** — `claude` or `codex`.
3. **Executor model** — the dropdown now shows that CLI's **native models**:
   - claude → Claude Sonnet 5 / Opus 4.8 / Haiku 4.5
   - codex → `codex-mini-latest` (add your ids in `HARNESS_NATIVE_MODELS`,
     `frontend/src/pages/Runs.tsx`)

   The selected id is passed straight to the CLI (`claude --model …` /
   `codex -m …`). Planner model is unused for these harnesses.
4. **Prompt variant** (e.g. `sdlc`), **Max steps**, then **Create run**.

The run streams into its detail page. Under the hood it runs, on your host:

```
claude -p --dangerously-skip-permissions --model claude-sonnet-5 "<prompt>"
codex  --quiet --full-auto -m codex-mini-latest "<prompt>"
```

with `cwd` at the repo root; the prompt tells the agent to build in
`workspace/<run_id>/`.

## 3. Score + validate

- Web runs finish in **`awaiting_manual_score`**. On the run detail, set a score
  (0–10) + interventions and submit — it moves to `completed`.
- **Objective validation** (the smoke / test floor) runs automatically and shows
  under *Validation & score*; use **Re-validate** to re-run it. On macOS the GNU
  `timeout` wrapper is handled automatically (`gtimeout` or dropped).
- **LLM audit** (optional) — click *Run audit* for a qualitative diagnostic
  (skill/SDLC/efficiency/failure) written by a strong model against the logs.

## 4. Compare

**Compare** page → pick the runs (same case, different harness/model) to see
objective result, score, tokens, steps side by side, plus the leaderboard.
Typical loop: run `tetris-v1` with `claude`+Opus, `claude`+Sonnet, and
`codex`, then compare.

## Gotchas / limits

- **Sandboxes (Terminal/Desktop) need Docker** — they don't work in host mode.
  You don't need them: the produced code is on disk at
  `data/runs/<run_id>/workspace/` — open it, or run it directly
  (`python data/runs/<run_id>/workspace/main.py`).
- **Attribution is thin for claude/codex.** Their CLIs emit a final summary, not
  opencode-style per-tool step logs, so the *Harness attribution* panel shows
  `read×0 edit×0 …` and `governance 0/8`. The **LLM audit** still works (it reads
  the summaries). Treat tool/edit counts as unavailable for these harnesses.
- **Model ids** are the systematic-lab knob: if a CLI rejects an id (version
  drift), edit `HARNESS_NATIVE_MODELS` in `frontend/src/pages/Runs.tsx`.
- **Preflight guard**: creating a claude/codex run while in Docker mode is
  rejected with "runs the host CLI … start host mode (make app-host)".

## Quick reference

| I want to test… | Mode | Harness | Model source |
|---|---|---|---|
| Claude Code (your account) | `make app-host` | `claude` | native menu (Sonnet 5 / Opus 4.8 / Haiku 4.5) |
| Codex (your account) | `make app-host` | `codex` | native menu (edit ids in Runs.tsx) |
| opencode + gateway model | `make app-up` | `opencode` | live LiteLLM list |
| SWE-bench objective | `make app-up` | `opencode` | live LiteLLM list |
