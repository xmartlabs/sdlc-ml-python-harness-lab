# Langfuse metadata standard (shared by all harnesses)

Every call routed through LiteLLM must carry consistent metadata so a full run is filterable
by `session_id` and variants are comparable by `tags`. LiteLLM exports metadata/tags to
Langfuse under the `langfuse.*` prefix, so these become searchable in the Langfuse UI.

## Canonical payload

```json
{
  "metadata": {
    "generation_name": "tetris-coding-turn",
    "trace_name": "tetris-opencode-local-qwen35b",
    "session_id": "bench-tetris-v1-A-001",
    "tags": [
      "benchmark:tetris-v1",
      "harness:opencode",
      "method:local-only",
      "planner:qwen3.6-35b",
      "executor:qwen3.6-35b",
      "prompt:tetris_incremental_v1"
    ],
    "trace_metadata": {
      "benchmark_id": "tetris-v1",
      "run_id": "tetris-v1-A-001",
      "repo": "ml-python-base",
      "harness": "opencode",
      "methodology": "local-only",
      "planner_model": "qwen3.6-35b",
      "executor_model": "qwen3.6-35b",
      "executor_context": 32768,
      "prompt_variant": "tetris_incremental_v1",
      "temperature": 0.2,
      "agent_role": "executor",
      "agent_name": "qwen-build-agent"
    }
  }
}
```

## Field rules

- `session_id` — **one per run**, exactly the `langfuse_session_id` from `runs.yaml`. This is
  how you collapse every call of a run into a single comparable session.
- `tags` — flat, `key:value` strings. Always include `benchmark`, `harness`, `method`,
  `planner`, `executor`, `prompt`.
- `trace_metadata.run_id` — matches `runs.yaml`.
- `agent_role` / `agent_name` — **the key for conductor runs**. Set them per agent so Langfuse
  can separate `planner` / `executor` / `critic` / `tester`. Without this you only see tokens
  per model, not per role.

## Three layers of attribution (use all three, in priority order)

1. **`session_id` + `tags` per run** — the base; always present.
2. **Role aliases in the gateway** — even when they point at the same backend
   (`qwen35b-planner`, `qwen35b-executor`). Lets you group usage by role when the harness
   cannot inject fine metadata. See `../GATEWAY_CHANGES.md`.
3. **Virtual keys per methodology** — see consumption per methodology even if metadata is
   imperfect. The `virtual_key` field in `runs.yaml` names the key per cell.
