# ADR-0094: Codex and Gemini run on their own subscription login

- **Status:** Accepted
- **Date:** 2026-11-20
- **Deciders:** Marcos Soto
- **Related:** [ADR-0043](0043-one-llm-transport-gateway-first-claude-cli-second.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0061](0061-a-claude-run-started-inside-a-clone-must-be-told-where-the-skills-are.md),
  [ADR-0073](0073-a-measurement-names-its-real-instrument-and-its-reach.md),
  [ADR-0075](0075-every-arm-is-measured-by-the-same-instrument.md),
  [ADR-0081](0081-reasoning-effort-is-part-of-the-arm.md),
  plan [`2026-11-codex-gemini-subscription-harnesses.md`](../plans/2026-11-codex-gemini-subscription-harnesses.md)

## Context

The lab compares harnesses, and a team that pays for ChatGPT and Google AI plans
wants to run cases through the official Codex CLI and Gemini CLI on those plans,
not through a per-token API. The state on `main`:

- **codex was broken.** The runner invoked `codex --quiet --full-auto`. The
  current CLI (codex-cli 0.171.0) refuses it: `unexpected argument
  '--full-auto' found`. Its only catalog model, `codex-mini-latest`, is not in
  that CLI's own model catalog.
- **codex was host-only**, so it could not run the `bare`, `native` or
  `injected` conditions, which need the runner's masks (ADR-0049).
- **codex had no event stream**, so attribution, skill invocation and usage read
  as zeros.
- **gemini did not exist** as a harness.

Checking against the installed CLIs and a real runner container (no login)
turned up four facts that shape the design:

1. Codex's `--sandbox workspace-write` needs bubblewrap, which cannot create a
   namespace in the runner container. Every command fails, and the legacy
   Landlock path panics.
2. Codex loads user-scope skills from `$CODEX_HOME/skills` and
   `$HOME/.agents/skills`. An operator's `~/.codex` holds personal skills,
   memories and an `AGENTS.md`.
3. Both CLIs fall back to per-token billing when an API key is in the
   environment: Codex reads `OPENAI_API_KEY`/`CODEX_API_KEY`, Gemini reads
   `GEMINI_API_KEY` unless `GOOGLE_GENAI_USE_GCA` is set.
4. The runner masked every skill mirror except `.claude/skills`, so a Docker
   codex run would have seen no lab skill, while codex and gemini both read
   `.agents/skills`.

## Decision

We will run codex and gemini as harnesses that use the provider's own login,
under the same cases, containers, conditions, scoring and provenance as claude.

1. **Official CLI, official login, never a token.** A per-provider session
   store (`HARNESS_LAB_AUTH_DIR`, default `~/.harness-lab/auth`) sits outside the
   repository. The official CLI fills it through `make auth-codex`
   (`codex login --device-auth`) and `make auth-gemini` (Sign in with Google,
   `NO_BROWSER`). The lab reads only the *shape* of the credential file, to say
   whether a session exists. It never reads, copies or forwards a token, and it
   never calls a provider API with one.
2. **A run sees a private copy of its own provider, and nothing else.** A run
   never mounts the store. The worker copies the allow-listed credential and
   config into `data/runs/<run>/session/<provider>` and binds that copy
   read-write. When the run ends, a refreshed credential goes back to the store
   (only a well-formed login), and the copy is deleted together with anything
   else the agent wrote there. That rules out a gemini memory, or a codex
   `AGENTS.md`, reaching another run or arm. The run gets
   `CODEX_HOME` / `GEMINI_CLI_HOME`, an empty `HOME`, and none of the `.env`
   variables that would switch billing or provider. The host lane, where the
   CLI refreshes the store in place, prunes the store to the allow-list before
   every step and uses a fresh temporary `HOME` per step.
3. **The sandbox follows the lane, and is recorded.** On the host, codex runs
   with `--sandbox workspace-write` and gemini with `--sandbox` (Seatbelt). In
   the runner, codex runs with `--dangerously-bypass-approvals-and-sandbox` and
   gemini with `yolo`, because the container is the sandbox. That is the
   reasoning already applied to claude. `metadata.json: harness_sandbox` names
   the mode.
4. **One event shape for every reader.** The raw stream is archived per step
   (`step-N.raw.jsonl`). `harness_events` translates it into the claude shape
   (`step-N.events.jsonl`) that attribution, trajectory, the score evaluators,
   run progress and the Langfuse exporter already read. A codex skill counts as
   invoked when the agent reads its `SKILL.md`, a gemini skill when the agent
   calls `activate_skill`.
