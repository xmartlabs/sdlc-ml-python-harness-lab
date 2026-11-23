# ADR-0096: Antigravity CLI runs on a Gemini API key, never on a personal sign-in

- **Status:** Accepted
- **Date:** 2026-11-22
- **Deciders:** Marcos Soto
- **Related:** [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0073](0073-a-measurement-names-its-real-instrument-and-its-reach.md),
  [ADR-0075](0075-every-arm-is-measured-by-the-same-instrument.md),
  [ADR-0094](0094-codex-and-gemini-run-on-their-own-subscription-login.md),
  plan [`2026-11-22-antigravity-cli-api-key-harness.md`](../plans/2026-11-22-antigravity-cli-api-key-harness.md)

## Context

Google stopped serving Gemini CLI for Google AI Pro/Ultra and free accounts on
2026-06-18; Antigravity CLI (`agy`) replaces it. ADR-0094 wired `codex` and
`gemini` on each provider's own subscription login. That pattern cannot be
copied to `agy`:

- Google's FAQ: using third-party software to access Antigravity violates the
  ToS, wrapping `agy` in third-party applications "is not supported under
  consumer plans", and for third-party agents Google recommends "a Gemini
  Enterprise or Google AI Studio API key".
- With a Gemini API key, requests go straight to the Gemini API and "the CLI
  never establishes an account session" (docs/install). This is the documented
  mode for headless and CI runs.

Probing the official binary (agy 1.5.14, Linux arm64, no real credential, a
local Gemini API mock through the documented `GOOGLE_GEMINI_BASE_URL`; the
facts are tabulated in the plan) turned up five constraints:

1. **The key reaches agent-run code.** `run_command` children inherit the
   whole environment, `GEMINI_API_KEY` included. agy has no option to scrub
   it, and its `--sandbox` cannot start inside the runner container.
2. **A soft-deny ends the turn silently.** In headless `request-review`, a
   tool needing approval is denied, and the turn ends: exit 0, `SUCCESS`, no
   answer. An explicit allow list plus `--mode accept-edits` avoids that
   without `--dangerously-skip-permissions`.
3. **The stream never names the API model.** `init.model` echoes the slug
   only when `--model` is passed. The API model was read off the request path.
4. **agy loads skills only from `.agents/skills`.** Its rules come from
   `AGENTS.md`, `GEMINI.md` and `.agents/rules/`. Builtin skills under
   `$HOME` are always listed.
5. **`agy models` does not call the API.** It lists the slugs this version
   offers, not what a key may use.

## Decision

1. **A separate harness, `antigravity_cli`, on an API key only.** The
   `gemini` harness (and its discontinuation notice) and the human-driven
   `antigravity` flow stay as they are. No OAuth, keychain, consumer session,
   account rotation or proxy is ever used. `native_auth` (subscription logic)
   is not shared: `api_key_auth` owns the opposite rule set.
2. **The key lives in the lab store, written only by the worker.** It sits at
   `<HARNESS_LAB_AUTH_DIR>/antigravity_cli/gemini_api_key` (0600, written
   through `mkstemp`). It is never stored in `.env` or the DB, and never sent
   back by the API (only its last four characters). It never crosses the
   broker: the API writes it to a 0600 drop file under `data/app/native-connect/`
   (random name, 15-minute expiry), and the Celery message carries only that
   name. Celery logs the whole body of a task the worker does not know, so
   `argsrepr` alone was not enough. The request model is a `SecretStr`.
3. **The key is the only secret an agy container holds.** Its environment
   drops every other provider's credential. Inside `/app` these are masked:
   - `.env`, `.claude/settings.local.json` and the lab database;
   - `data/app/env-backups`, `data/app/native-connect` and `backups/`;
   - every other run's provider session that exists at start
     (`data/runs/*/session`).

   It gets a private `HOME` and no subscription session. Config goes through
   `--settings` (session-only), and the self-updater is off.
4. **Third-party code is refused by default.** SWE-bench/SWE Pro instances
   and external target repositories (anything that is not a real ad hoc task)
   are refused unless `HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1`. The check runs at
   preflight (per case, also inside batches and experiments) and again on a
   retry. The key is redacted from every streamed line, both in the runner
   and in the worker's handle. After the run, on every way out, the worker
   scrubs the key it read at the run's start from:
   - the run directory and workspace;
   - every file in the lab tree modified during the run.

   Any hit sets `metadata.secret_exposed`.
5. **Permissions are explicit, not "dangerous".** The allow list is
   `command(*)`, `read_file(*)`, `write_file(*)`, `read_url(*)`, with
   `--mode accept-edits` and `--disable-slash-commands`. That gives the reach
   claude and codex have in the container (ADR-0094 §3); MCP and browser
   execution stay denied.
