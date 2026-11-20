# Harness adapters — opencode, codex, claude, gemini

> **Just want to run tests with claude/codex?** See the step-by-step
> [runbook-claude-codex.md](runbook-claude-codex.md). This page is the design
> reference behind it.

The lab treats the **harness** (the agent CLI that drives a run) as an
independent variable. Four are wired today:

| Harness | CLI | Runs in | Auth | Model selection |
|---|---|---|---|---|
| `opencode` | `opencode run` | runner **container** | LiteLLM gateway (`gateway/…`) | live gateway list |
| `codex` | `codex exec --json` (prompt on stdin) | **container** or **host** | ChatGPT plan login, lab session store (`make auth-codex`) | native menu → `-m` |
| `claude` | `claude -p` | **container** (Docker) or **host** | native (Anthropic) — env token in Docker, your login on host | native menu → `--model` |
| `gemini` | `gemini -p "" --output-format stream-json` (prompt on stdin) | **container** or **host** | Code Assist Standard/Enterprise login only — personal Google plans retired 2026-06-18 ([runbook](runbook-claude-codex.md#gemini-cli-and-personal-google-plans)) | native menu → `-m` |

(`antigravity` remains a separate human-driven flow, not executed by the runner.)

The command builders live in `scripts/bench_run.py`
(`build_opencode_cmd` / `build_codex_cmd` / `build_claude_cmd` /
`build_gemini_cmd`, dispatched by `build_harness_cmd`). codex and gemini are
described in [their own section](#codex-and-gemini-subscription-clis-adr-0094). The workspace path (`workspace/<run_id>/`) is carried in
the **prompt**, so all three run with `cwd=REPO_ROOT` and produce the same
artifact — only the CLI differs. Pick the harness in the New evaluation form (or
the single-run form under Advanced → Runs).

## Run `claude` in Docker (whole lab in `make app-up`)

You don't have to use host mode for claude — the Claude Code CLI is baked into the
runner image, so it runs in the same Docker lane as opencode/SWE-bench. It just
needs credentials, supplied once via `.env` (which flows into the runner
container):

1. **Subscription (recommended):** on your host, run `claude setup-token` (while
   logged in), and paste the long-lived token into `.env`:
   ```
   CLAUDE_CODE_OAUTH_TOKEN=<token from setup-token>
   ```
   This uses your Claude plan. Or use an API key instead: `ANTHROPIC_API_KEY=sk-ant-…`.
2. Rebuild the runner image so it has the CLI: `docker compose build runner`
   (or just `make app-up`, which builds it).
3. Create a claude run as usual — no host worker needed.

**The CLI version is pinned** (`ARG CLAUDE_CODE_VERSION` in `Dockerfile.runner`) and
recorded as the image label `claude-code.version`. Newer models need a newer CLI —
the 5.5 family and Fable 5.1 need 2.1.280 or later, Fable 5.2 needs 2.1.321
(`MIN_CLAUDE_CLI_VERSION` in `native_models.py`) — and an unpinned install stays on
whatever Docker cached. Bump the ARG and rebuild when a model needs it. **Health** shows the image's version and
warns when it is too old for a current model, and preflight refuses such a run
instead of letting it die on the API's `claude_code_version_too_old`.

### Adding a new Claude model

A new model is reachable only when all three of these know it:

1. **The gateway.** Add a LiteLLM card for the bare id (for example
   `claude-fable-5-2`) and deploy it. The forms mark a catalog model with no card
   "not on your gateway", and preflight refuses it.
2. **The CLI in the runner image.** A CLI older than the model refuses the id
   itself, before any request: 2.1.309 answered `[claude-code:unrecognized_model]`
   for Fable 5.2. Find the release that adds the model in the Claude Code
   changelog, bump `ARG CLAUDE_CODE_VERSION` in `Dockerfile.runner`, and rebuild
   `docker compose build runner`, then `docker compose build runner-godot` for the
   3D cases (its image is built on top of the runner). A rebuild does not touch a
   run in flight: its container keeps the image it started with.
3. **The lab's catalog.** Add the model to `NATIVE_MODELS` in
   `src/harness_lab_app/native_models.py` with its floor in
   `MIN_CLAUDE_CLI_VERSION`, and mirror it in `HARNESS_NATIVE_MODELS`
   (`frontend/src/harnesses.ts`), the offline fallback. When it replaces a model
   in the same line, move the old one to `generation: "previous"` so it stays
   selectable for comparisons with the archive. The API reads the catalog at
   start, so restart it (`docker compose restart api`) between runs.

`test_the_runner_pins_a_claude_cli_new_enough_for_every_current_model` fails
when the catalog lists a model the pinned CLI is too old for.

### Reasoning effort (`--effort`)

A claude run can be given a reasoning effort: `low`, `medium`, `high`, `xhigh` or
`max` ([ADR-0081](adr/0081-reasoning-effort-is-part-of-the-arm.md)). Pick it in the
**Reasoning effort** select that appears under the executor model when the harness
is `claude` and the model accepts effort. Leave it on **Default** to keep the CLI's
own choice. That is what every run before the selector used. Use `xhigh` / `max` for
longer, more thorough tests, and `low` for cheap smoke passes.

- The level reaches the CLI as `claude --effort <level>`. On the command line, use
  `scripts/bench_run.py --harness claude --executor-effort high` or
  `make bench-run ... EXECUTOR_EFFORT=high`.
- Which levels a model accepts comes from `effort_levels` in `native_models.py`.
  Haiku 4.5 has none. Preflight refuses an effort on a non-claude harness, on a
  gateway-routed model, or at a level the model does not accept.
- Effort is part of the arm. A non-default level changes `condition_hash`, so the
  same model at two levels makes two arms. To compare levels, vary `executor_effort`
  between the conditions of a `subject_validation` or `discovery` experiment. A
  `harness_evaluation` holds it fixed, like the model, so it cannot confound the
  harness. A batch always runs at the default, so the batch form hides the select.
- To compare levels from the UI, use **Subject validation**: each arm has its own
  effort select, and an arm at a level is labelled `<harness>/<model>@<level>`.
- `RESUME=1` continues at the level the run's `metadata.json` recorded. Passing a
  different `EXECUTOR_EFFORT` on a resume is refused.
- A launched experiment locks its effort with the rest of its configuration.
  Relaunching it adds repetitions at the same level, and the locked panel shows it.
- `metadata.json` records the effort that was **requested**. The CLI does not report
  the level it applied.

In Docker every claude run reaches Anthropic through the gateway
(`ANTHROPIC_BASE_URL` in `.claude/settings.local.json`), so the gateway must have a
card for the model too; preflight checks that as well.

### Claude observability + A/B hygiene (stream-json)

`claude` runs execute with `--output-format stream-json --verbose`: the raw
events (one JSON per assistant message / tool call / result) are teed to
`steps/step-N.events.jsonl`, while the step log receives a human-readable
rendering (`→ Read(path)`, `→ Bash(cmd)`, final stats line). Three consumers:

- **Attribution** (`attribution.build_attribution_from_events`): first-class
  tool calls — files read/edited, bash histogram, governance coverage, and
  *real* skill invocations (the `Skill` tool), not just SKILL.md reads.
- **usage.json** (`bench_run.collect_claude_usage`): tokens, cost, turns,
  duration and modified files summed from the per-step `result` events.

`opencode` runs get their usage from opencode's own SQLite store
(`$XDG_DATA_HOME/opencode/opencode.db`, default `~/.local/share/…`), read inside the
runner container before it exits. The join key is the session id: every session
opencode opens is announced in its `--print-logs` output as
`message=created id=ses_… parentID=…`, and `bench_run.opencode_session_ids` collects
them from the step logs. `pull_usage_opencode_db` then sums exactly those sessions plus
any sub-agent session whose `parent_id` points at one: tokens (input, output +
reasoning, cache read/write), turns (assistant messages), tool calls (`tool` parts) and
`model_minutes`. Title and directory are never used. The title is written by the model,
and the directory is `/app` for a full-harness run but `/app/workspace/<run>` for a
masked bare one (ADR-0049), so joining on either silently dropped whole arms
([ADR-0075](adr/0075-every-arm-is-measured-by-the-same-instrument.md)).

For every harness, `time_minutes` is the **step clock** (wall time of each harness
invocation, summed by `bench_run`), and `cost_usd` is `0` when every addressed model is
self-hosted and the harness is opencode. Both are set in `bench_run.assemble_usage`,
which the managed and the interactive paths share.
- **Audit**: step logs now show the tool timeline, so audits stop being
  narrative-only.

**Control-arm hygiene:** Claude Code auto-loads `CLAUDE.md` + `.claude/skills/`
from its cwd, so a "bare" run launched from the repo root silently inherits the
whole lab harness. For SWE cases with the `swe_default` variant, the claude
agent therefore runs with **cwd = workspace/<run_id>/**; the `swe_harness`
variant keeps the repo-root cwd so the surface is available. Prompts adjust
their path references accordingly.

**A different cwd is not isolation on its own.** The workspace lives *under* the
repo root, which is bind-mounted whole at `/app`. Two archived `native` runs show
what that means in practice: the agent got none of the lab's `.claude/` (no
subagents, no slash commands, no skills, no SessionStart hook) — but it did get
`/app/.mcp.json`, and Claude Code's memory lookup collects `CLAUDE.md` from every
ancestor directory. So the container masks the governance surface for every
condition that runs from the workspace (ADR-0049):

| `harness_source` | agent cwd | what the container masks |
|---|---|---|
| `lab_root` | the lab repo | nothing — the lab's harness *is* the treatment |
| `native` / `injected` | `workspace/<run>/` | `.claude`, `.agents`, `.opencode`, `.codex`, `.gemini`, `memory`, `docs/adr`, and the adapter files (`CLAUDE.md`, `AGENTS.md`, `OPENCODE.md`, `GEMINI.md`, …) plus `.mcp.json`. `.github/` stays readable: `bench_run.py` projects the injected harness from it |
| `bare` | `workspace/<run>/` (empty) | all of the above **and** `.github/` |

`opencode.json` is never masked — it is routing, not governance, and blanking it
would take the gateway provider block with it. The `swe_default` cwd hygiene
above predates this and is unchanged.

Two details the docker lane handles for you:

- **Root + skip-permissions:** the runner container runs as root, and Claude
  Code refuses `--dangerously-skip-permissions` as root unless `IS_SANDBOX=1`
  is set. `build_run_environment` injects it into every run container (the
  same escape hatch Anthropic's devcontainers use).
- **Project settings reach the run as environment, not as a file.** Claude Code
  reads `.claude/settings*.json` from its *project* directory, so a run whose cwd
  is the workspace never saw the lab's — and with it `ANTHROPIC_BASE_URL`, i.e.
  the LiteLLM gateway and its metrics headers. `build_run_environment` therefore
  merges the `env` block of
  `.claude/settings.json` and `.claude/settings.local.json` (local wins, and both
  win over `.env`) into every run container, so routing is identical whatever the
  harness condition. The file is still gitignored — copy it to any new deployment
  (the bench server) along with `.env`.

The preflight guard checks the credential is present before a Docker claude run,
with an actionable message if it's missing.

## codex and gemini: subscription CLIs (ADR-0094)

Both run the official CLI with the provider's own login and are pinned in the
runner image (`ARG CODEX_VERSION=0.171.0`, `ARG GEMINI_CLI_VERSION=0.70.0`,
labels `codex.version` / `gemini-cli.version`). Every flag, event field and
skill directory below was verified against those versions; preflight refuses an
older CLI (`native_models.MIN_NATIVE_CLI_VERSION`).

**Commands.**

| | host lane | Docker lane |
|---|---|---|
| codex | `codex exec --json --skip-git-repo-check --sandbox workspace-write [-m M] -` | `… --dangerously-bypass-approvals-and-sandbox [-m M] -` |
| gemini | `gemini --output-format stream-json --approval-mode yolo --sandbox [-m M] -p ""` | the same without `--sandbox` |

The prompt goes to **stdin** for both (argv has a size limit, stdin does not).
`codex exec` never asks for approval. Codex's `workspace-write` sandbox needs
bubblewrap, which cannot create a namespace inside the runner container (it
fails with "No permissions to create a new namespace", and the legacy Landlock
path panics), so in Docker the per-run container is the sandbox — the same
reasoning that lets claude use `--dangerously-skip-permissions` there. Gemini's
`yolo` is only used under its macOS Seatbelt profile on the host or inside the
container. The mode is recorded per run as `metadata.json: harness_sandbox`.

**Sessions.** `native_auth` keeps one directory per provider under
`HARNESS_LAB_AUTH_DIR` (default `~/.harness-lab/auth`). **A run never mounts the
store.** The worker copies only the allow-listed files (codex `auth.json` +
`config.toml`; gemini `.gemini/oauth_creds.json` + `.gemini/settings.json`) into
`data/runs/<run>/session/<provider>/` and binds that copy read-write at
`/run/harness-auth/<provider>`. When the container ends, a refreshed credential
is written back to the store, and only when it is still a well-formed login (a
run cannot plant an API key there). Then the copy is deleted. Anything else the
agent wrote in its CLI home is discarded with it: a gemini memory in
`GEMINI.md`, a codex `AGENTS.md` or skill. So nothing carries from one run, or
arm, to the next. The run also gets `CODEX_HOME` / `GEMINI_CLI_HOME` pointing at
the copy, `HOME=/tmp/agent-home` (empty, so no user-scope skills),
`GOOGLE_GENAI_USE_GCA=true` and `GEMINI_CLI_TRUST_WORKSPACE=true` for gemini, and
**none** of the `OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_API_KEY`, Vertex or Anthropic variables from `.env`.

The host lane cannot copy, because the CLI refreshes the store in place. It
prunes the store to the allow-list before every step and gives every step a
fresh temporary `HOME`. The worker mounts the store read-write (for the
copy-back) and the API read-only (for `native_auth.session_status`, which reads
a credential's *shape*, never its value).

**Events.** The raw CLI stream is archived per step as `step-N.raw.jsonl`
(stamped with `_received_at`) and translated by `harness_events` into the claude
shape in `step-N.events.jsonl`, which every existing reader (attribution,
trajectory, score evaluators, run progress, the Langfuse exporter) reads
unchanged. Tool names map onto the claude vocabulary: codex
`command_execution` → `Bash`, `file_change` → `Edit`/`Write`; gemini
`read_file` → `Read`, `replace` → `Edit`, `run_shell_command` → `Bash`,
`activate_skill` → `Skill`. A codex skill is invoked by reading its `SKILL.md`
(codex has no skill tool), which the trajectory already counts.

**Usage and cost.** codex reports the thread's *cumulative* usage on each
`turn.completed`, so the last one per step is taken. Codex's `input_tokens`
includes the cache, so the cached share moves to `cache_read_tokens`. Its
`reasoning_output_tokens` are already inside `output_tokens` (`protocol.rs`:
`blended_total = non_cached_input + output_tokens`), so they are recorded beside
it, never added. The translated `step-N.events.jsonl` is rewritten while the
step runs (at most every 5 s, on each completed item or tool result) and once
more at the end. Live progress therefore reads it, and a step the watchdog
kills keeps its usage and attribution.

**Condition hash.** `harness` enters the condition hash for codex and gemini
only. Both run their CLI's default model when none is chosen, so without it a
codex, a gemini and a claude run with an empty model could share an arm.
claude and opencode are left out, so every historical hash is unchanged. gemini's
`result.stats.input` is the uncached prompt. A subscription run records
`billing_mode: subscription`, `cost_usd: null`, `cost_source:
subscription_quota` — unknown, never $0.

**Skill catalog per harness.** In the runner every duplicate catalog is masked
except the one the harness reads (`executors.RUNNER_SKILLS_DIR_FOR_HARNESS`):
claude/opencode → `.claude/skills`, codex → `.codex/skills`, gemini →
`.gemini/skills`. `.agents/skills` is always masked: codex and gemini both read
it, and leaving it would load the catalog twice. Before this, codex's own
mirror was masked too, so a Docker codex run would have seen no lab skill.

## Model selection

The New-run executor menu is **harness-aware**: for `opencode` it's the live
gateway list; for `codex`/`claude`/`gemini` it swaps to that CLI's **native models**
(served by `GET /api/models/native`, from `native_models.py`) — the Claude 5.5
family and Fable 5.2 first, the previous generation after. The selected id is a bare
(non-`gateway/`) name, which
`bench_run.py` passes straight to the CLI's `--model` / `-m` (`_native_model`
ignores any `gateway/`-prefixed value and lets the CLI default apply).

Keep `NATIVE_MODELS` (and its offline mirror `HARNESS_NATIVE_MODELS`) current
as new CLI versions ship. The codex ids come from `codex debug models --bundled`
and the gemini ids from `gemini --list-models` on the pinned versions — both
offline, no login. They prove the CLI knows the id, not that a plan grants it;
leaving the model empty keeps the CLI's default.

## Running codex / claude on tetris

One command from the repo root brings up redis + worker + API + frontend on the
host (Ctrl-C stops all):

```bash
make app-host
```

Or run the pieces in separate terminals for debugging: `make app-worker-host`,
`make app-api-host`, then `cd frontend && VITE_API_TARGET=http://localhost:8009
npm run dev`.

Then open `http://localhost:5173/advanced/runs`, pick **Case** `clinic-slots-t1`,
set **Harness** to `codex` or `claude`, and Create run. The run streams into the
detail page like any other and scores itself: the case's validation commands are its
graded tests.

`opencode`, SWE-bench and Docker `claude` runs use the containerized `make app-up`
(they need the runner image). The two modes don't run at once — choose per what
you're testing.

## Verified vs. to-verify

- **Unit-verified:** the exact CLI invocations per lane, the dispatch,
  `--harness` plumbing, the preflight guards, the event translation and usage
  math, the env scrub, the per-harness skill masks and the session mount
  (`test_harness_adapters.py`, `test_native_harnesses.py`,
  `test_harness_events.py`).
- **Verified offline against the pinned CLIs:** flags (`--help`), the codex
  event schema (`exec_events.rs` at `rust-v0.171.0`), the gemini event schema
  (its bundle), skill discovery (`codex debug prompt-input`, `gemini skills
  list`), the unauthenticated failure of each CLI, and codex's sandbox failing
  inside the runner container.
- **Verified with a real login (2026-11-20):** Gemini CLI on a Google AI Pro
  account is refused by Google (personal plans retired 2026-06-18).
- **Verified end to end without a login:** the UI's *Enable* starts
  `codex login --device-auth` on the interactive worker and shows the device
  URL and code within seconds (System → Agent CLIs).
- **To verify with a codex login:** that *Test connection* answers, that a run
  completes a case headlessly, and that a token refresh inside the container
  writes back to the store.

### Connecting from the UI (System → Agent CLIs)

`native_connect` drives the CLI's own device login and a one-request connection
test from the web UI. Both need the docker socket, so they are Celery tasks
(`connect_native_cli`, `test_native_cli`, `disconnect_native_cli`) on the
**interactive** queue (a login waits for a person for up to fifteen minutes and
must not hold a run lane). They start the CLI through `DockerRunExecutor`, so
they get exactly a run's isolation: a private copy of this provider's session,
the scrubbed environment, an empty `HOME`, and the credential copied back into
the store only when it is a well-formed subscription login. Progress records
(state, the public device URL, the one-time code, the test's answer or error)
live in `data/app/native-connect/` and never hold a credential.
