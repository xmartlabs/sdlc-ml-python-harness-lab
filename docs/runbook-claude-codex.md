# Runbook — testing with the claude, codex and gemini harnesses

How to run benchmark cases against the **claude** (Claude Code), **codex**
(OpenAI Codex CLI) and **gemini** (Gemini CLI) harnesses. All three drive their
own official CLI with native auth instead of a gateway model, and all three run
in the normal Docker stack (`make app-up`) or in host mode (`make app-host`):

- **claude** reads a credential from `.env` (`CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token`, or `ANTHROPIC_API_KEY`).
- **codex** uses **your ChatGPT plan's login**, kept in the lab's session store,
  never in `~/.codex` and never as an API key. Enable it from **System → Agent
  CLIs** (no terminal needed). **gemini** no longer serves personal Google
  plans (see [below](#gemini-cli-and-personal-google-plans))
  ([ADR-0094](adr/0094-codex-and-gemini-run-on-their-own-subscription-login.md)).

Design details live in [harness-adapters.md](harness-adapters.md); this is the
step-by-step.

## 0. Prerequisites (once)

- **claude in Docker:** a credential in `.env` — `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token` (uses your subscription) or `ANTHROPIC_API_KEY`. Walkthrough:
  [onboarding.md, Path B](onboarding.md#path-b--claude-code-with-your-own-account).
  Rebuild the runner image once so it has the CLI (`make app-up` does).
- **codex / gemini:** sign in once — see
  [codex and gemini on your subscription](#codex-and-gemini-on-your-subscription).
  Docker needs nothing else (the CLIs are pinned in the runner image); host mode
  needs the CLI on the PATH of the shell you start `make app-host` from.
- **claude in host mode:** the CLI installed and logged in on your machine.
  Verify with `command -v claude codex gemini`.

## 1. Start the lab

```bash
make app-up      # every harness in Docker: claude, codex, gemini, opencode, SWE-bench
make app-host    # the same harnesses against your host CLIs (no bare/injected masks)
```

`make app-host` brings up redis + the Celery worker + the API + the frontend on the
host with `HARNESS_LAB_EXECUTION_BACKEND=subprocess`; Ctrl-C stops everything. You
run one mode at a time — pick by the harness you're testing. Open
<http://localhost:5173>.

## 2. Create a run

**Evaluate → New evaluation** for a control/treatment comparison, or **Advanced →
Runs → New run** for a single run:

1. **Case** — e.g. `clinic-slots-t1` (pure Python, graded by the lab's own tests —
   a good clean probe) or `todo-web-v1` (a page graded in a real browser).
2. **Harness** — `claude`, `codex` or `gemini`.
3. **Executor model** — the dropdown shows that CLI's **native models**:
   - claude → Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.2, then the
     previous generation; a model your gateway does not serve is marked
   - codex → the ids in the pinned CLI's own catalog (`codex debug models
     --bundled`, codex-cli 0.171.0): GPT-6.2-Sol, GPT-6.2-Astra, GPT-6.1 Terra,
     then the previous generation. Leave it empty for the CLI's default.
   - gemini → Auto, Gemini 2.5 Pro, Gemini 3.5 Flash, Gemini 3.1 Flash-Lite
     (`gemini --list-models`, gemini-cli 0.70.0).

   The catalog proves the CLI knows an id, not that your plan grants it; a
   refused id fails the run with the CLI's own message. The selected id is passed
   straight to the CLI (`claude --model …` / `codex -m …` / `gemini -m …`).
   Planner model is unused for these harnesses.
4. **Prompt variant** (e.g. `sdlc`), **Max steps**, then **Create run**.

The run streams into its detail page. Under the hood:

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --model claude-sonnet-5 "<prompt>"
codex  exec --json --skip-git-repo-check --sandbox workspace-write -m gpt-6.2-sol -      # host; prompt on stdin
codex  exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -   # Docker
gemini --output-format stream-json --approval-mode yolo --sandbox -m gemini-2.5-pro -p ""  # host; prompt on stdin
gemini --output-format stream-json --approval-mode yolo -p ""                            # Docker
```

with `cwd` at the repo root; the prompt tells the agent to build in
`workspace/<run_id>/`.

## 3. Score + validate

- Every web run leaves with a score ([ADR-0038](adr/0038-every-run-leaves-with-a-score.md)):
  a case with validation commands scores itself, a case with a `scoring_rubric`
  (`tetris-v1` has one) is LLM-judged, and only a rubric-less, non-objective case
  stops in `awaiting_manual_score`. Any score can be overwritten by hand on the
  run detail (0–10 + interventions).
- **Objective validation** (the smoke / test floor) runs automatically and shows
  under *Validation & score*; use **Re-validate** to re-run it. On macOS the GNU
  `timeout` wrapper is handled automatically (`gtimeout` or dropped).
- **LLM audit** (optional) — click **Run audit** for a qualitative diagnostic
  (skill/SDLC/efficiency/failure) written by a strong model against the logs. It
  uses the gateway, or the Claude Code CLI when no gateway is configured
  ([ADR-0043](adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)).

## 4. Compare

**Compare** page → pick the runs (same case, different harness/model) to see
objective result, score, tokens, steps side by side; **Benchmarks** has the
leaderboard. For a comparison you can trust, build it as an experiment instead —
**Evaluate → Harness x model** or **New evaluation** with the **Swap the harness**
factor ([run and compare](journey-run-and-compare.md)).

## Codex and Gemini on your subscription

Both CLIs run with the login the CLI itself performs; the lab never reads a
token's value and never calls a provider API with one. Each provider gets its
own directory in the **session store** — `HARNESS_LAB_AUTH_DIR`, default
`~/.harness-lab/auth`, outside the repository and outside `data/runs`:

```
~/.harness-lab/auth/codex/            CODEX_HOME: auth.json + the lab's config.toml
~/.harness-lab/auth/gemini/.gemini/   oauth_creds.json + settings.json
```

**Sign in once, from the UI.** Open **System → Agent CLIs**, press **Enable**
on Codex, open the link it shows, sign in with your ChatGPT account and enter
the one-time code. The worker runs the CLI's own `codex login --device-auth` in
a runner container, so nothing has to be installed on your machine. The card
turns green and runs **Test connection**: one real request, with exactly the
credential and environment a run gets, reporting the model that answered or the
provider's own error. *Disconnect* forgets the login (never `~/.codex`).

**The code's lifetime.** The code lives 15 minutes, set by OpenAI. The lab
cannot extend it. The card counts it down in minutes and seconds, measured on the
API server's clock, so a skewed browser clock does not shift it. The countdown
starts a moment before the CLI asks for the code, so it ends early rather than
late. **Generate new code** is always there while a code is showing: it
invalidates that code, stops its CLI and asks for a fresh one. Only one login
runs at a time. When a login does not complete, the card says why and offers a
new code:

| Card says | Meaning | What to do |
| --- | --- | --- |
| *The code expired* | The 15 minutes ran out before approval | Generate a new code and enter it promptly |
| *OpenAI refused the authorization* | Denied, wrong account, or device code login disabled for the workspace | Check the account and workspace settings, then use a new code |
| *The lab ran out of time* | No worker picked the login up, or the CLI printed no code | Check the interactive worker on **Health** |
| *The runner could not reach OpenAI* | Network or TLS error from the runner container | Check the runner's network |

The worker waits 5 minutes past the code's lifetime, so an approval made in the
last seconds can still complete. That extra time is never shown as time left on
the code. A login whose worker died mid-wait (a restart, a laptop sleep) reads
*expired* once its code's time has passed. It never stays "waiting" with a dead
code.

The terminal equivalent, when you prefer it (needs the CLI on your host):

```bash
make auth-codex     # codex login --device-auth: open the URL, enter the code
make auth-status    # connected / missing per provider (never prints a token)
```

A credential file is not proof that the provider serves your account: always
run *Test connection* once.

Why not reuse `~/.codex` / `~/.gemini`: Codex loads *user* skills from
`$CODEX_HOME/skills` and `~/.agents/skills`, and `~/.codex` usually holds your
personal skills, memories and an `AGENTS.md`. A run pointed at it would carry
your own harness into every arm — `bare` included. A run sees only the lab
session directory, an empty `HOME`, and none of the `.env` keys that would
switch the CLI to per-token billing (`OPENAI_API_KEY`, `CODEX_API_KEY`,
`GEMINI_API_KEY`, `GOOGLE_API_KEY`, Vertex, …).

**What a run records.** `metadata.json` carries `harness_cli_version`,
`harness_sandbox` (`workspace-write` / `seatbelt` on the host, `container` in
Docker) and `billing_mode: subscription`. `usage.json` has the tokens the CLI
reported (codex: the cumulative thread total of each step; gemini: the per-model
stats) and **`cost_usd: null` with `cost_source: subscription_quota`** — a plan's
quota has no per-token price, so the cost is unknown, never $0. Comparisons that
need a cost figure withhold it (ADR-0075).

**Plan limits.** Quotas are per plan (one agent step is many requests). An exhausted plan fails the run with the provider's own
message (`usage_limit_reached`, `RESOURCE_EXHAUSTED`, 429), recorded as the
run's cause.

**Expired login.** The CLI refreshes its token inside the run's private copy of
the session, and the worker writes the refreshed credential back to the store
when the run ends. If the refresh is refused, the run fails at its first request —
codex with `turn.failed … 401`, gemini with exit 41 — and the cause names the
`make auth-*` command to run again.

## Gemini CLI and personal Google plans

**Google stopped serving Gemini CLI for Google AI Pro, Ultra and free accounts
on 2026-06-18** ([Google Developers Blog, 2026-05-19](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli);
[deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)).
The login still succeeds and the CLI still lists models, but every request
fails on Google's side:

- `auto` and preview models: *"This client is no longer supported for Gemini
  Code Assist for individuals … migrate to the Antigravity suite"*;
- `gemini-2.5-pro` and the default: `429 Resource has been exhausted`.

Both were reproduced on 2026-11-20 with a Google AI Pro account. The lab
classifies such a run as "measured the connection, not the subject", and
System → Agent CLIs lists Gemini as not available. The harness stays for
organizations with a Code Assist Standard/Enterprise license.

The official replacement is **Antigravity CLI** (`agy`). The lab runs it as
the `antigravity_cli` harness on a **Gemini API key**, the credential Google
documents for headless and third-party use. It never uses a personal Antigravity
sign-in. See the next section.

## Antigravity CLI on your Google plan

`antigravity_cli` drives Google's Antigravity CLI (`agy` 1.5.14, pinned in the
runner image) headless, under the same cases, containers, conditions, scoring
and comparison as every other harness. It runs on one of two credentials:

- **Your Google sign-in** (Google AI Pro/Ultra), on the plan's Antigravity
  quota like the IDE ([ADR-0097](adr/0097-antigravity-cli-on-the-operators-google-sign-in.md)).
  Used whenever it is connected.
- **A Gemini API key**, billed per token ([ADR-0096](adr/0096-antigravity-cli-runs-on-a-gemini-api-key.md)).
  The fallback (below).

**Before you sign in.** Google's Antigravity FAQ says headless mode (`agy -p`)
"is intended for direct scripting and CI workflows". It also says "wrapping
`agy` inside third-party coding agents or applications, or automating account
rotation, is not supported under consumer plans", and its Terms allow account
suspension for third-party access. The lab runs `agy -p` itself, with the
sign-in `agy` itself performs, on one account and with no rotation. Google does
not say whether that counts. Using your account for it is your decision.

The lab never reads your macOS keychain, your own `~/.gemini`, or the IDE's
session.

### Sign in with Google (no terminal)

1. Rebuild the runner image once so it has `agy`: **Health → Build runner
   image** (or `docker compose build runner`). Then restart the API and the
   workers (`docker compose restart api worker worker-interactive`).
2. **System → Agent CLIs → Antigravity CLI → Sign in with Google.** The worker
   starts `agy`'s own sign-in in a runner container. A few seconds later the
   card shows **Open Google sign-in**.
3. Open it, sign in with the account that has your plan, and allow access.
   Google's page (`antigravity.google/oauth-callback`) then shows an
   **authorization code**.
4. Paste the code (or the whole page address) into the card and press
   **Connect**. The card says "Signed in" and sends one test request on your
   plan.

   If Google refuses the code, the card shows `invalid_grant`: each code works
   once, for a few minutes. Press **Start over**. The sign-in window closes
   after 15 minutes.
5. **Refresh models** lists the models your plan offers (`agy models`, signed
   in).

What is stored: only the files `agy` wrote for the sign-in, under
`~/.harness-lab/auth/antigravity_cli/account/` (0700/0600). Nothing goes in
`.env` or the database.

- Each run gets a private copy as its home and no API key. When it ends, a
  refreshed token returns to the store and everything else is deleted.
- Other containers never see another run's copy.
- **Sign out** deletes only the lab's copy; your account and the IDE are
  untouched.

Runs on the sign-in record `billing_mode: subscription` and
`cost_source: subscription_quota`, with `cost_usd` empty, like codex.

## Antigravity CLI on a Gemini API key

The fallback when no Google sign-in is connected, or for an organization's key
([ADR-0096](adr/0096-antigravity-cli-runs-on-a-gemini-api-key.md)). With a
key, `agy` calls the Gemini API directly and never opens an account session.
Note that **Google AI Pro adds no API quota**: its higher limits apply only in
the AI Studio web app. A free AI Studio key serves only the Flash and Hydrogen
models, and Google uses its prompts to improve its products.

### Set it up from the UI (no terminal)

1. Create a **dedicated** key in [Google AI Studio](https://aistudio.google.com/apikey)
   (or use a Gemini Enterprise key). Restrict it to the Generative Language API
   and set a budget cap on its project: commands the agent runs can read it
   (see *Security* below).
2. Rebuild the runner image once so it has `agy`: **Health → Build runner
   image** (or `docker compose build runner`). The Antigravity CLI card shows
   `v1.5.14` when it is there.
3. **System → Agent CLIs → Antigravity CLI → Use a Gemini API key instead →
   Configure API key.** Paste the key
   and save. The worker writes it to
   `~/.harness-lab/auth/antigravity_cli/gemini_api_key` (0600). It is never
   written to `.env` or the database, and never shown again; the card shows
   only its last four characters. The card then runs a test by itself.
4. **Test connection** sends one real request (billed to the key, a few
   hundred tokens on `gemini-3.8-flash-low` by default; you can pick another
   model). It shows the answer, the slug you asked for, the Gemini API model it
   calls, and the time. On failure it says which kind and what to do:

   | Card says | Cause | Fix |
   |---|---|---|
   | API key rejected | `API_KEY_INVALID`, leaked key, API not enabled (400/401/403) | Replace the key; enable the Generative Language API on its project |
   | Quota or billing | `RESOURCE_EXHAUSTED` (429), after agy's own ~2 min of retries | Check the project's billing and rate limits in AI Studio; retry later |
   | Model not available | unknown slug, or `NOT_FOUND` for this key | Pick another model; *Refresh models* |
   | Network | the runner cannot reach `generativelanguage.googleapis.com` | Check the runner containers' network/proxy |
   | Timeout / Server | `DEADLINE_EXCEEDED` / 5xx | Retry |
   | Not installed | the runner image has no `agy` | Rebuild the runner image (step 2) |

5. **Refresh models** lists the slugs this `agy` offers for the Gemini API
   (`agy models`). The command does not ask the API, so only a request proves
   your key may use a slug.
6. **Remove credentials** deletes the key, the last test and the model list.

### Run a case and compare it with Codex

1. **New evaluation** (or the guided flow): pick the case, harness
   **antigravity_cli**, and a model, for example `gemini-3.8-flash-medium` or
   `gemini-4-neon-high`. A model is required: `agy`'s stream never names the
   model that answered, so the run is filed under the slug you chose.
2. Run the same case with **codex** (and/or claude/opencode) with the same
   prompt variant, harness condition and repetitions.
3. **Compare** both runs. Score, validation, tokens, time, tool calls, files
   read and edited, and skills consulted come from the same readers. Cost:
   - antigravity_cli: `cost_usd` = its tokens × Google's paid-tier list price
     for the API model, at the rate in effect on the run's date (table read
     2026-11-22; Carbon/Argon change on 2026-12-01, the Flash models on
     2027-01-01). The whole table stops answering after 2027-05-22 until it
     is read again. It is labelled "estimated at the Gemini API list price"
     (`cost_source: api_list_price_estimate`).
   - codex: `cost_usd` is null (subscription quota), with an API-price
     estimate beside it.

### What a run is given

- The command is `agy --settings '{"modelProvider":"gemini","permissions":{"allow":["command(*)","read_file(*)","write_file(*)","read_url(*)"]}}' --mode accept-edits --disable-slash-commands --model <slug> --input-format stream-json --output-format stream-json`.
  The prompt goes in as one stdin message.
- The tool grants are explicit, not `--dangerously-skip-permissions`. In
  headless mode an action `agy` would have to ask about is denied, and **the
  turn ends with SUCCESS and no answer**. The allow list therefore covers what
  a coding agent does in the container (the same reach claude/codex have
  there). MCP tools and browser execution are not granted.
- The environment holds `GEMINI_API_KEY` from the lab store and no other
  provider's credential. These are masked inside `/app`:
  - `.env`, `.claude/settings.local.json` and the lab database;
  - the `.env` backups and the native-connect state;
  - `backups/`;
  - other runs' provider sessions.

  `HOME` is private to the container, so `agy`'s conversations, logs and
  databases die with it. The self-updater is off.
- Skills: `agy` reads `.agents/skills` (the Antigravity mirror) and nothing
  else, so that mirror is the one the runner keeps for this harness. Rules:
  `AGENTS.md`, `GEMINI.md` and `.agents/rules/GEMINI.md`.
- **Docker only.** A host-mode run is refused: `agy`'s `--sandbox` cannot
  start in a container, so the container is the sandbox, and on the host
  nothing would contain the agent.

### Security: the key is readable by code the agent runs

`agy` passes its environment to every command it runs, and has no setting to
scrub a variable. The lab cannot hide the key from the agent. What the lab does:

- **Third-party code is refused by default:** SWE-bench / SWE Pro instances
  and external target repositories. Lab cases and your own ad hoc folders run.
  To allow third-party code, set `HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1` on the API
  and the worker, and use a throwaway key.
- Every line the run streams has the key's exact value replaced with
  `***REDACTED***`, in the runner and again in the worker. After the run,
  whatever way it ends, the worker also scrubs the run directory, the
  workspace and every file in the lab tree modified during the run. A hit is
  logged and recorded as `secret_exposed` in `metadata.json`. **Rotate the
  key** if you see it. Redaction matches the exact value only, so an encoded,
  split or compressed copy would pass. The container has network access, so
  an agent could also send the key out.
- Saving a key never sends it through the task queue. The API writes it to a
  0600 drop file that the worker collects and deletes (a drop expires after
  15 minutes), so no worker log can contain it.
- The key is the only secret in that container, and it never reaches another
  harness's container.

### Gemini options compared

| Option | Agent under test | Credential | Billing in the lab | When to use |
|---|---|---|---|---|
| `gemini` harness (legacy Gemini CLI) | Gemini CLI 0.70 | Code Assist Standard/Enterprise sign-in | `subscription_quota` (cost null) | Organizations with a Code Assist license; personal plans no longer work |
| `antigravity_cli` harness | Antigravity CLI (`agy`) | Gemini API key (AI Studio or Enterprise), in the lab store | per-token list-price estimate in `cost_usd` | Benchmarking Google's own agent |
| `opencode` + a Gemini model through LiteLLM | opencode | the gateway's Gemini key | whatever the gateway reports | Comparing *models* under one agent |
| Antigravity by hand (`antigravity` flow) | Antigravity IDE/CLI, driven by a person | your own sign-in, on your machine | none (manual) | Improving the `.agents` rules; never automated |

### Not verified yet

Everything above was checked against the `agy` 1.5.14 binary with no real
credential: flags, failure shapes (invalid key against the real API; 403, 404,
429, 500, network and timeout against a local mock), the event schema, token
usage, the slug→model map, skill discovery and the env leak. The test fixtures
are those captures (`tests/harness_lab_app/fixtures/agy/`). Still pending with a
real key: one authenticated request end to end, which slugs a given key may
use, and real usage numbers.

## Gotchas / limits

- **Sandboxes (Terminal/Desktop) need Docker** — they don't work in host mode.
  You don't need them: the produced code is on disk at
  `data/runs/<run_id>/workspace/` — open it, or run it directly
  (`python data/runs/<run_id>/workspace/main.py`).
- **Attribution is first-class for all three.** claude streams
  `stream-json`, codex `exec --json`, gemini `stream-json`; the codex/gemini
  streams are archived raw (`steps/step-N.raw.jsonl`) and translated into the
  claude shape (`step-N.events.jsonl`), so files read/edited, the bash
  histogram, governance coverage and skill invocations read the same way. A
  codex skill is "invoked" when the agent opens its `SKILL.md` (codex has no
  Skill tool); a gemini skill when it calls `activate_skill`.
- **Model ids** are the systematic-lab knob: if a CLI rejects an id (version
  drift), edit `HARNESS_NATIVE_MODELS` in `frontend/src/harnesses.ts`.
- **Preflight guard**: a `codex`/`gemini` run is refused without a subscription
  login (System → Agent CLIs, or `make auth-codex`), with an API-key session (it
  would bill per token), with a `gateway/…` model id, or when the runner image's
  CLI is older than the verified version; a `claude` run in Docker mode without a
  credential in `.env` is rejected with the variable names to set.

## Quick reference

| I want to test… | Mode | Harness | Model source |
|---|---|---|---|
| Claude Code (your account) | `make app-up` (token in `.env`) or `make app-host` | `claude` | native menu (the Claude 5.5 family and Fable 5.2) |
| Codex (your ChatGPT plan) | `make auth-codex`, then `make app-up` or `make app-host` | `codex` | native menu (codex-cli catalog) |
| Antigravity CLI (your Google plan) | `make app-up`, then System → Agent CLIs → Antigravity CLI → Sign in with Google, paste the code | `antigravity_cli` | native menu (`agy models`, your plan) |
| Antigravity CLI (Gemini API key) | System → Agent CLIs → Antigravity CLI → Use a Gemini API key instead → Configure API key | `antigravity_cli` | native menu (`agy models` slugs) |
| Gemini CLI | Code Assist Standard/Enterprise license only (personal plans retired 2026-06-18) | `gemini` | native menu (gemini-cli list) |
| opencode + gateway model | `make app-up` | `opencode` | live LiteLLM list |
| SWE-bench objective | `make app-up` | `opencode` | live LiteLLM list |