6. **Docker only.** The host lane is refused at preflight.
7. **An explicit, verified model.** Preflight requires a slug from agy
   1.5.14's catalog, which maps to its Gemini API model and thinking level.
   The run records `executor_model`, `detected_model` (`init.model`),
   `api_model` and `thinking_level`.
8. **Cost is the list price, in `cost_usd`.** agy reports tokens but no
   bill, and an API key is billed per token. So `cost_usd` = the run's tokens
   × Google's paid-tier Standard price for the API model **on the run's
   date**. The table (`api_price_estimate.GEMINI_PRICE_TABLE`) carries each
   rate's validity window, and the whole table stops answering after
   2027-05-22 until it is read again. The cost is recorded with
   `cost_source: api_list_price_estimate` and a `cost_basis`, and never as
   `subscription_quota`. An unpriced or unknown model, or a date with no known
   rate, gets `cost_usd: null`, `cost_source: api_price_unknown` and a reason.
9. **One reader shape.** `harness_events.antigravity_to_claude` translates
   `init`/`step_update`/`result` into the claude events every reader uses. A
   skill counts as invoked when its `SKILL.md` is read (agy has no skill
   tool).
10. **History holds.** `antigravity_cli` enters the condition hash (additive,
    like codex/gemini). Claude and opencode stay out, so recorded hashes do
    not change. The new "lab" failure markers (API key rejected, quota
    exhausted, model not served) only match a reason agy produced, so no other
    harness's classification moves. Zero recorded runs matched them anyway.
11. **Pinned install.** The runner installs the versioned GCS tarball from
    Google's release manifest (1.5.14, build `5302690847260672`), checks it
    against the manifest's sha512 per architecture, and labels it
    `antigravity-cli.version`.

## Consequences

- Antigravity arms run under the same cases, containers, conditions,
  validation, scoring, attribution, failure classification and comparison as
  every other harness. Their cost compares directly with API-billed arms.
- **Accepted risk:** an agent, or code it runs, can read the key. The
  mitigations are containment (one key, no other secrets, per-run home), the
  untrusted-code guard, redaction, and documentation: use a dedicated,
  budget-capped, restricted key and rotate it after any untrusted run.
  Redaction matches the exact value only, so a transformed copy is not
  caught: base64, split, compressed, or inside a git object. A provider
  session of a codex/gemini run that starts *after* an agy container (on the
  parallel lane) is not masked in it. The agent can also exfiltrate the key
  over the network: the container has network access, as every arm's does.
- **The cost is an estimate.** The short-context rate is used, so 3.1 Pro
  runs whose prompts exceed 200k are a floor. Dated price changes
  (2026-12-01, 2027-01-01) need the table updated. Free-tier keys are billed
  $0 by Google but recorded at the paid price.
- **`lab_root` rules.** agy reads the lab's `AGENTS.md`, `GEMINI.md` and
  `.agents/rules/GEMINI.md`, which is three adapter files where other
  harnesses read one. `bare`/`native`/`injected` masks already cover them.
- **Not verified with a real key yet:** an authenticated request, which slugs
  a given key may use, and real usage numbers. Everything else was checked
  against the binary.

## Alternatives considered

- **Reuse a personal Antigravity sign-in** (keychain, `~/.gemini`, OAuth
  copy). This is forbidden by Google's terms for third-party apps on consumer
  plans, and on macOS the token sits in the keychain.
- **`--dangerously-skip-permissions`.** It works, but it also approves MCP
  and browser execution, which no other arm gets. The explicit list is
  equivalent for coding and narrower.
- **Scoped allow list limited to the workspace.** A read of `/usr/lib` or
  `~/.cache` would be soft-denied and end the turn silently. That would bias
  the arm and score it as the model's work.
- **Keep the key in `.env`.** Every harness's container receives `.env`, so
  the key would leak into codex/claude/opencode runs.
- **Gemini through OpenCode + LiteLLM.** That stays available. It measures
  opencode with a Gemini model, not Google's agent; both are valid arms, and
  they answer different questions.
- **ADC / Vertex / Gemini Enterprise.** Documented by Google as the
  enterprise path. Not wired: it needs a GCP project and `gcloud` credentials
  in the container. It is the alternative for organizations that cannot use
  an AI Studio key.
- **Record `cost_usd: null` with an estimate beside it** (as for codex).
  Rejected: an API key is a per-token bill, so leaving it out of rankings
  would hide a real cost.
