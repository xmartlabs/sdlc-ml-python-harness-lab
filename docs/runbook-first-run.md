# Runbook: benchmark experiments

Two commands per run. One model at a time.

---

## Setup (once)

```bash
cp .env.example .env
# Edit .env: GATEWAY_BASE_URL, LITELLM_MASTER_KEY
# LITELLM_MASTER_KEY = the LiteLLM master key, needed to create a key per run automatically
```

Check that the gateway answers:

```bash
make opencode-doctor
```

---

## The experiment map (cells A–E)

Each **cell** = one fixed experimental condition (model + methodology + prompt).
Each **run** (001, 002, 003) = one repetition of that condition.

| Cell | Name | Harness | Planner | Executor | Prompt |
|-------|--------|---------|---------|----------|--------|
| **A** | pure local — Qwen 35B | opencode | Qwen 35B | Qwen 35B | incremental |
| **B** | conductor cloud + large local | opencode-conductor | GPT-5.5 | Qwen 35B | conductor |
| **C** | conductor cloud + small local | opencode-conductor | GPT-5.5 | Qwen 9B | conductor |
| **D** | conductor local + local | opencode-conductor | Qwen 35B | Qwen 9B | conductor small |
| **E** | pure cloud baseline | opencode | GPT-5.5 | GPT-5.5 | incremental |

**Key rule:** change nothing between runs of the same cell (same model, same key,
same prompt). The goal is to measure variance, not to change conditions.

See the live table (what is complete, scores, per-cell averages):

```bash
make bench-results
```

---

## Per-run flow

### 1. Pick the run_id

Follow the natural order within each cell: A-001, A-002, A-003, then B-001, and so
on. To see which are still pending:

```bash
make bench-results
```

### 2. Start

```bash
make bench-start RUN=tetris-v1-A-002
```

If automatic key creation fails (Cloudflare), pass one by hand:

```bash
make bench-start RUN=tetris-v1-A-002 KEY=sk-your-key
```

This does everything at once:
- Registers the run (`runs.yaml` → running)
- Creates `workspace/tetris-v1-A-002/` — this is where OpenCode writes
- Generates `data/runs/tetris-v1-A-002/prompt.md` with the workspace path injected
- Writes `BENCH_CONTEXT.md` (OpenCode reads it and knows where to write)
- Copies the prompt to the clipboard
- Launches OpenCode

### 3. Run it in OpenCode

```
Cmd+V → Enter    ← pastes the prompt already on the clipboard
```

Validate each increment before approving the next one:

```bash
python workspace/tetris-v1-A-002/main.py
```

OpenCode writes inside `workspace/tetris-v1-A-002/` — never at the repository root.

### 4. Close (from another terminal, or after quitting OpenCode)

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=5 INTERVENTIONS=0
```

With notes:

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=7 INTERVENTIONS=1 NOTES="Reached rotation, lock failed"
```

This:
- Archives `workspace/tetris-v1-A-002/` → `data/runs/tetris-v1-A-002/workspace/`
- Generates `diff.patch`
- Updates `runs.yaml` with score and interventions
- Commits

### 5. See the updated results

```bash
make bench-results
```

---

## Suggested execution order

Complete all three repetitions of a cell before moving to the next one, so the
variance is measurable. With two machines you can run cells in parallel:

```
A-001 → A-002 → A-003 → (check avg A) → B-001 → B-002 → B-003 → ... → make bench-results
```

---

## Adding a new model to the comparison

1. Add a new cell section in `benchmarks/tetris/runs.yaml`:

```yaml
  # --- Cell F: new model — description ------------------------------------
  - run_id: tetris-v1-F-001
    methodology: opencode-local
    harness: opencode
    planner_model: <model-alias>
    executor_model: <model-alias>
    gateway_models: [gateway/<alias>]
    executor_context: 32768
    temperature: 0.2
    prompt_variant: tetris_incremental_v1
    langfuse_session_id: bench-tetris-v1-F-001
    virtual_key: bench-opencode-<name>
    branch: bench/tetris-v1-F-001
    status: pending
    result_level:
    human_interventions:
    notes: ""
  - run_id: tetris-v1-F-002
    ... (copy and adjust for F-002, F-003)
```

2. Add the model to the gateway if it is not there yet: edit `opencode.json` under
   `provider.gateway.models` so it shows up in the OpenCode selector.

3. Run it normally:

```bash
make bench-start RUN=tetris-v1-F-001
make bench-close RUN=tetris-v1-F-001 SCORE=X INTERVENTIONS=N
make bench-results   # it is already in the table
```

---

## LiteLLM keys (one per run)

Create one by hand in the gateway UI (`GATEWAY_BASE_URL/ui → Keys → New Key`):
- **Key alias**: the exact run_id (e.g. `tetris-v1-A-002`)
- **Metadata**: `session_id = bench-tetris-v1-A-002` (to group traces in Langfuse)

Pass it to the command:

```bash
make bench-start RUN=tetris-v1-A-002 KEY=sk-your-litellm-key
```

One key per run is the right call: LiteLLM reports usage per key, and Langfuse
groups traces by the `session_id` baked into the key.

---

## Scoring cheatsheet

| Score | What it achieved |
|-------|-----------|
| 0 | Produced no usable code |
| 1 | Generates files but does not run |
| 2 | Runs without crashing |
| 3 | Opens a pygame window |
| 4 | Shows a static piece |
| 5 | Piece falls |
| 6 | Lateral movement |
| 7 | Rotation |
| 8 | Collisions and lock |
| 9 | Clears lines + score |
| 10 | Fully playable (game over, restart) |

---

## Where to look at results

| Source | What it shows |
|--------|-------------|
| `make bench-results` | live comparison table: every run, scores, per-cell averages |
| `data/runs/<run-id>/report.md` | detail for a single run |
| `data/runs/<run-id>/diff.patch` | what the model wrote |
| LiteLLM UI → Keys | tokens and cost per run (search by key alias = run_id) |
| Langfuse → Sessions | traces grouped by session_id (if the key has session_id baked in) |
| `reports/tetris-v1-results.md` | final narrative report (fill in once every cell is done) |
