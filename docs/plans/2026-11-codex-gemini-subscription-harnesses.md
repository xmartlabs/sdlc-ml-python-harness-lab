# Plan — Codex and Gemini as subscription-backed harnesses

- **Date:** 2026-11-19
- **Status:** Implemented (2026-11-20). See [ADR-0094](../adr/0094-codex-and-gemini-run-on-their-own-subscription-login.md).
- **Scope:** run cases through the official Codex CLI and Gemini CLI with their
  native subscription login (no per-token API), under the lab's existing case
  system, per-run containers, harness conditions, scoring and provenance.
- **Related:** ADR-0043, ADR-0049, ADR-0061, ADR-0073, ADR-0075, ADR-0081,
  [harness-adapters.md](../harness-adapters.md),
  [runbook-claude-codex.md](../runbook-claude-codex.md)

## 1. Verified facts (installed CLIs, not memory)

Every flag and behaviour below was checked against **codex-cli 0.171.0** and
**@google/gemini-cli 0.70.0** installed in an isolated scratch directory, plus a
real `harness-lab-runner:latest` container, and the official source/docs that
ship with those exact versions (`openai/codex@rust-v0.171.0`,
`gemini-cli/bundle/docs`). No login was used.

### Codex 0.171.0

| Fact | Evidence |
|---|---|
| The lab's current command `codex --quiet --full-auto …` is **broken** | `error: unexpected argument '--full-auto' found` |
| Headless entry point: `codex exec [--json] [-s MODE] [-m MODEL] [-]`, `-` reads the prompt from stdin | `codex exec --help` |
| `exec` forces `approval_policy = never` (never blocks on a prompt) | `exec/src/lib.rs:642` |
| JSONL events: `thread.started`, `turn.started`, `turn.completed{usage}`, `turn.failed{error}`, `item.started/updated/completed{item}`, `error` | `exec/src/exec_events.rs` |
| Items: `agent_message`, `reasoning`, `command_execution{command,exit_code,status}`, `file_change{changes[{path,kind}]}`, `mcp_tool_call`, `collab_tool_call`, `web_search`, `todo_list`, `error` | same |
| `turn.completed.usage` is the **thread's cumulative total**; `input_tokens` **includes** cached input (`non_cached = input − cached`) | `event_processor_with_jsonl_output.rs:119`, `protocol.rs:2764` |
| No auth → `thread.started` … `turn.failed` 401 after ~30 s of retries, exit 1 | live probe |
| `codex login status` → `Not logged in` exit 1 / `Logged in using ChatGPT` exit 0; `codex login --device-auth` exists | live probe + binary strings |
| Credentials: `$CODEX_HOME/auth.json` (`auth_mode`, `tokens{…}`); `cli_auth_credentials_store` defaults to `file` | config schema |
| Skills auto-loaded: `<project>/.codex/skills`, `.agents/skills` (project root → cwd), **`$HOME/.agents/skills`, `$CODEX_HOME/skills`** (user scope); `AGENTS.md` | `codex debug prompt-input` probes |
| `codex debug models --bundled` is the official catalog of this binary: `gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol/terra/luna`, `gpt-5.5`. **`codex-mini-latest` (today's only catalog entry) is not in it** | `codex debug models --bundled` |
| `--sandbox workspace-write` **cannot run inside the runner container**: bubblewrap cannot create a namespace and the legacy Landlock path panics (`requires bubblewrap`) | real `harness-lab-runner` container |
| `--dangerously-bypass-approvals-and-sandbox` is documented for "environments that are externally sandboxed" | `codex exec --help` |

### Gemini CLI 0.70.0

| Fact | Evidence |
|---|---|
| Headless: `gemini -p <prompt> --output-format stream-json`; stdin is **prepended** to `-p`, so `-p ""` + stdin carries any length | bundle source + live probe |
| `--approval-mode yolo` auto-approves every tool | `gemini --help` |
| stream-json events: `init{session_id,model}`, `message{role,content,delta}`, `tool_use{tool_name,tool_id,parameters}`, `tool_result{tool_id,status,output,error}`, `error`, `result{status,error?,stats}` | bundle source |
| `result.stats`: `input_tokens` (prompt, **includes** cached), `input` (non-cached), `cached`, `output_tokens`, `total_tokens`, `duration_ms`, `tool_calls`, `models{…}` | `convertToStreamStats` |
| Exit codes: 0 ok, 1 API error, 41 auth not configured, 42 bad input, 53 turn limit | docs + live probe (41) |
| Tools: `read_file`, `write_file`, `replace`, `run_shell_command`, `glob`, `grep_search`, `list_directory`, `read_many_files`, `activate_skill{name}`, `web_fetch`, `google_web_search`, `invoke_agent`, … | bundle constants |
| Auth precedence from env: `GOOGLE_GENAI_USE_GCA=true` (Sign in with Google) **before** `GEMINI_API_KEY` | `getAuthTypeFromEnv` |
| OAuth cache: plain file `$GEMINI_CLI_HOME/.gemini/oauth_creds.json` (keychain only with `GEMINI_FORCE_ENCRYPTED_FILE_STORAGE`); headless reuses a cached login; `NO_BROWSER=true` gives a URL+code login | bundle source + auth docs |
| Skills: `.gemini/skills` or `.agents/skills` (workspace), `~/.gemini/skills` / `~/.agents/skills` (user); symlinks followed; context from `GEMINI.md` in cwd **and ancestors** | `gemini skills list` probes + docs |
| An untrusted folder is fatal headless; `GEMINI_CLI_TRUST_WORKSPACE=true` or `--skip-trust` | trusted-folders docs |
| Offline model list: `auto`, `gemini-2.5-pro`, `gemini-3.5-flash`, `gemini-3.1-flash-lite` | `gemini --list-models` |

Both CLIs install and start on the runner image (Node 20, aarch64).

## 2. Decisions that differ from the request (and why)

1. **Codex sandbox depends on the lane.** Host: `--sandbox workspace-write`, as
   asked. Docker: `--dangerously-bypass-approvals-and-sandbox`, because the
   requested mode kills every command in the container (verified). The per-run
   container is the sandbox there, the same pattern as claude's
   `--dangerously-skip-permissions` + `IS_SANDBOX=1`. The mode is recorded per run
   (`metadata.json: harness_sandbox`).
2. **Codex models: default plus the binary's own catalog, nothing else.**
   `codex-mini-latest` leaves the catalog (it is not in 0.171's catalog). The
   catalog lists the bundled `visibility: list` slugs, labelled "confirmed in
   codex-cli 0.171.0 catalog". Whether a ChatGPT plan grants a given slug is
   **not** confirmed without a login, so the UI default is "CLI default".
3. **Gemini models: default plus the offline list of the pinned CLI.**
4. **No effort for codex/gemini in this change.** Codex has
   `model_reasoning_effort`, but ADR-0081 scopes effort to claude; extending it is
   a separate decision (follow-up).
5. **The lab's own `.codex/` must not be the run's `CODEX_HOME`, and neither may
   your `~/.codex`.** `~/.codex` holds personal `skills/`, `memories/`, `AGENTS.md`
   and plugins, and Codex loads user skills from `$CODEX_HOME/skills` and
   `$HOME/.agents/skills`. Mounting it would contaminate every arm, `bare`
   included. Sessions are dedicated directories (§4.4).

## 3. Architecture

No new subsystem. Each harness stays a command builder in `bench_run.py` plus an
event normalizer, and everything downstream keeps reading one shape.

```
bench_run.py ──build_<h>_cmd──► CLI ──stdout JSONL──► steps/step-N.events.jsonl (raw, _received_at stamped)
                                                   └► render_<h>_event ─► step-N.log (readable)
harness_lab_app/harness_events.py   (NEW, pure)
   codex_to_claude(lines) / gemini_to_claude(lines) → claude-shaped event lines
   normalized_event_lines(run_dir, harness) → what every parser reads
attribution / trajectory / step_detail / score_service ── read normalized lines (unchanged parsers)
bench_run.collect_<h>_usage ── usage.json (+ billing_mode, cost_source)
```

**Normalization by translation, not N parsers.** The raw per-CLI JSONL stays on
disk untouched (provenance). The one existing claude-shaped reader set
(`parse_claude_events`, `from_claude_events`, `parse_claude_step_events`) gets a
translated view, so attribution, skill invocation, trajectories and Langfuse
light up for all three without forking four parsers. Tool names map onto the
claude vocabulary the parsers already know (`read_file`→`Read`,
`write_file`→`Write`, `replace`→`Edit`, `run_shell_command`/`command_execution`→
`Bash`, `activate_skill`→`Skill`, codex `file_change`→`Edit`/`Write`).
Unmapped tools keep their own name, prefixed with the harness.

**Skill activation for codex** has no tool call: codex opens `SKILL.md` with a
shell command. The trajectory already counts a `SKILL.md` path inside any tool
call as an invocation (`trajectory._skill_mentions`), so the translated
`Bash(cat …/skills/X/SKILL.md)` records `X` as invoked. That is the same
observation as claude's `Skill` tool and gemini's `activate_skill`. (An earlier
draft of this plan said codex skill use was not observable; the existing reader
shows it is.)

## 4. Stages

Each stage leaves `make check` green.

### A. Codex command (`scripts/bench_run.py`)
- `build_codex_cmd(model, *, sandbox)` →
  `codex exec --json --skip-git-repo-check -s workspace-write [-m M] -`, or the
  bypass flag in Docker. The prompt is **stdin**. `run_opencode` gains
  `stdin_text`.
- Lane detection: `HARNESS_LAB_IN_RUNNER=1`, set by `build_run_environment`.
- Tee raw JSONL to `step-N.events.jsonl` for codex and gemini, with
  `render_codex_event`.

### B. Gemini harness
- `build_gemini_cmd(model)` →
  `gemini --output-format stream-json --approval-mode yolo [-m M] -p ""`, prompt
  on stdin, `GEMINI_CLI_TRUST_WORKSPACE=true`, `GOOGLE_GENAI_USE_GCA=true` in its
  env.
- `--harness` choices and `build_harness_cmd` dispatch. `render_gemini_event`.
  Exit codes 41/42/53 become actionable `harness_error_cause` lines.
- `run_failures._HARNESS_SIGNALS["codex"|"gemini"]` from the rendered markers.

### C. Catalog, preflight, capabilities
- `native_models.NATIVE_MODELS["codex"|"gemini"]` with `source` and
  `cli_version`, plus `MIN_CODEX_CLI_VERSION` / `MIN_GEMINI_CLI_VERSION`
  (`0.171.0` / `0.70.0`, the versions verified here).
- `native_catalog`: codex and gemini are never gateway-routed (`on_gateway: None`).
- `RunService.preflight`:
  - host lane: CLI on PATH and a logged-in session (`codex login status`, or the
    existence of `oauth_creds.json`).
  - docker lane: the runner image label names the CLI version and the provider
    session directory has a credential.
  - An unknown model id is passed through (the CLI decides), as for claude. A
    `gateway/` id is refused for codex/gemini instead of being silently dropped.
  - Effort is refused (ADR-0081 unchanged).
- Harness regexes in `schemas.py` accept `gemini`.

### D. Docker runner and native auth
- `Dockerfile.runner`: `ARG CODEX_VERSION=0.171.0`, `ARG GEMINI_CLI_VERSION=0.70.0`,
  `npm install -g` pinned, plus the labels `codex.version` / `gemini-cli.version`.
  `harness_facts` reads them.
- **Session store outside the repo:** `HARNESS_LAB_AUTH_DIR` (default
  `~/.harness-lab/auth`), one sub-directory per provider:
  - `codex/` = a `CODEX_HOME` holding `auth.json` + a lab `config.toml`
    (`check_for_update_on_startup=false`, `analytics.enabled=false`,
    `features.memories=false`) and nothing else.
  - `gemini/.gemini/` = `oauth_creds.json` + `settings.json`
    (`security.auth.selectedType="oauth-personal"`,
    `general.enableAutoUpdate=false`, `privacy.usageStatisticsEnabled=false`).
- `DockerRunExecutor.start(harness=…)` binds **only that provider's** directory,
  rw (token refresh writes back), at `/run/harness-auth/<provider>`. It sets
  `CODEX_HOME` / `GEMINI_CLI_HOME` and `HOME=/tmp/agent-home` (an empty home, so
  no user-scope skills).
- **Env isolation:** for codex/gemini runs, `build_run_environment(harness)` drops
  `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL`, `GEMINI_API_KEY`,
  `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_GEMINI_BASE_URL`,
  `GOOGLE_APPLICATION_CREDENTIALS` and `ANTHROPIC_*`, so a key from `.env` cannot
  silently switch the run to per-token billing.
- `make auth-codex` / `make auth-gemini` / `make auth-status`. Login runs the
  official CLI with `CODEX_HOME` / `GEMINI_CLI_HOME` pointed at the store:
  `codex login --device-auth`, and `NO_BROWSER=true gemini` for "Sign in with
  Google". Auth is never copied out of `~/.codex` or `~/.gemini`.
- Host lane: the same store, the same env, so host and docker behave alike.

### E. Event normalization and usage
- `harness_lab_app/harness_events.py` (pure): `codex_to_claude`,
  `gemini_to_claude`, `normalized_event_lines(run_dir, harness)`.
- `attribution.read_step_events`, `trajectory.build`, `score_service`, and
  `observability.step_detail` read through it, choosing by `metadata.json` harness.
- `bench_run.collect_codex_usage`: last `turn.completed` per step file, summed
  across steps. `input_tokens = input − cached`, `cache_read_tokens = cached`,
  `output_tokens = output + reasoning`, `reasoning_tokens`. Plus turns, tool calls
  and files modified.
- `bench_run.collect_gemini_usage`: the `result.stats` per step, summed, with
  `input_tokens = input`, `cache_read_tokens = cached`. `detected_model` comes from
  `init`, or from the `models{}` key with the most output.
- **Billing honesty:** codex/gemini runs on a subscription session record
  `billing_mode: "subscription"`, `cost_usd: null`, `cost_source:
  "subscription_quota"`. They never record 0. claude records its own
  `billing_mode` from the credential kind (`oauth_token` → subscription,
  `api_key` → api).
- `ADR-0075` holds: a missing cost is withheld from deltas, not compared.

### F. Gemini adapter and mask parity
- `adapters/registry.toml`: a new tool `gemini` (`native_skills_dir =
  ".gemini/skills"`, symlink, `adapter_file = "GEMINI.md"`, template
  `gemini-cli.md.j2`). `make sync-skills` generates `GEMINI.md` + `.gemini/skills/`.
- `executors`: add `.gemini` to `RUNNER_GOVERNANCE_DIRS` and `.gemini/skills` to
  the skill mirrors. `GEMINI.md` is already in `RUNNER_GOVERNANCE_FILES`.
- **Per-harness catalog in Docker.** Today the runner masks every mirror except
  `.claude/skills`, so codex and gemini would see **no** lab skills under
  `lab_root`. `runner_skill_masks(harness)` keeps the one mirror that harness
  reads: claude/opencode → `.claude/skills`, codex → `.codex/skills`, gemini →
  `.gemini/skills`. It also masks `.agents/skills` for codex and gemini (both read
  it, so leaving it would load the catalog twice).
- `inject_harness` / `add_skills_to_native` / `stage_skill_catalog` already take a
  `tool_id`. The new `gemini` tool makes `injected` project `GEMINI.md` +
  `.gemini/skills`.
- `governance.HARNESS_RULES_FILE["gemini"] = "GEMINI.md"`; provenance
  `NATIVE_SKILL_DIRS` / `ADAPTER_FILES` gain the gemini paths.
- Hidden tests and graders: the existing `runner_grading_masks` apply to every
  harness unchanged (the container is shared), and a test asserts it for codex
  and gemini.
- `bare` stays docker-only. Masks cover `.codex`, `.agents`, `.gemini`,
  `AGENTS.md`, `GEMINI.md`.

### G. Judges unchanged
- `llm_transport.py` is not touched, and a regression test pins it: adding codex
  or gemini sessions never changes `judge_transport()`.

### H. Tests
Builders (exact argv, stdin), dispatch, parsers and translators (fixtures from the
verified schemas), usage math (cumulative codex totals, cache split), auth
missing/expired (exit 41 / `turn.failed` 401 → cause), preflight per lane, skill
masks per harness (tripwires vs the registry), env scrub, docker mounts (only the
run's provider directory), sandbox flag per lane, judge transport unchanged,
backward compatibility (claude and opencode argv byte-identical).

### I. Onboarding and UI
- `GET /api/harness/clis`: per CLI → `installed`, `version`, `lane` (host/docker),
  `session` (`connected` / `missing` / `unknown`), `billing_mode`. Never a token.
- Health: one row per CLI (codex, gemini, claude). RunDetail: harness, CLI
  version, sandbox mode, requested vs effective model, billing mode.
- Frontend: `HARNESS_OPTIONS` + `gemini`; `HOST_HARNESSES` becomes "native
  harnesses"; `HARNESS_NATIVE_MODELS` fallback; codex leaves
  `SUMMARY_ONLY_HARNESSES`; EN/ES strings.
- Docs: harness-adapters, runbook (renamed in place: claude/codex/gemini),
  onboarding Path C, ADR-0094.

## 5. Out of scope / follow-ups
- Codex/Gemini as **judges** (would change ADR-0043's transport order).
- Effort for codex (`model_reasoning_effort`) and Gemini thinking budgets.
- A visible quota meter (codex `RateLimitSnapshot` is internal; not in `--json`).
- Experiment wizard pages that hard-code `claude`/`opencode` (CompareModels,
  HarnessByModel) — they keep working, and they offer gemini once the shared
  `HARNESS_OPTIONS` does.

## 6. Security limits (stated, not solved)
- A session directory mounted rw into a container is readable by the agent in
  that container. The agent runs as root there and can `cat` its own provider's
  credential. Containment: only that provider's directory is mounted, only for
  that run. No other provider's, no `~/.codex`, no `.env` keys for the other
  providers. Do not run untrusted repositories with a personal subscription.
- Codex in Docker runs without its own sandbox (container-only isolation).
- Gemini `yolo` and Codex bypass are never used on the host lane.
