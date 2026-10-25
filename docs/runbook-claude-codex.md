# Runbook — testing with the claude and codex harnesses

How to run benchmark cases against the **claude** (Claude Code) and **codex**
(OpenAI Codex) harnesses. Both drive their own CLI with native auth instead of a
gateway model, but they run in different places:

- **claude** runs in the normal Docker stack (`make app-up`) — the CLI is baked
  into the runner image and reads a credential from `.env`. Host mode also works.
- **codex** runs in **host mode** (`make app-host`, subprocess backend) with your
  own OpenAI login; codex-in-Docker is not wired yet.

Design details live in [harness-adapters.md](harness-adapters.md); this is the
step-by-step.

## 0. Prerequisites (once)

- **claude in Docker:** a credential in `.env` — `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token` (uses your subscription) or `ANTHROPIC_API_KEY`. Walkthrough:
  [onboarding.md, Path B](onboarding.md#path-b--claude-code-with-your-own-account).
  Rebuild the runner image once so it has the CLI (`make app-up` does).
- **codex, or claude in host mode:** the CLI installed and **logged in** on your
  machine, on the PATH of the shell you start `make app-host` from — the worker
  inherits it to find the CLI. Verify with `command -v claude codex`.

## 1. Start the lab

```bash
make app-up      # claude (Docker), opencode, SWE-bench
make app-host    # codex — or claude with your host login
```

`make app-host` brings up redis + the Celery worker + the API + the frontend on the
host with `HARNESS_LAB_EXECUTION_BACKEND=subprocess`; Ctrl-C stops everything. You
run one mode at a time — pick by the harness you're testing. Open
<http://localhost:5173>.

## 2. Create a run

**Evaluate → New evaluation** for a control/treatment comparison, or **Advanced →
Runs → New run** for a single run:

1. **Case** — e.g. `clinic-slots-t1` (pure Python, graded by the lab's own tests —
   a good clean probe) or `todo-web-v1` (a page graded in a real browser).
2. **Harness** — `claude` or `codex`.
3. **Executor model** — the dropdown shows that CLI's **native models**:
   - claude → Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.1, then the
     previous generation; a model your gateway does not serve is marked
   - codex → `codex-mini-latest` (add your ids in `HARNESS_NATIVE_MODELS`,
     `frontend/src/harnesses.ts`)

   The selected id is passed straight to the CLI (`claude --model …` /
   `codex -m …`). Planner model is unused for these harnesses.
4. **Prompt variant** (e.g. `sdlc`), **Max steps**, then **Create run**.

The run streams into its detail page. Under the hood:

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --model claude-sonnet-5 "<prompt>"
codex  --quiet --full-auto -m codex-mini-latest "<prompt>"
```

with `cwd` at the repo root; the prompt tells the agent to build in
`workspace/<run_id>/`.

## 3. Score + validate

- Every web run leaves with a score ([ADR-0038](adr/0038-every-run-leaves-with-a-score.md)):
  a case with validation commands scores itself, a case with a `scoring_rubric`
  (`tetris-v1` has one) is LLM-judged, and only a rubric-less, non-objective case
  stops in `awaiting_manual_score`. Any score can be overwritten by hand on the
  run detail (0–10 + interventions).
- **Objective validation** (the smoke / test floor) runs automatically and shows
  under *Validation & score*; use **Re-validate** to re-run it. On macOS the GNU
  `timeout` wrapper is handled automatically (`gtimeout` or dropped).
- **LLM audit** (optional) — click **Run audit** for a qualitative diagnostic
  (skill/SDLC/efficiency/failure) written by a strong model against the logs. It
  uses the gateway, or the Claude Code CLI when no gateway is configured
  ([ADR-0043](adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)).

## 4. Compare

**Compare** page → pick the runs (same case, different harness/model) to see
objective result, score, tokens, steps side by side; **Benchmarks** has the
leaderboard. For a comparison you can trust, build it as an experiment instead —
**Evaluate → Harness x model** or **New evaluation** with the **Swap the harness**
factor ([run and compare](journey-run-and-compare.md)).

## Gotchas / limits

- **Sandboxes (Terminal/Desktop) need Docker** — they don't work in host mode.
  You don't need them: the produced code is on disk at
  `data/runs/<run_id>/workspace/` — open it, or run it directly
  (`python data/runs/<run_id>/workspace/main.py`).
- **Attribution is thin for codex.** Its CLI emits a final summary, not per-tool
  step logs, so the *Harness attribution* panel shows `read×0 edit×0 …`. The
  **LLM audit** still works (it reads the summaries). `claude` runs use
  `--output-format stream-json`, so their attribution is first-class: files
  read/edited, bash histogram, governance coverage and real `Skill` invocations
  (see [harness-adapters.md](harness-adapters.md#claude-observability--ab-hygiene-stream-json)).
- **Model ids** are the systematic-lab knob: if a CLI rejects an id (version
  drift), edit `HARNESS_NATIVE_MODELS` in `frontend/src/harnesses.ts`.
- **Preflight guard**: a `codex` run in Docker mode is rejected with "runs the
  host CLI … start host mode (make app-host)"; a `claude` run in Docker mode
  without a credential in `.env` is rejected with the variable names to set.

## Quick reference

| I want to test… | Mode | Harness | Model source |
|---|---|---|---|
| Claude Code (your account) | `make app-up` (token in `.env`) or `make app-host` | `claude` | native menu (the Claude 5.5 family and Fable 5.1) |
| Codex (your account) | `make app-host` | `codex` | native menu (edit ids in `harnesses.ts`) |
| opencode + gateway model | `make app-up` | `opencode` | live LiteLLM list |
| SWE-bench objective | `make app-up` | `opencode` | live LiteLLM list |
