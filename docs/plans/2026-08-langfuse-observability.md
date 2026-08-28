# Langfuse Observability for Runs

**Status:** proposed — no implementation started.

**Goal:** make a single run inspectable end to end. One Langfuse **session per
run**, one **trace per iteration** (step), with the exact prompt sent, the
agent's output, the objective evaluation, the tokens/cost, and the final scores
attached — so "I want to look at test case X in detail" is one click instead of
`cat data/runs/<id>/steps/step-3.log`.

**Non-goal:** replacing the lab's own UI, DB, or `usage.json`. Langfuse is a
*second* view over data the lab already owns, never the source of truth.

**Hard requirement:** the whole integration is switchable off. `LANGFUSE_ENABLED=false`
(the default) must make every code path a no-op with zero HTTP calls, and an
enabled-but-broken Langfuse must never fail a run.

---

## Framing — what exists today, and what is missing

The repo is already *shaped* for this and never finished it:

| Piece | State |
|---|---|
| `langfuse_session_id` per run (`bench-<run_id>`) | exists — `bench_run.resolve_or_synthesize_run`, `write_metadata`, `write_run_env` |
| `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` in `.env` + settings allowlist | exists — `settings_service.py` `GROUP_TRACING` |
| Langfuse host rewritten for the runner container | exists — `executors._HOST_URL_ENV_VARS` |
| Per-run LiteLLM virtual key with `session_id` baked in | exists **only in the CLI lane** — `scripts/create_run.py:create_litellm_key` |
| Reading usage back from a session | partial — `bench_run.pull_usage_langfuse` (aggregate only) |
| `scripts/export_langfuse_usage.py` | **stub**, TODO in the docstring |
| Anything that *writes* a trace from the lab | **does not exist** |

So today the only thing that can reach Langfuse is whatever LiteLLM forwards
on its own — and in the web lane that arrives with **no session**, because
`tasks.py` passes the shared `.env` `GATEWAY_TOKEN` rather than a per-run key.
Direct-lane runs (LM Studio / Ollama with no gateway in the path) produce
nothing at all.

---

## Decisions taken

1. **Two layers, joined by `session_id`.**
   - *Layer A — lab skeleton (we write it):* the run's structure, prompts,
     step outcomes, validation, scores. Works in every lane, gateway or not.
   - *Layer B — gateway generations (LiteLLM writes them):* real
     tokens/cost/latency per model call. Only exists on the gateway lane.
   Both carry `session_id = bench-<run_id>`, so the Langfuse Session view
   collapses them into one story. Layer A is the deliverable; Layer B is a
   one-function fix in the web lane.

2. **Trace granularity = one trace per step, always.** A single-prompt,
   single-iteration case yields exactly one trace in its session; a 10-step run
   yields 10 traces in one session. No special-casing — the shape is uniform,
   the count differs. Run-level work (provisioning, validation, audit) goes in
   an eleventh `run:<id>` trace so it never distorts step timings.