5. **A subscription is not $0.** codex/gemini runs record `billing_mode:
   subscription`, `cost_usd: null` and `cost_source: subscription_quota`. claude
   records its own billing mode from its credential.
6. **The harness names a codex/gemini arm.** `harness` enters the condition
   hash for those two only. Without it, a codex, a gemini and a claude run with
   the CLI's default model could share an arm. claude and opencode stay out,
   so history keeps its hashes.
7. **One catalog per harness in the runner.** The runner keeps the mirror each
   harness reads (`.claude/skills` for claude and opencode, `.codex/skills`,
   `.gemini/skills`) and always masks `.agents/skills`. A new `gemini` tool in
   `adapters/registry.toml` generates `GEMINI.md` and `.gemini/skills`, so
   `injected` projects gemini's own surface. `.gemini` and `GEMINI.md` are in
   the governance masks.
8. **Models only from the pinned CLI.** The catalog lists the ids the pinned
   binary itself reports (`codex debug models --bundled`, `gemini
   --list-models`), and the default is the CLI's own choice. A `gateway/…` id is
   refused, not silently dropped. Effort stays a claude setting (ADR-0081).
9. **Versions pinned and enforced.** `Dockerfile.runner` pins codex 0.171.0 and
   gemini-cli 0.70.0 and labels them. Preflight refuses an older or missing CLI,
   a missing login, and an API-key session.
10. **Judges are not touched.** `llm_transport` keeps its order (ADR-0043). A
   codex or gemini login is an executor credential, and a test pins that it
   never changes `available_transport()`.

## Consequences

- codex and gemini run in every condition, `bare` included. Their runs carry
  first-class attribution, skill invocation and token usage.
- A codex `lab_root` run in Docker now sees the lab's catalog. No codex Docker
  run existed before, so no history is re-binned.
- Cost comparisons that include a subscription arm withhold the cost delta. The
  outcome comparison still shows (ADR-0075).
- The executor passes `harness` to the docker executor for every run. For
  claude and opencode the masks and environment are unchanged, except for the
  new `.gemini/skills` mirror mask and the `HARNESS_LAB_IN_RUNNER=1` marker.
- **Security limits, accepted.** The agent runs as root in its container and
  can read its own provider's credential copy there, including a long-lived
  refresh token. If the agent prints it, it lands in the raw stream,
  `events.jsonl` and any trace export; token values are not redacted yet
  (follow-up). Containment: a run gets only a copy of its own provider's
  credential, for that run only. Do not run untrusted repositories on a
  personal subscription. In Docker, codex has no sandbox of its own: the
  container is the whole isolation.
- **Host lane is weaker.** Nothing is masked on the host, so codex sees
  `.codex/skills` + `.agents/skills` and gemini `.gemini/skills` +
  `.agents/skills` (the catalog twice), and `bare` is refused there as before.
  Use Docker for comparisons.
- **Fingerprint change.** Adding `.gemini/skills` and `GEMINI.md` to the
  workspace fingerprint changes the hash of a past `native`/`injected` run whose
  target ships a `GEMINI.md`. That is correct (it was part of the surface), and
  recorded here.
- **Not verified without a human login:** a full authenticated run, a token
  refresh written back from the container, and which model ids a given plan
  grants.

## Alternatives considered

- **Route codex/gemini through opencode as model providers.** This needs API
  keys or an OAuth proxy (Google forbids reusing Gemini CLI's OAuth in other
  tools), and it changes the harness under test.
- **Mount `~/.codex` / `~/.gemini`.** It carries the operator's personal skills,
  memories and instructions into every arm, and on macOS Gemini's credentials
  may sit in the keychain.
- **Keep `workspace-write` in Docker.** It does not run there. Running the
  container privileged so that bubblewrap works would weaken the isolation the
  other harnesses rely on.
- **Separate parsers per harness in every reader.** That is four parsers to keep
  aligned across six readers. A single translation keeps one reader per concept
  and keeps the raw stream as provenance.
- **Record subscription cost as 0.** Rejected for the same reason as ADR-0073:
  a request is not a measurement, and an unknown figure is not a free one.

## Amendment (2026-11-20): one login attempt at a time, and a code is never shown past its lifetime

