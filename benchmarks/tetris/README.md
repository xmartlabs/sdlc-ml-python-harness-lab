# tetris-v1

The model builds a Tetris game in Python from scratch. A single command runs it
end-to-end, and it asks you for the score at the end.

```bash
make bench-results       # see which runs are still pending
```

---

## Flow from the web UI (CLI parity, without blocking)

- The UI creates runs with `score_mode=manual`; it does not support `score_mode=interactive`.
- A run created through the API with `score_mode=interactive` is rejected by the backend.
- When the worker finishes, the run lands in `awaiting_manual_score`; the score is submitted from the detail panel (`POST /api/runs/{run_id}/score`).
- If the `worker` service is not running, runs stay in `pending` and no execution logs appear.
- The UI now loads valid `run_id` values from `benchmarks/tetris/runs.yaml`, which avoids errors from invented IDs (for example, using `tetris-v1-K-001`).
- The Docker runtime does not depend on the host `.venv` to execute runs from the UI.
- To play-test Tetris before scoring, run it from the host against the shared workspace:

```bash
python workspace/<run_id>/main.py
```

The worker container has no display, which is why pygame cannot be validated inside
Docker.

To bring up UI + API + worker together:

```bash
make app-up
# direct equivalent:
# docker compose --profile worker up --build api worker frontend
```

---

## End-to-end runs per model

Each command prepares the workspace, executes up to 10 chained opencode sessions,
and asks for an interactive score (0–10) when it finishes. **You do not need to open
OpenCode or paste prompts by hand.**

---

### Qwen3.6 35B local — Cell K ✅ works today (SDLC mode)

**Prerequisite:** LM Studio running with the `qwen3.6-35b` model loaded.

```bash
# K-001
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-K-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# K-002 / K-003: change only RUN=tetris-v1-K-002  (or K-003)
```

---

### Nemotron Super 120B — Cell J ✅ via the AI gateway (SDLC mode)

```bash
# J-002  (J-001 was closed with a previous config error)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-J-002 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=gateway/nim-nemotron-super-120b \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# J-003: change RUN=tetris-v1-J-003
```

---

### GPT-5.5 cloud — Cell E ⚠️ requires GATEWAY_TOKEN

**Prerequisite:** `GATEWAY_TOKEN` in `.env`, with access to the configured LiteLLM gateway.

```bash
# E-001  (incremental mode, baseline)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-E-001 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/gpt-5.5 \
  MAX_STEPS=6 \
  SCORE_MODE=interactive
```

---

### Nemotron planner + Qwen executor — Cell F ✅ via the AI gateway

**Prerequisite:** LM Studio running with qwen3.6-35b.

```bash
# F-001
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-F-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nim-nemotron-super-120b \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=6 \
  SCORE_MODE=interactive
```

---

### GPT-4o-mini cloud — Cell L ⚠️ requires GATEWAY_TOKEN

**Prerequisite:** `GATEWAY_TOKEN` in `.env`, with access to the LiteLLM gateway for OpenAI.

```bash
# L-001  (sdlc mode)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-L-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=openai/gpt-4o-mini \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# L-002 / L-003: change only RUN=tetris-v1-L-002  (or L-003)
```

---

### Kimi-K2.6 cloud — Cell M ⚠️ requires GATEWAY_TOKEN

**Prerequisite:** `GATEWAY_TOKEN` in `.env`, with access to the LiteLLM gateway for Moonshot AI.

```bash
# M-001  (sdlc mode)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-M-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=openai/moonshotai/kimi-k2.6 \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# M-002 / M-003: change only RUN=tetris-v1-M-002  (or M-003)
```

---

## Reset and start over

```bash
# Reset one run to pending (keeps previous artifacts)
make bench-reset RUN=tetris-v1-K-001

# Reset + delete artifacts (workspace, logs, report)
make bench-reset RUN=tetris-v1-K-001 CLEAN=1

# Reset every run of a case
make bench-reset CASE=tetris-v1

# Nuclear: reset everything + delete every artifact
make bench-reset CASE=tetris-v1 CLEAN=1

# Skip the interactive confirmation
make bench-reset RUN=tetris-v1-K-001 FORCE=1
```

`bench-reset` sets `status: pending` in `runs.yaml`, removes the row from
`results.csv`, and with `CLEAN=1` deletes `data/runs/<id>/` and `workspace/<id>/`.

---

## Check the configuration before running (dry run)

```bash
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-K-001 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=10 \
  DRY_RUN=1
```

With `DRY_RUN=1` the command prints everything it would do without writing or
executing anything.

---

## How to score at the end