3. **Post-run export first, live emission later.** The full trace tree can be
   reconstructed from `data/runs/<run_id>/` (metadata.json, steps/*.md,
   steps/*.log, usage.json, validation) with **real timestamps** parsed from
   the `# <harness> run started <iso>Z` log headers. That buys:
   - zero risk to the run hot path (an exporter cannot hang a container),
   - **retroactive backfill of every existing run on disk** — dozens of Tetris
     and SWE-bench runs become inspectable on day one,
   - idempotent re-export while the shape is still being tuned.
   Live per-step emission (Phase 4) is a strict addition afterwards.

4. **OTLP/JSON over stdlib HTTP, no new runtime dependency.** *(revised during
   implementation — the original decision named `/api/public/ingestion`, which
   Langfuse v4 no longer accepts for traces.)*

   Two findings forced the transport choice:
   - On Langfuse **v4**, `POST /api/public/ingestion` returns 400 for every
     event type except `score-create` and `sdk-log`; `/api/public/traces`,
     `/spans`, `/generations` and `/events` return 404. The supported ingestion
     path is **OTLP over HTTP** at `POST {host}/api/public/otel/v1/traces`,
     which accepts **protobuf or JSON** (gRPC is not supported), with
     `Authorization: Basic b64(public_key:secret_key)` and the header
     `x-langfuse-ingestion-version: 4`.
   - The official `langfuse` SDK (4.15.0) exposes `start_observation()` with
     **no `start_time`/`end_time`** — timestamps come from the live OTEL clock.
     A post-run exporter (decision 3) must backdate spans to the real run
     times, so the SDK cannot express what we need. Hand-built OTLP spans carry
     explicit `startTimeUnixNano` / `endTimeUnixNano`.

   So: build OTLP/JSON payloads by hand and POST them with `urllib`, matching
   every other outbound call in this repo (`case_summary.py`,
   `gateway_models.py`, `tasks.py`, `create_run.py`, `pull_usage_langfuse`).
   `Dockerfile.runner` stays unchanged and the emitter remains importable from
   `scripts/bench_run.py` inside the container, which is the constraint Phase 4
   depends on.

   Langfuse fields are set through documented span attributes:
   `langfuse.session.id`, `langfuse.trace.name`, `langfuse.trace.tags`,
   `langfuse.observation.type`, `langfuse.observation.input` / `.output`,
   `langfuse.observation.level`, `langfuse.observation.metadata.<key>`,
   `langfuse.observation.usage_details`, `langfuse.observation.cost_details`,
   `langfuse.environment`.

5. **Deterministic ids.** `trace_id = sha256(f"{run_id}:step:{n}").hexdigest()[:32]`
   (a 16-byte OTEL trace id), span ids likewise truncated to 16 hex chars.
   Re-exporting a run **updates** its traces instead of duplicating them. This is what makes the exporter safe to re-run and safe
   to call from more than one place (worker completion, re-validate, backfill).

6. **Fail-open, everywhere.** Follows the repo's existing convention for
   optional enrichment ("all fail silently" in `bench_run.py`, the audit
   findings parser that never raises). A Langfuse outage degrades observability
   and nothing else.

7. **Redaction is mandatory, not optional.** Prompts and env dumps carry
   `GATEWAY_TOKEN`, `LITELLM_MASTER_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`. Reuse
   `harness_lifecycle/sanitizer.py` on every payload before it leaves the
   process. Langfuse is an external service; a leaked key there is a leaked key.

---

## Environment contract

New block in `.env.example`, under the existing tracing section:

```dotenv
# --- Langfuse observability (optional; off by default) -----------------------
# Master switch. false => zero HTTP calls, zero overhead, no code path taken.
# Setting it true with missing keys logs one warning and degrades to disabled.
LANGFUSE_ENABLED=false
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
# Separates this box's traces from CI or a teammate's in the same project.
LANGFUSE_TRACING_ENVIRONMENT=lab-local
# Base URL of the Langfuse project, used only to build the "View in Langfuse"
# deep link in the run detail page. e.g. https://cloud.langfuse.com/project/abc123
LANGFUSE_PROJECT_URL=
# Prefix for the session id. Keep in sync with runs.yaml langfuse_session_id.
LANGFUSE_SESSION_PREFIX=bench-
# When true, tasks.py exports a run's traces as soon as it reaches a terminal
# state. False keeps the integration manual (`make langfuse-export RUN=<id>`).
LANGFUSE_AUTO_EXPORT=true
```

Resolution order matches the rest of the app: `os.environ` first, then
`read_env_file_value` from `.env`. `LANGFUSE_ENABLED` is parsed strictly —
`{"1","true","yes","on"}` case-insensitive is on, anything else (including
unset and garbage) is off. **Unset means off**, so a fresh clone and CI stay
silent without configuration.

Also add `LANGFUSE_ENABLED`, `LANGFUSE_TRACING_ENVIRONMENT`,
`LANGFUSE_PROJECT_URL`, `LANGFUSE_AUTO_EXPORT` to the `GROUP_TRACING` allowlist
in `settings_service.py` so they are editable from the Settings page
(ADR-0014), with the secret key keeping its masked treatment.

---

## The trace model

For run `tetris-v1-K-071` with `max_steps=5` that stopped at step 3:

```
session: bench-tetris-v1-K-071
│
├── trace  run:tetris-v1-K-071            (envelope; run-level work only)
│   ├── span provision-workspace
│   ├── span finalize-workspace
│   ├── span objective-validation         input: validation spec / output: verdict
│   ├── span llm-audit                    (when an audit exists)
│   └── scores: functional_score, composite, interventions, steps_used,
│               swebench_resolved (SWE lanes only)
│
├── trace  step-1 · tetris-v1-K-071
│   ├── input:  full text of steps/step-1-prompt.md      <- "los prompts enviados"
│   ├── output: stop reason + tail of steps/step-1.log
│   ├── span agent-execution              model, exit code, duration
│   ├── span swe-evaluation               F2P/P2P (SWE lanes only)
│   └── usage_details (from usage.json / opencode session db, when attributable)
│
├── trace  step-2 · tetris-v1-K-071
└── trace  step-3 · tetris-v1-K-071       level=ERROR when rc != 0
```

A single-step case produces `run:<id>` + `step-1 · <id>` — the same shape,
two traces, one session. That satisfies "un prompt simple y una iteración =
una traza en una sesión" without a second code path.

**Trace-level metadata** (every trace, from `metadata.json`): `run_id`,
`case`, `case_type`, `cell_id`, `harness`, `planner_model`, `executor_model`,
`prompt_variant`, `max_steps`, `condition_hash`, `harness_fingerprint`,
`skills_bundle`, `skills_disabled`, `skills_experimental`, `template_ref`,
`base_commit`, `continues_run`, `batch_id`, `runner_image`.

**Tags** (flat `key:value`, per `harnesses/metadata-standard.md`):
`case:<id>`, `type:<case_type>`, `harness:<h>`, `executor:<model>`,
`planner:<model>`, `prompt:<variant>`, `condition:<hash8>`,
`batch:<batch_id>` (when present), `ablation` (when `skills_disabled` is
non-empty). These are what make Langfuse filterable by experiment arm, which
is the whole point of the lab.

**Scores** are the sleeper feature: pushing `functional_score`,
`swebench_resolved`, `composite` and `steps_used` as Langfuse scores makes the
Langfuse UI able to compare arms directly — a second, independent read on the
leaderboard the lab already computes.

---

## Phase 1 — The emitter and the exporter (the core)

### New module: `src/harness_lab_app/observability/`

| File | Responsibility |
|---|---|
| `config.py` | `LangfuseConfig.from_env()` → frozen dataclass with `enabled`, host, keys, environment, session prefix, project url. `enabled` is False whenever the switch is off **or** either key is missing. One `logging.warning` on the enabled-but-unconfigured case, emitted once per process. |
| `client.py` | `LangfuseClient` — stdlib `urllib` POST to `{host}/api/public/ingestion`, Basic auth from `public:secret`, batches of ≤ 100 events, one retry on 5xx/timeout, then give up. `NullClient` with the same interface returned when disabled. Never raises: every public method is wrapped and returns a bool. |
| `events.py` | Typed builders for the ingestion event envelopes (`trace-create`, `observation-create`, `score-create`) + deterministic id helpers. Pure functions, no I/O — this is where the tests live. |
| `run_export.py` | `build_run_events(run_dir: Path) -> list[dict]` — pure: reads the run directory, returns the event list. `export_run(run_dir, config) -> ExportResult` — impure wrapper that sends them. |
| `redaction.py` | Thin adapter over `harness_lifecycle/sanitizer.py`, applied to every string field in every event. |

**Why `build_run_events` is pure and separate:** it is the entire testable
surface. Given a fixture run directory it must produce a stable event list —
no network, no clock, no env. `export_run` is then a five-line shipper.

### What `build_run_events` reads

| Source | Yields |
|---|---|
| `metadata.json` | session id, trace metadata, tags, `created_at` |
| `steps/step-N-prompt.md` | trace **input** (the prompt actually sent) |
| `steps/step-N.log` | trace **output** (tail), start timestamp from the `# <harness> run started <iso>Z` header, exit-code line, stop signal |
| `steps/step-N.events.jsonl` | claude lane: per-turn usage and tool calls (Phase 4 detail; Phase 1 uses only the terminal `result` event for usage) |
| `opencode.log` | single-step runs, which write here instead of `steps/` |
| `usage.json` | run-level tokens/cost/turns → `usage_details` + `cost_details` |
| `validation` (DB) / `audit_findings.json` | validation span + scores |
| DB `Run` row | state, exit code, failure reason, score, interventions, batch/parent lineage |

**Timestamp reconstruction:** step start comes from the log header; step end
is the next step's start, or `Run.ended_at` for the last one. When a header is
unparseable, fall back to file mtime, and when that fails, to
`Run.started_at` — a trace with an approximate timestamp is still worth far
more than no trace. Record `timestamp_source: "log-header" | "mtime" | "run"`
in the trace metadata so the imprecision is visible rather than pretended away.

### New CLI: `scripts/export_langfuse_traces.py`

```
python scripts/export_langfuse_traces.py --run tetris-v1-K-071
python scripts/export_langfuse_traces.py --all           # backfill everything on disk
python scripts/export_langfuse_traces.py --run X --dry-run   # print events, send nothing
```

`--dry-run` prints the event tree as JSON — the development and review loop for
the trace shape, and what the tests assert against.

`scripts/export_langfuse_usage.py` (the existing stub) is **deleted** and its
docstring's responsibilities absorbed here; it has never done anything.

### Makefile

```
make langfuse-export RUN=<id>     # one run
make langfuse-backfill            # every run under data/runs/
make langfuse-check               # config + reachability, prints enabled/disabled
```

### Tests (`tests/harness_lab_app/test_observability_*.py`)

- `test_config`: unset → disabled; `true` + missing key → disabled + one warning;
  full config → enabled. No env leakage (the suite already redirects `.env`).
- `test_events`: deterministic ids stable across calls; re-building the same run
  produces byte-identical ids.
- `test_run_export`: fixture run dirs — (a) single-step Tetris, (b) 5-step run
  stopped at 3, (c) SWE-bench run with F2P/P2P, (d) failed run with `rc != 0`
  → asserts trace count, session id, input == prompt file content, ERROR level
  on the failed step, scores present.
- `test_redaction`: a fixture prompt containing `GATEWAY_TOKEN=sk-real` never
  appears in any emitted event.
- `test_client_disabled`: with a fake transport that raises on any call,
  a disabled client completes successfully — **proves the off switch**.
- `test_client_failure_open`: transport raises `URLError` → `export_run`
  returns a failed `ExportResult` and does not propagate.

**No test touches the network.** Transport is injected.

---

## Phase 2 — Wire it into the run lifecycle

### `tasks.py`

Add `_export_langfuse_traces(session, run_id)` next to the existing
`_run_objective_validation`, with the identical shape: wrapped in
`try/except Exception`, failures recorded as a `RunEvent(level="warning")`,
never propagated. Call it:

1. after a run reaches a terminal state (`completed`, `failed`,
   `awaiting_manual_score`),
2. after `validate_run` (so validation spans and objective scores attach),
3. after the audit task (so the audit span attaches).

Because ids are deterministic, calls 2 and 3 update the same traces rather
than duplicating them. Gated on `LANGFUSE_AUTO_EXPORT`.

### `api.py` + `RunDetail.tsx`

`GET /api/runs/{id}` gains:

```json
"langfuse": {
  "enabled": true,
  "session_id": "bench-tetris-v1-K-071",
  "session_url": "https://cloud.langfuse.com/project/abc/sessions/bench-tetris-v1-K-071",
  "exported_at": "2026-08-27T12:00:00Z"
}
```

`session_url` is null when `LANGFUSE_PROJECT_URL` is unset. The frontend shows
a **"View in Langfuse"** button when `enabled && session_url`, and renders
nothing at all otherwise — the disabled case must not leave dead UI behind.

An **"Export traces"** action (`POST /api/runs/{id}/langfuse-export`) covers
re-export after tuning and runs that predate the feature.

### `health_service.py`

One new check row, `langfuse`:

- switch off → `unknown` / "Langfuse tracing disabled (LANGFUSE_ENABLED=false)."
  — **informational, never a warning**; off is a valid, supported state.
- on + keys missing → `warn` with the missing variable named.
- on + configured → probe `GET {host}/api/public/health` → `ok` or `warn`.

---

## Phase 3 — Close Layer B (real tokens and cost per call)

The web lane currently sends the shared `GATEWAY_TOKEN`, so every gateway
generation from every run lands in Langfuse unattributed. The CLI lane already
solved this and the web lane never inherited it.

1. Move `create_litellm_key` from `scripts/create_run.py` into
   `src/harness_lab_app/gateway_keys.py` (stdlib only — the runner may need it),
   extended with `metadata.tags` alongside the existing metadata block.
   `scripts/create_run.py` imports it; behavior there is unchanged.
2. In `tasks.py`, before launching the container: mint a per-run key aliased to
   `run_id` and override `GATEWAY_TOKEN` in the environment returned by
   `build_run_environment()` for that run only.
3. **Fallbacks, in order:** `LITELLM_MASTER_KEY` unset → shared token +
   `RunEvent` warning. Key creation fails → shared token + warning. A run
   never fails because attribution could not be set up.
4. Keys are kept (not deleted) so LiteLLM's per-key spend view stays queryable;
   `key_alias = run_id` makes them greppable. Add a
   `make langfuse-prune-keys DAYS=30` chore rather than deleting inline.

### Verification spike — do this before writing step 2

The comment in `create_run.py` asserts that "LiteLLM forwards metadata.session_id
to Langfuse for session grouping". That claim has never been verified for this
gateway deployment, and the whole of Layer B rests on it. One cheap experiment:

```
# mint a key with metadata.session_id=spike-001, fire one completion with it,
# then check whether a session "spike-001" exists in Langfuse.
```

Outcomes:
- **Works** → Phase 3 is exactly as written above.
- **Does not work** → fall back to LiteLLM's documented per-request
  `langfuse_*` request headers (`langfuse_trace_id`, `langfuse_trace_metadata`),
  injected via opencode's `provider.gateway.options.headers` in the
  config overlay (`opencode_overlay.py` already writes a per-run overlay, so
  the seam exists). Note this fallback is opencode-only; the `claude` and
  `codex` harnesses would keep session-less generations, and Layer A remains
  their observability.
- **Either way**, Layer A (Phases 1–2) is unaffected. This is why it ships first.

### Honest limitation to document

Direct-lane runs (`lmstudio/*`, `ollama/*` with no gateway in the path) will
**never** have Layer B — nothing sits between the harness and the model server
to report. Their token data comes from `pull_usage_opencode_db` at step
granularity and is attached to the step trace as `usage_details`. Say this in
the guide instead of letting a user wonder why some sessions have generations
and others do not.

---

## Phase 4 — Live emission and tool-level detail (optional, later)

Only worth doing once the trace shape has stopped changing.

1. **Live step traces from `bench_run.py`.** The Phase 1 client is stdlib and
   already importable in the runner container. Emit each step's trace as the
   step ends rather than after the run. `Dockerfile.runner` unchanged.
   Guarded by the same `LANGFUSE_ENABLED`, plus a hard 2s timeout so a slow
   Langfuse can never extend a run.
2. **Tool timeline inside a step.** `attribution.parse_logs` already extracts
   tool calls, files read/edited, and bash commands from opencode logs, and the
   `claude` lane has structured `step-N.events.jsonl`. Emitting these as child
   observations (`as_type="tool"`) turns each step from a wall of text into a
   readable timeline — the single biggest jump in "look at a case in detail",
   and it reuses code that already exists and is already tested.
3. **Prompt registry.** Register `prompt_variant` versions as Langfuse prompts
   so a variant change is diffable in the Langfuse UI. Nice-to-have; the
   condition hash already covers reproducibility.

---

## Explicitly out of scope

- Replacing `usage.json`, `results.csv`, or the leaderboard with Langfuse
  queries. Langfuse is a viewer, not a store — the lab must stay fully
  functional with the switch off.
- Langfuse-hosted evaluations / datasets. The lab has its own scoring
  (`agentic-scoring.md`) and objective validation; duplicating it there would
  create two disagreeing verdicts.
- Multi-user or per-user attribution. Single-operator lab (ADR-0015).
- Automatic Langfuse deployment. Bring your own host or cloud project.

---

## Sequencing

| Step | Deliverable | Gate |
|---|---|---|
| 0 | Verification spike (Layer B mechanism) — 30 min, no code | Documented outcome in the ADR |
| 1 | `observability/` package + `build_run_events` + tests | `make check` green; `--dry-run` output reviewed by hand |
| 2 | `export_langfuse_traces.py` + Makefile targets; backfill existing runs | A real historical run readable in Langfuse |
| 3 | `tasks.py` auto-export + API field + RunDetail button + health row | A new run appears in Langfuse with no manual step |
| 4 | Per-run virtual key in the web lane (Phase 3) | Generations and lab traces in one session |
| 5 | `docs/guide-langfuse-observability.md` + ADR-0027 + `/retro` | `make check-sync` clean |

Steps 1–3 are the useful unit — after step 3 the original complaint is solved.
Step 4 upgrades it from "structure and prompts" to "structure, prompts and
money". Phase 4 is a separate decision.

## Files touched

**New:** `src/harness_lab_app/observability/{__init__,config,client,events,run_export,redaction}.py`,
`src/harness_lab_app/gateway_keys.py`, `scripts/export_langfuse_traces.py`,
`tests/harness_lab_app/test_observability_*.py` (+ fixture run dirs),
`docs/guide-langfuse-observability.md`, `docs/adr/0027-langfuse-observability-for-runs.md`.

**Changed:** `.env.example`, `settings_service.py`, `tasks.py`, `api.py`,
`schemas.py`, `health_service.py`, `scripts/create_run.py`, `Makefile`,
`frontend/src/pages/RunDetail.tsx`, `frontend/src/types.ts`,
`docs/onboarding.md`, `harnesses/metadata-standard.md`.

**Deleted:** `scripts/export_langfuse_usage.py` (stub, never implemented).

**Not changed:** `Dockerfile.runner`, `pyproject.toml` dependencies — the
stdlib client is what keeps both untouched.
