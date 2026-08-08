# ADR-0016 — opencode config overlay for model routing

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** supersedes the "manual sync" half of ADR-0012; ADR-0013 (overlay
  model), ADR-0003 (trial provenance)

## Context

ADR-0012 split discovery from routing: LiteLLM answers "what models exist"
(`GET /v1/models`), while `provider.gateway.models` in the tracked `opencode.json`
answers "what can opencode route". It kept the reconciliation manual —
`make sync-gateway-models` — deliberately, so the user reviews the diff.

That trade-off assumed a developer at a terminal in the repo. On an always-on
server it produces the lab's most common failure: a model registered in LiteLLM
shows up in the run form, the run starts, and every step burns on a provider
resolution error. The symptom appears minutes after launch and looks like a model
problem rather than a configuration one.

Editing `opencode.json` from the web would fix routing and break the clean-tree
invariant (ADR-0013). What makes a third option possible is opencode's documented
config precedence, later winning:

    Remote → Global → OPENCODE_CONFIG (file) → project opencode.json
           → .opencode/ → OPENCODE_CONFIG_CONTENT (inline JSON) → Managed

`OPENCODE_CONFIG_CONTENT` ranks *after* the project file. Neither variable was used
anywhere in this repository.

## Decision

We will route web-configured models through an **environment overlay applied at run
launch**, leaving `opencode.json` untouched.

1. **`GatewayModel` table** — the web model catalog: `model_id` (unique), `label`,
   `enabled`, `is_default`, `live`, `options_json`, `first_seen_at`/`last_seen_at`.
   `refresh()` adopts whatever the gateway serves; ids that vanish are marked
   **stale, never deleted**, so a temporarily-down provider does not lose its
   hand-written label. An empty live list means "unreachable", not "everything
   vanished", and marks nothing stale.
2. **`src/harness_lab_app/opencode_overlay.py`** (stdlib only, importable from
   `scripts/bench_run.py`) computes the **delta** between the enabled catalog and
   the tracked map, materializes `data/app/opencode-effective.json`, and returns
   both `OPENCODE_CONFIG` (the file path) and `OPENCODE_CONFIG_CONTENT` (the same
   JSON inline). Setting both is deliberate: the inline form wins over the project
   file, the file form closes the gap for genuinely new ids, and neither requires
   knowing the opencode version — `Dockerfile.runner` pins it to `latest`.
   The overlay reproduces the **whole** `provider.gateway` block (npm, name,
   options, merged models), so it is correct whether a given opencode version merges
   deeply or replaces at the provider level.
3. **Injection happens in `scripts/bench_run.py`**, not in
   `executors.build_run_environment()`. `SubprocessRunExecutor.start` passes no
   `env=`, so host-mode runs would silently miss an overlay applied there. An
   `lru_cache`d `apply_gateway_overlay()` is called from `main()` and from the
   `enrich_metadata` call site, so ordering cannot break it.
4. **Provenance:** `gateway_overlay_hash` enters `condition_hash` **only when
   non-empty**, following the existing rule in `provenance.py`. `build_overlay()`
   returns `({}, None)` when the catalog expresses nothing the tracked map does not,
   so every historical arm keeps its identity. A regression test locks this.
5. **Preflight rejects an unroutable executor** before the run is persisted: a
   `gateway/` model in neither the tracked map nor the enabled overlay fails at
   creation with an actionable message. It fails open when the routing map is
   unreadable, and is skipped under `HARNESS_LAB_DISABLE_GATEWAY_PROBE` alongside
   the reachability probe.
6. **`GET /api/models` gains a `routable` flag per row** and a 30-second TTL cache
   (`list_gateway_models_cached`) so the Settings page and run form do not hit
   LiteLLM on every render. An empty result is never cached.

`make sync-gateway-models` remains, and remains manual — it is now the *promotion*
path (curate the tracked map for the CLI lane and for git history), not the only way
to make a model usable.

## Consequences

- The user's core workflow becomes: add the model in LiteLLM → press **Refresh from
  gateway** in Settings → launch a run. No CLI, no commit, `git status` clean.
- A model enabled in the web catalog is routable only for runs launched through this
  lab. A bare `opencode` invocation in a terminal still sees only `opencode.json` —
  which is why promotion still exists.
- `options_json` is reserved for per-model options and id remaps. Today the overlay
  carries only labels, which are cosmetic; gating the hash on non-empty means the
  identity is already correct when a behavioural option lands.
- The overlay rests on opencode's documented precedence. **It must be validated
  empirically against the pinned runner image**: `docker compose run --rm runner
  opencode --version`, then a run using a synthetic id present only in the overlay.

## Alternatives considered

- **Write `opencode.json` from the web.** Rejected: breaks the clean-tree invariant
  (ADR-0013) and would make `harness_git_dirty` permanently true.
- **`OPENCODE_CONFIG` alone.** It ranks *before* the project file, so it cannot
  override an entry `opencode.json` already defines — only add new ones. Kept, but
  paired with the inline form.
- **A `.opencode/` directory in the repo.** Rejected: it is a tracked path, so the
  same clean-tree problem, plus it would leak into the CLI lane.
- **Run `make sync-gateway-models` from the API.** Rejected: it rewrites a tracked
  file, and the API cannot commit the result — the tree would be left dirty in a way
  no one reviews.
