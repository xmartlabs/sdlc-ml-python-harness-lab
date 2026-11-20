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

The official replacement is **Antigravity CLI** (`agy`, headless with
`agy -p … --output-format stream-json`). It is not wired into the lab yet: on
macOS it keeps its login in the Keychain, which a runner container cannot read,
and inside a Linux container it falls back to a file whose layout still has to
be confirmed with a real login.

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
| Gemini CLI | Code Assist Standard/Enterprise license only (personal plans retired 2026-06-18) | `gemini` | native menu (gemini-cli list) |
| opencode + gateway model | `make app-up` | `opencode` | live LiteLLM list |
| SWE-bench objective | `make app-up` | `opencode` | live LiteLLM list |
