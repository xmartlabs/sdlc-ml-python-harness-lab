# Harness: opencode-conductor

Plan/conductor model orchestrating a separate executor model through the gateway.
Used by **Cells B, C, D** (`tetris_conductor_v1` / `tetris_conductor_small_steps_v1`).

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
