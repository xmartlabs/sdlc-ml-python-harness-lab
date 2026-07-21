# ADR-0012 — Dynamic gateway model list and opencode.json sync

- Status: accepted
- Date: 2026-07-20
- Relates to: ADR-0002 (Celery/Docker execution, `.env` host URLs), the audit
  gateway client (`scripts/bench_audit.py`)

## Context

The "New run" form let the user pick a Planner/Executor model from two
hand-maintained `as const` arrays in `frontend/src/pages/Runs.tsx`. Those lists
had already drifted from the real source of truth — the `provider.gateway.models`
map in `opencode.json` — listing models the gateway does not serve
(`openai/gpt-4o-mini`, `openai/moonshotai/kimi-k2.6`) and omitting ones it does.
Every new model the user registers in their LiteLLM gateway required editing the
frontend by hand, so the form lagged the gateway and was easy to get wrong.

A second, subtler constraint: opencode only routes models that appear in
`opencode.json`'s gateway `models` map (prefix `gateway/<id>`). So "read the list
from LiteLLM" is not enough on its own — the routing map must also be kept in
step, or a live-listed model would show in the dropdown but fail to run.

## Decision

1. **LiteLLM is the discovery source; opencode.json is the routing source.**
   A shared, stdlib-only module `src/harness_lab_app/gateway_models.py` holds the
   gateway helpers (lifted from `bench_audit.py`, which now imports them):
   `list_gateway_models()` (live `GET {GATEWAY_BASE_URL}/models`),
   `normalize_model_name()` / `gateway_model_id()` (the `gateway/` prefix rules),
   `read_opencode_gateway_models()` (offline fallback + label source) and the
   pure `build_model_catalog()` composer.
2. **New endpoint `GET /api/models`.** Reads gateway config with
   `read_env_file_value()`, rewrites `localhost` → docker host alias only when
   running in a container (`/.dockerenv`), queries LiteLLM, and returns a
   `{source, models:[{id,label}], default_executor}` payload. Ids are
   `gateway/`-prefixed (what the harness expects); labels come from
   `opencode.json` when present, else the bare id. When the gateway is
   unreachable it falls back to the `opencode.json` map (`source:"opencode.json"`)
   so the form never breaks. `Runs.tsx` fetches it on mount and drops the
   hardcoded arrays.
3. **`make sync-gateway-models` reconciles opencode.json.** A script rewrites
   *only* the `provider.gateway.models` block from the live list: existing entries
   keep their label and order, new models are appended (label = id), removed
   models are dropped, and everything else in the file is left byte-for-byte
   untouched (string-aware brace splice, validated as JSON before write, with
   `--dry-run`). This makes "update the gateway, then run one command" the whole
   workflow for adding a model — no hand-editing of `opencode.json`.

## Consequences

- The run form always reflects the gateway; adding a model (e.g. a new OpenAI or
  open-source release) to LiteLLM makes it selectable after `make
  sync-gateway-models`, with no frontend change.
- The sync is authoritative: a model removed from LiteLLM is dropped from
  `opencode.json` on next sync (e.g. `qwen9b-executor`). Re-add it to the gateway
  to keep it. The command is manual (not a hook) so the user reviews the diff.
- `default_executor` stays `gateway/lmstudio-qwen3.6-35b-build` when the gateway
  serves it, else the first served id; the frontend only adopts the gateway
  default while the form is untouched, never clobbering a user pick.
- Direct (non-gateway) opencode providers (`ollama`, `lmstudio`, `nvidia`) are
  out of scope for the web dropdown, which is intentionally LiteLLM-centric; they
  remain available to the CLI via `opencode.json`.
