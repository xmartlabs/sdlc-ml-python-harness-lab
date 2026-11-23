# Plan — Antigravity CLI (`agy`) as an API-key harness

- **Date:** 2026-11-22
- **Status:** Implemented (2026-11-22). See [ADR-0096](../adr/0096-antigravity-cli-runs-on-a-gemini-api-key.md); live authenticated smoke test pending a key.
- **Scope:** add an automated `antigravity_cli` harness that drives Google's
  official Antigravity CLI (`agy`) headless, authenticated **only** by a Gemini
  API key, under the lab's existing cases, per-run containers, conditions,
  scoring, attribution and provenance. Keep the legacy `gemini` harness and the
  manual `antigravity` IDE flow as they are.
- **Related:** ADR-0049, ADR-0061, ADR-0073, ADR-0075, ADR-0094,
  [harness-adapters.md](../harness-adapters.md),
  [runbook-claude-codex.md](../runbook-claude-codex.md),
  [harnesses/antigravity.md](../../harnesses/antigravity.md)

## 1. Terms of use (checked first)

| Source | What it says |
|---|---|
| antigravity.google/docs/faq | Using third-party software to access Antigravity violates the ToS; "To use a third-party coding agent with Gemini, we recommend using a Gemini Enterprise or Google AI Studio API key." Wrapping `agy` inside third-party applications "is not supported under consumer plans". |
| antigravity.google/docs/install (Authenticate) | API key mode "suits headless and CI runs"; requests go straight to the Gemini API and "the CLI never establishes an account session". Needs `modelProvider: "gemini"` **and** `GEMINI_API_KEY`; `GOOGLE_API_KEY` and `.env` files are not read. |
| developers.googleblog.com (2026-05-19) | Gemini CLI stops serving Google AI Pro/Ultra and free individual use on 2026-06-18; Antigravity CLI replaces it. |

So: the lab drives `agy` only with an API key the operator supplies. No OAuth,
no keychain, no consumer session, no account rotation, no proxy. The CLI's own
`~/.gemini` from the host is never read.

## 2. Verified facts (agy 1.5.14, Linux arm64, no real credential)