**Observed.** A UI login showed "the code expires in 0 min", and OpenAI refused
the code. The record on disk had been `waiting` for six hours. Its code was long
expired, and its worker had been recreated mid-wait. Host, API and worker clocks
agreed within 0.3 s, and the code was published 0.35 s after the CLI requested
it. Neither clock skew nor latency caused it. Three defects did:

1. The worker checked its deadline only when the CLI printed a line. After
   printing the code the CLI is silent, so the deadline was never checked. It
   was also measured on `time.monotonic()`, which stops in the Docker Desktop
   VM while the host sleeps.
2. No path wrote a final state when the task died (worker restart, broken log
   stream). The record stayed `waiting` forever.
3. The card rounded the time left to whole minutes and clamped it at 0. It kept
   showing the dead code and hid every button, so the only way out was
   *Disconnect* or a lab restart.

**Decision.**

- **The code's lifetime is the provider's and is never extended.** The UI counts
  down `code_lifetime_seconds` (15 min, or what the CLI prints). The worker gets
  a separate `grace_seconds` (5 min) to settle a last-second approval and clean
  up. The grace is never shown as time left on the code. The countdown is
  anchored on a clock read *before* the container starts, so it can only end
  early.
- **The API judges expiry on its own clock** (`login_view`). A `waiting` record
  past `expires_at` reads `expired` and loses its code, whatever became of the
  worker. A `queued`/`starting` record nobody moved on reads `failed/timeout`.
  Every response carries `server_time`, and the browser counts down
  `expires_at − server_time` from when the response arrived. A skewed browser
  clock does not shift it.
- **One attempt at a time, each fully isolated.** `POST …/connect` mints an
  attempt id and points `<harness>.login.json` at it. Only the API (and a
  disconnect) writes that pointer. Each worker writes `<harness>.login.<attempt>.json`
  and has its own container and session copy (`connect-<harness>-login-<attempt>`).
  `?regenerate=true` supersedes the running attempt. Its worker sees that within
  a second, stops its CLI, discards its copy and writes nothing. File locks
  would not have helped: they exclude nothing on the Docker Desktop bind
  (`memory/learnings.md`). A new attempt also stops leftover login containers
  and removes their copies from a worker that died.
- **Every exit writes a final state:** `connected`, `expired`, or `failed` with
  a `reason`. The reasons are `rejected` (the provider refused it), `timeout`
  (the lab's own budget), `connection` (the runner could not reach the
  provider) and `error`. They are read from what codex 0.171.0 prints (strings
  taken from its binary). The one-time code is masked in any message kept.

**Unchanged.** It is still the CLI's own `codex login --device-auth`. The lab
never reads or edits a token, only moves the file the CLI wrote, through the
same allow-listed copy-back. The session store, the per-run private copies and
the masks are unchanged.

## Amendment (2026-11-20, later): a subscription run shows an API-price estimate beside its unknown cost

A codex run's cost stays `cost_usd: null` (point 5 above). Comparing it in
dollars with a gateway run still matters. `GET /api/runs/{id}/validation` now
adds `estimated_cost_usd` to a run whose `cost_source` is `subscription_quota`.
The figure is the run's own tokens priced at OpenAI's published API list price
(`api_price_estimate.py`), and `estimate_basis` names the model, the source and
the date of the price table.

- **Beside, never instead.** `cost_usd` stays null. Rankings, statistics,
  experiment cost deltas and the leaderboard still read only `cost_usd`, so no
  aggregate includes a figure nobody was billed. Only the comparison table and
  the run detail show the estimate, labelled "≈ … (estimated, API price)". The
  comparison table also uses it to mark the cheaper column.
- **Computed on read**, from the stored token counts and the run's configured
  executor model (`detected_model` can be wrong; K-015 recorded `"I"`). Runs
  validated before this change get the estimate without re-validating.
- **Tokens are priced as recorded.** Non-cached input is billed at the input
  rate and cache reads at the cached-input rate. Output includes reasoning and
  is priced once. The short-context rate is always used: usage is a sum over
  requests, so the long-context share (over 272K per request) is unknown, and
  the estimate is a floor for runs that crossed it.
- **A dated snapshot, not a live lookup.** The gateway's `/model/info` answers
  403 with the lab's key, and the codex catalog carries no prices. The table
  is read from https://developers.openai.com/api/docs/pricing (2026-11-20),
  and a model absent from it gets no estimate, never a guess. gpt-5.6-sol's
  price is promotional until 2026-11-30.
