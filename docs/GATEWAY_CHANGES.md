# Gateway changes (to apply in the `ai-gateway` repo)

The gateway **observes, never directs** — do **not** put benchmark logic inside it. These are
the minimal changes that make benchmark runs attributable, in priority order. Apply them in the
`ai-gateway` repo (LiteLLM), not here.

## 1. `session_id` + tags per run (base — already supported)

Runs carry `session_id` and `tags` via request metadata (see `harnesses/metadata-standard.md`).
LiteLLM forwards these to Langfuse under `langfuse.*`. Nothing to add beyond confirming the
Langfuse OTEL exporter stays enabled. This is the primary attribution layer.

## 2. Role aliases (group usage by role even on the same backend)

Add `model_name` aliases per role so planner vs executor usage is separable when the harness
cannot inject fine `agent_role` metadata. They can resolve to the **same** physical backend.

```yaml
# config.yaml (conceptual)
model_list:
  - model_name: qwen35b-planner        # same backend, slightly higher temp
    litellm_params:
      model: openai/qwen/qwen3.6-35b-a3b
      api_base: os.environ/LMSTUDIO_API_BASE
      api_key: "lm-studio"
      temperature: 0.3
  - model_name: qwen35b-executor       # same backend, low temp
    litellm_params:
      model: openai/qwen/qwen3.6-35b-a3b
      api_base: os.environ/LMSTUDIO_API_BASE
      api_key: "lm-studio"
      temperature: 0.2
  - model_name: qwen9b-executor        # Qwen 3.5 9B in executor role, temp 0.2
    litellm_params:
      model: openai/qwen/qwen3.5-9b
      api_base: os.environ/LMSTUDIO_API_BASE
      api_key: "lm-studio"
      max_tokens: 4096
      temperature: 0.2
      top_p: 0.8
```

> Note: the gateway already has `lmstudio-qwen3.5-9b` (default `temperature: 0.7`). The
> `qwen9b-executor` alias is a separate entry at `0.2` so executor usage groups by role name
> in Langfuse and keeps low-temperature execution isolated from the default alias.

Then in Langfuse/LiteLLM you can group by `model_name` even though it is physically one model.

## 3. Virtual keys per methodology

Create one virtual key per methodology so consumption is attributable even if metadata is
imperfect. These match the `virtual_key` field in `benchmarks/tetris/runs.yaml`:

```
bench-opencode-local
bench-conductor-gpt55-qwen35b
bench-conductor-gpt55-qwen9b
bench-conductor-qwen35b-qwen9b
bench-cloud-baseline
```

## 4. Document the metadata standard

Point the gateway README at `harnesses/metadata-standard.md` in this lab so the contract has
one source of truth.

## 5. Per-run summary command (confirm or add)

Confirm there is a way to pull a usage summary by `run_id` / `session_id` (LiteLLM exports
metadata/tags to Langfuse with the `langfuse.*` prefix). The lab-side half of this exists:
`scripts/export_langfuse_traces.py` posts one Langfuse session per run, carrying the run's own
token and cost totals ([guide](guide-langfuse-observability.md)). What is still missing is the
gateway forwarding *per-call* generations under the same `session_id`.
