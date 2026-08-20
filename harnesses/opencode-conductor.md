# Harness: opencode-conductor

Plan/conductor model orchestrating a separate executor model through the gateway.
Used by **Cells B, C, D, F, G, H, I** (`tetris_conductor_v1` / `tetris_conductor_small_steps_v1`
and Nemotron planner variants).

## Per-run procedure

Same clones-per-run flow as `opencode-local.md` (freeze → register → worktree → run → capture →
score). The differences are the model wiring and the role attribution.

### Model wiring (`.env` in the subject worktree)

```bash
# Cell B: cloud conductor + local executor
OPENCODE_MODEL_PLAN=gateway/gpt-5.5                       # conductor / plan agent
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build         # executor / build agent

# Cell C: cloud conductor + Qwen 3.5 9B executor
OPENCODE_MODEL_PLAN=gateway/gpt-5.5
OPENCODE_MODEL=gateway/qwen9b-executor        # lmstudio-qwen3.5-9b at temp 0.2 (role alias)

# Cell D: all-local conductor + Qwen 3.5 9B executor
OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build
OPENCODE_MODEL=gateway/qwen9b-executor

# Cell F: Nemotron planner + Qwen 35B executor
OPENCODE_MODEL_PLAN=gateway/nemotron-120b-planner
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build

# Cell G: Nemotron planner + Qwen 3.5 9B executor (Qwen 8B class)
OPENCODE_MODEL_PLAN=gateway/nemotron-120b-planner
OPENCODE_MODEL=gateway/qwen9b-executor

# Cell H: Qwen 35B planner + Qwen 35B executor (homogeneous local baseline)
OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build

# Cell I: all Nemotron 120B
OPENCODE_MODEL_PLAN=gateway/nemotron-120b-planner
OPENCODE_MODEL=gateway/nemotron-120b-executor

GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=<virtual key for the cell, see runs.yaml>
```

OpenCode's `conductor` and `plan` agents use `OPENCODE_MODEL_PLAN`; `build` uses `OPENCODE_MODEL`
(see the subject's `opencode.json`). Temperature 0.2 on all agents.

### Role attribution (the hard part)

To know whether the conductor actually used subagents, each role must be distinguishable in
Langfuse. Use **either**:

- **Per-agent metadata** — set `agent_role` / `agent_name` per call
  (`conductor-planner`, `qwen-build-agent`) per `metadata-standard.md`. Preferred.
- **Role aliases in the gateway** — point the plan agent at `gateway/qwen35b-planner` and the
  build agent at `gateway/qwen35b-executor` (same backend, different `model_name`) so usage
  groups by role even without fine metadata. See `../GATEWAY_CHANGES.md`.

If neither is available you will see tokens per model but not per role — that is the failure
mode this harness exists to avoid.

### Prompt

- Cells B, C: `tetris_conductor_v1`.
- Cell D: `tetris_conductor_small_steps_v1` (conductor assumes a weak executor — finer steps).
- Cell F: `nemotron_planner_v1`.
- Cell G: `nemotron_planner_small_steps_v1`.
- Cell H: `tetris_conductor_v1` (homogeneous local baseline).
- Cell I: `nemotron_planner_v1` (all-Nemotron).

## Using NVIDIA NIM as planner (Nemotron variant)

For Nemotron planner runs (F, G, I), map a planner alias in LiteLLM first and keep the same
`OPENCODE_MODEL_PLAN` value for every repetition.

```bash
# example aliases expected by runs.yaml
gateway/nemotron-120b-planner
gateway/nemotron-120b-executor
```

Recommended metadata tags for quick filtering:

- `planner:nemotron-super-120b`
- `executor:qwen3.6-35b` or `executor:qwen3.5-9b` or `executor:nemotron-super-120b`
- `method:conductor-nemotron-local|conductor-nemotron-small|conductor-nemotron-homogeneous`

Operational notes:

- Estimate token and USD budget before launching each run and store it in run metadata.
- Keep temperature at 0.2 for plan/executor stability.
- If NIM rate limits hit, pause and resume the same run/session instead of opening a new one.