Every fact was checked against the official binary in a `python:3.11-slim`
container (the runner's base), with no key, a fake key, and a local Gemini API
mock reached through the documented `GOOGLE_GEMINI_BASE_URL`.

| Fact | Evidence |
|---|---|
| Release manifest `…/manifests/linux_{arm64,amd64}.json` names version, versioned GCS tarball and sha512 (1.5.14) | install.sh + manifest |
| Tarball holds one static binary `antigravity`; `--version` → `1.5.14` | probe |
| `--settings '<json>'`: "Settings for this session only, never saved" | `--help` |
| Writes conversations, `brain/`, logs, sqlite DBs, builtin skills under `$HOME/.gemini/` | probe |
| No key + `modelProvider: gemini` → exit 1, stderr only, no JSON | probe |
| Invalid key → exit 3, `AGY_ERROR: {…"reason":"API_KEY_INVALID"…}` on stderr + `result{status:ERROR}` | probe |
| 429 → exit 3 after ~135 s of retries (`retryable: true`); 403/404/500 → exit 3 at once; connection refused → exit 3 after ~145 s | mock |
| Unknown `--model` → exit 1, `result{status:ERROR}`, error lists valid labels | probe |
| `agy models` lists 25 slugs (Neon/Carbon/Hydrogen/Argon, 3.8/3.7/3.6 Flash, 3.1 Pro) **without calling the API** — it does not validate the key | mock |
| Default model with a key: `gemini-3.1-pro-preview` (label "Gemini 3.1 Pro (Low)") | mock request path |
| Slug → API model: `gemini-4-neon-*`→`gemini-4-neon`, `…-carbon-*`→`gemini-4-carbon`, `…-hydrogen-*`→`gemini-4-hydrogen`, `…-argon-*`→`gemini-4-argon`, `gemini-3.X-flash-*`→`gemini-3.X-flash`, `gemini-3.1-pro-*`→`gemini-3.1-pro-preview`; the suffix is the thinking level | mock request path + `thinkingConfig` |
| Stream: `init{conversation_id, init{cwd, tools, permission_mode, model?}}`, `step_update{step_index, state ACTIVE/DONE/ERROR, step_type, tool_name, tool_info{name, parameters, output, error}, text_delta, usage, subagent_info}`, `result{status, response, error, duration_seconds, num_turns, usage, finished_reason}` | probe |
| `init.model` is present only when `--model` is passed; **no event names the API model** that served | probe |
| Text streams as `ACTIVE` deltas; the closing `DONE` carries only the tail (`"\n"`); `result.response` is the full text | slow mock |
| Usage: `input_tokens` excludes cache, `cache_read_tokens` separate, `output_tokens` includes `thinking_tokens`, `result.usage` = sum of steps | mock with known numbers |
| Tools on the wire: `read_file{Path}`, `write_file{Path,Content}`, `edit_file{Path,Target,Replacement}`, `run_command{CommandLine}`, `search_web{query}`, `invoke_subagent`, … (only `Path`/`CommandLine` are echoed in `tool_info.parameters`) | request body + stream |
| A failed command is a `DONE` tool step whose output says `Command exited with code N` | mock |
| Workspace skills: **only `.agents/skills`** (not `.gemini/skills`, not `.claude/skills`); rules: `AGENTS.md`, `GEMINI.md` **and** `.agents/rules/*.md`; builtin skills under `$HOME` always listed | system prompt captured by mock |
| Headless `request-review`: a tool that needs approval is soft-denied and **the turn ends**: exit 0, `SUCCESS`, empty response, no further request | mock |
| `permissions.allow: [command(*), read_file(*), write_file(*), read_url(*)]` + `--mode accept-edits` → every command (incl. `$(…)`), read and write proceeds; `mcp`/`execute_url` stay denied | mock |
| `--sandbox` inside the container: `fork/exec … operation not permitted` | probe |
| **`run_command` children inherit the whole environment, `GEMINI_API_KEY` included**; no documented scrubbing | probe (`${#GEMINI_API_KEY}` = 35) |
| The key is not written to `$HOME`, the CLI log or the stream | grep after a run |
| `--input-format stream-json` on stdin carries ≥150 KB intact (argv `-p` fails there: ARG_MAX); real case prompts are ≤ 14 KB | mock |
| `--print-timeout 3s` → exit 3, `DEADLINE_EXCEEDED`, partial result | mock |
| `AGY_CLI_DISABLE_AUTO_UPDATE=true` disables the self-updater | docs (troubleshooting) |

## 3. Design

### 3.1 One harness, reuse everything

`antigravity_cli` joins the native-CLI path codex/gemini already use:
`bench_run.py` builds the command, `run_opencode` tees the raw stream to
`step-N.raw.jsonl`, `harness_events` translates it into the claude shape
`step-N.events.jsonl`, and every reader (attribution, trajectory, usage,
progress, Langfuse, step reward) stays unchanged. No second runner.

The existing `antigravity` id (manual IDE flow, `make bench-antigravity`,
`governance.HARNESS_RULES_FILE`, registry tool) is untouched; historical runs
keep their meaning.

### 3.2 Credential: an API key, not a session

`native_auth.py` keeps codex/gemini (subscription) unchanged. A separate
module, `api_key_auth.py`, owns the API-key harness:

- **Store:** `<HARNESS_LAB_AUTH_DIR>/antigravity_cli/gemini_api_key` (0600,
  dir 0700), outside the repository, the same store ADR-0094 uses. Never in
  `.env` (which every run container receives), never in the DB.
- **Writes only by the worker** (the API mounts the store read-only). The API
  forwards the key once to an interactive-lane task; Celery's `argsrepr` /
  `kwargsrepr` are set to `***` so it never reaches the worker log or task
  events. The key is never returned: status shows `configured`, a masked tail
  (`…ab12`), and when it was saved.
- **Run isolation:** a fresh `HOME` per run/test (`data/runs/<run>/agy-home`,
  deleted after); config via `--settings` (never written); env built from
  scratch: `.env` values minus every provider credential (all
  `_BILLING_OVERRIDES` plus `GATEWAY_TOKEN`, `LITELLM_MASTER_KEY`, `LANGFUSE_*`
  secrets, `NVIDIA_API_KEY`, `GITHUB_TOKEN`…, and any `GOOGLE_GEMINI_BASE_URL`
  from `.env`), plus `GEMINI_API_KEY` from the store and
  `AGY_CLI_DISABLE_AUTO_UPDATE=true`.
- **Never read**: macOS keychain, host `~/.gemini`, `GOOGLE_API_KEY`,
  `oauth_creds.json`.

### 3.3 Exposure of the key to agent-run code (the hard limit)

`agy` passes its environment to every command the agent runs and has no
option to scrub it; `--sandbox` does not work in the container. **An agent, or
code it executes, can read the key.** Mitigations:

1. The key reaches only `antigravity_cli` containers, never another harness.
2. Every other secret is removed from that container's env.
3. **Untrusted code is refused by default**: preflight rejects an
   `antigravity_cli` run whose workspace is third-party code — SWE-bench / SWE
   Pro instances and external target repositories — unless the operator
   enables `HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1`. Lab-authored cases and the
   operator's own ad hoc folders are allowed.
4. Docs tell operators to use a **dedicated, budget-capped, restricted** key
   (AI Studio project with spend cap; key restricted to the Generative
   Language API) and to rotate it after any untrusted run.
5. The run stream is scanned for the literal key before archiving; a hit is
   redacted (`***`) in `step-N.raw.jsonl`/`events.jsonl`/logs and recorded as
   `secret_exposed: true` in `metadata.json`.

### 3.4 Permissions: explicit, not "dangerous"

In the container: `--mode accept-edits` + `--settings` with
`permissions.allow = [command(*), read_file(*), write_file(*), read_url(*)]`
and `--disable-slash-commands`. Verified to let a coding agent work exactly as
claude/codex do there (the container is the sandbox, ADR-0094 §3), while MCP
and browser execution stay denied. No `--dangerously-skip-permissions`.
`metadata.harness_sandbox = "container"`.

**Host lane:** refused at preflight (no container, and `--sandbox` scope plus
the env exposure make it unsafe). Docker only.

### 3.5 Command

```
agy --settings '<json>' --mode accept-edits --disable-slash-commands \
    --model <slug> --input-format stream-json --output-format stream-json
# stdin: {"event":"user","message":{"content":"<prompt>"}}
```

`--print-timeout` is not set: the lab's watchdog already owns time budgets,
and a CLI-side cut would differ from the other arms.

### 3.6 Models

- `agy models` (with the store's key and a fresh HOME) is the live catalog,
  refreshed from the card ("Refresh models") by a worker task and cached in
  `data/app/native-connect/antigravity_cli.models.json`.
- `native_models.NATIVE_MODELS["antigravity_cli"]` holds the 25 slugs of
  1.5.14 as the offline fallback (source-tagged), with the API model each maps
  to. Not the old gemini catalog.
- A model is **required** (preflight): without `--model`, the stream never
  names the model, and the run would be filed under a model nobody chose.
- Recorded: `executor_model` (requested slug), `detected_model` from
  `init.model`, `api_model` (resolved from the verified slug map, `null` when
  the slug is not in it), `thinking_level` (the suffix).

### 3.7 Events → claude shape

`harness_events.antigravity_to_claude`:

| agy | claude shape |
|---|---|
| `init` | `system/init{session_id: conversation_id, model, harness}` |
| `step_update` agent_response (ACTIVE deltas joined, closed by DONE) | `assistant` text |
| `step_update` tool DONE/ERROR | `assistant` tool_use + `user` tool_result (`is_error` on ERROR or `Command exited with code ≠0`) |
| tool names | `read_file/view_file`→Read, `write_file/write_to_file`→Write, `edit_file/replace_file_content/multi_replace_file_content`→Edit, `run_command`→Bash, `search_web`→WebSearch, `read_url_content`→WebFetch, `invoke_subagent`→Agent, `grep_search`→Grep, `find_by_name`→Glob, `list_dir`→LS; others `agy:<name>` |
| `result` | `result{usage{input, cache_read, output, reasoning=thinking}, duration_ms, num_turns, total_cost_usd: null, billing_mode: "api", harness}` |

Skill invoked = a `read_file` of `…/skills/<name>/SKILL.md` (agy has no
skill tool; same rule as codex). Error `result` renders as `API Error: …`
so `harness_error_cause` and failure classification read it unchanged.
Missing fields stay absent (no zeros invented).

### 3.8 Cost

`billing_mode: "api"`; `cost_usd` = the run's tokens × the dated Gemini API
price (AI Studio paid tier, Standard, read 2026-11-22) of the API model its slug
calls, `cost_source: "api_list_price_estimate"`, `cost_basis` naming model,
source, date and tier. Never `subscription_quota`. 3.1 Pro has a >200k tier
→ short-context rate, a floor. `gemini-3.7-flash` has no published price →
`cost_usd: null`, `cost_source: "api_price_unknown"` with a reason.
(Decided with the user: the estimate goes in `cost_usd`, so the arm ranks and
compares like any per-token arm.)

### 3.9 Conditions and hashing

- Runner mirror for the harness: `.agents/skills` (agy's only workspace
  catalog). It is masked today for every harness; for `antigravity_cli` the
  other mirrors (`.claude/.codex/.gemini/.opencode`) are masked instead.
- Rules: agy reads `AGENTS.md` + `GEMINI.md` + `.agents/rules/` → with
  `lab_root` it sees three adapters (the same catalog of rules three times).
  Documented; `governance.HARNESS_RULES_FILE["antigravity_cli"] =
  ".agents/rules/GEMINI.md"`. `bare`/`native`/`injected` masks already cover
  `.agents`, `AGENTS.md`, `GEMINI.md`.
- `injected`: projects registry tool `antigravity` (`.agents/skills` +
  `.agents/rules/GEMINI.md`).
- `provenance._HASHED_HARNESSES += {"antigravity_cli"}` (additive: no history
  re-binned).

### 3.10 UI (System → Agent CLIs)

New card **Antigravity CLI**, state machine `Not configured → Configured →
Verified / Failed`:

- *Configure API key* (password field, submit → worker writes store).
- *Test connection*: real request with a run's exact container, env and
  flags; shows answer, slug requested, API model resolved, seconds, error and
  the fix (invalid key, quota/billing, model not available, network).
- *Refresh models* (`agy models` with the key).
- *Remove credentials*.
- Shows CLI version, lane, provider "Gemini API", billing "API usage", masked
  key tail. No deprecation notice (that stays on the `gemini` card).

Harness selectors (`HARNESS_OPTIONS`, guided chooser, experiments, audit,
subject validation, schemas regexes, `bench_run --harness`) gain
`antigravity_cli`.

### 3.11 Install in the runner

```
ARG AGY_VERSION=1.5.14
ARG AGY_BUILD=5302690847260672
ARG AGY_SHA512_AMD64=8331…  ARG AGY_SHA512_ARM64=c9b2…
RUN arch=$(dpkg --print-architecture) … curl versioned tarball → sha512sum -c
    → /usr/local/bin/agy ; agy --version
LABEL antigravity-cli.version=…
ENV AGY_CLI_DISABLE_AUTO_UPDATE=true
```

Official GCS artifact of the official installer's manifest, pinned and
checksummed (reproducible; the installer itself only fetches "latest").

## 4. Out of scope / deferred

- ADC / Vertex / Gemini Enterprise login (the alternative authorized path for
  orgs; documented, not wired).
- Host-lane runs.
- Per-run network egress restriction (would also stop `read_url`).
- Live authenticated smoke test: needs a real key (pending, §5).

## 5. Validation

Unit: key store (write/read/mask/remove, perms), env construction (no
foreign secret, key only for this harness), command builder, event
translation from **fixtures captured from agy 1.5.14** (success with tools,
invalid key, 429, network, unknown model, timeout), usage mapping, cost
estimate, preflight (no key, no model, unknown model, untrusted target, host
lane), failure classification, condition hash unchanged for other harnesses,
redaction. Integration: executor mounts/env for an `antigravity_cli` run;
API never echoes the key; Celery argsrepr masked. `make check` +
`make check-frontend`. Live: pending a key.

## 6. Decisions taken with the user (2026-11-22)

1. `cost_usd` for API-key runs: the list-price estimate goes **in** `cost_usd`
   (`cost_source: api_list_price_estimate`).
2. Untrusted-code guard: **refuse by default**, override with
   `HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1`.