When the execution finishes, the runner shows the rubric and asks:

```
Functional score (0-10): _
Human interventions: _
```

| Score | State of the game |
|-------|-----------------|
| 0 | No usable code |
| 1 | Files generated, does not run |
| 2 | Runs without crashing |
| 3 | Opens a window |
| 4 | Shows a piece |
| 5 | Piece falls |
| 6 | Lateral movement |
| 7 | Rotation |
| 8 | Collisions + lock |
| 9 | Clears lines + score |
| 10 | Fully playable |

To test it: `python workspace/<run_id>/main.py`

---

## See results

```bash
make bench-results           # every run (pending + completed)
cat benchmarks/tetris/results.csv  # scored only
```

---

## Audit a run's behavior

An auditor LLM reads the artifacts the run already left in `data/runs/<id>/`
(`opencode.log` / `steps/step-*.log`, `diff.patch`, `usage.json`, `metadata.json`,
the score, and the corpus of rules the agent saw in `OPENCODE.md`) and writes a
**descriptive behavior diagnosis** to `data/runs/<id>/audit.md`.

It covers four dimensions: use of skills and rules, efficiency and loops, SDLC
adherence, and failure points. It is descriptive — it does not propose harness edits.

**Prerequisite:** the run must have artifacts in `data/runs/<id>/` (execute it first
with `make bench-run ...`). It needs `GATEWAY_BASE_URL` and `GATEWAY_TOKEN` in `.env`.

```bash
# Audit a run with the default auditor (gateway/gpt-5.5)
make bench-audit RUN=tetris-v1-K-001

# Pick the auditor model
make bench-audit RUN=tetris-v1-K-001 AUDITOR_MODEL=gateway/claude-sonnet-4.6

# Dry run: see which artifacts it detects and the request it builds, without calling the gateway
make bench-audit RUN=tetris-v1-K-001 DRY_RUN=1
```

The auditor model can also be fixed with `AUDITOR_MODEL` in `.env`.

---

## Running with Antigravity (human-driven) — Cell N

A separate cycle for running the task with **Google Antigravity** (IDE or panel) and
improving the rules for the Google case (`.agents/rules/GEMINI.md` + skills).
Antigravity is human-driven: there is no headless CLI, a person drives the execution.
**It does not touch the opencode flow.** Full detail in
[`harnesses/antigravity.md`](../../harnesses/antigravity.md).

Cell N defaults to the **`sdlc_full`** prompt: a single prompt instructing the model
to build all **8 milestones in one autonomous session** (Antigravity is agentic and
does it in one pass). That is the "complete case". For Milestone 1 only — like a
single step of the opencode flow — pass `PROMPT=sdlc`.

```bash
# 1. Prepare: register the run, workspace, full prompt (8 milestones), templates
make bench-antigravity RUN=tetris-v1-N-001
#    Milestone 1 only:  make bench-antigravity RUN=tetris-v1-N-001 PROMPT=sdlc

# 2. (by hand) Open Antigravity, paste the prompt, work inside workspace/<id>/,
#    paste the transcript into data/runs/<id>/transcript.md and fill in notes.md

# 3. Close (generic, same as opencode)
make bench-close RUN=tetris-v1-N-001 SCORE=<0-10> INTERVENTIONS=<N>

# 4. Audit the .agents rules (prescriptive)
make bench-agents-audit RUN=tetris-v1-N-001
```

### Auditing the .agents rules (prescriptive)

Unlike `make bench-audit` (descriptive, over opencode), `make bench-agents-audit` is
**prescriptive**: an LLM reads the run's evidence (`diff.patch`, `transcript.md`,
`notes.md`, score) against the current rules (`.agents/rules/GEMINI.md` + skills) and
proposes concrete edits to `GEMINI.md` and to the skills, citing evidence. It writes
`data/runs/<id>/agents_audit.md`. It degrades gracefully: it uses whatever is present.

```bash
make bench-agents-audit RUN=tetris-v1-N-001 DRY_RUN=1   # see what it detects, without spending tokens
make bench-agents-audit RUN=tetris-v1-N-001 AUDITOR_MODEL=claude-sonnet-4.6
```

---

## Reference files

| File | Role |
|---------|-----|
| `task.yaml` | Frozen definition: description, base commit, prompts |
| `runs.yaml` | Registry of every run (source of truth) |
| `scoring.md` | Detailed rubric |
| `prompt_incremental.md` | 8 step-by-step increments (the most effective one) |
| `prompt_conductor.md` | Conductor/planner for two-model architectures |
| `prompt_naive.md` | Baseline: everything in a single response |
| `results.csv` | Comparison of scored runs |
