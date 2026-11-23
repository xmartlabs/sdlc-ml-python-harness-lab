# ADR-0097: Antigravity CLI can run on the operator's own Google sign-in

- **Status:** Accepted
- **Date:** 2026-11-23
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0096](0096-antigravity-cli-runs-on-a-gemini-api-key.md) (its
  "never on a personal sign-in" rule)
- **Related:** [ADR-0094](0094-codex-and-gemini-run-on-their-own-subscription-login.md)

## Context

ADR-0096 put `antigravity_cli` on a Gemini API key only. The operator pays for
Google AI Pro and wants the lab to use it, as it does for codex (ChatGPT plan)
and claude (Claude plan), without a separate per-token bill.

What Google says (read 2026-11-23):

- **Google AI Pro adds no Gemini API quota.** Per AI Studio's "Google AI
  plans" page, the plan's quota "applies only within the Google AI Studio web
  interface". It gives US$10/month of Cloud credits, which need billing and a
  US$5 prepay. The free API tier serves only the Flash and Hydrogen models.
- **Antigravity FAQ.** "Antigravity CLI headless mode (`agy -p`) is intended
  for direct scripting and CI workflows. Wrapping `agy` inside third-party
  coding agents or applications, or automating account rotation, is not
  supported under consumer plans." The Terms add that "using third party
  software … to access the Service (e.g. using OpenClaw with Antigravity
  OAuth)" is a breach and may lead to suspension.
- **Headless runs.** The headless docs say headless mode "uses your cached
  credentials. Authenticate once with an interactive `agy` session first."

The lab is the operator's own tool. It runs `agy -p` itself, with the sign-in
`agy` itself performs, one account, no rotation. Whether that counts as
"wrapping in an application" is not defined by Google. The operator was told
this and chose to use their account (2026-11-23).

Verified on the agy 1.5.14 binary in the runner image (no account):

- `agy` with no arguments, on a TTY, shows "Select login method: 1. Google
  OAuth / 2. Use a Google Cloud project". It then prints an
  `accounts.google.com/o/oauth2/auth` URL (PKCE, redirect
  `https://antigravity.google/oauth-callback`) and waits for the authorization
  code to be pasted. The wait has no time limit; the 60-second limit applies
  only to `-p` print mode.
- The URL is printed as an OSC 8 terminal hyperlink. Its *target* holds the
  whole URL; the visible text is wrapped to the screen width.
- With no D-Bus session (a container), agy logs "Using file-based token
  storage". At start-up it reads `$HOME/.gemini/antigravity-cli/
  antigravity-oauth-token` and `$HOME/.config/antigravity/
  credential-user-scope` (strace).
- A wrong code prints `token exchange failed: oauth2: "invalid_grant"`.
- Signed out, `agy models` says "Please sign in to view available models". On
  a sign-in it asks the service for the plan's models.

## Decision

1. **A second credential for the same harness.** `antigravity_cli` runs on the
   operator's Google sign-in when one is stored, else on the Gemini API key
   (ADR-0096), else it is refused at preflight. The sign-in wins because it is
   what the operator connected to use the plan. The key stays as the fallback.
2. **agy's own sign-in, driven from the UI.** "Sign in with Google" starts
   `scripts/agy_login.py` in a runner container on the interactive lane. The
   helper runs `agy` on a pseudo-terminal and picks "Google OAuth". It reads
   the URL from the OSC 8 target and publishes it. The operator signs in in
   their browser and pastes the code Google shows into the card; the helper
   types it into agy.
   - The code travels API → 0600 drop file (`data/app/native-connect/
     <h>.code.<attempt>`) → worker → the container's exchange directory, where
     it is read once and deleted. It never crosses the broker and is never
     logged.
   - The helper prints only its own one-line records, never agy's screen.
   - The lab sets a 15-minute window, so a forgotten sign-in does not hold a
     container.
3. **The session is an allow-list, like codex/gemini.** After the sign-in, the
   worker keeps the files agy wrote outside its work directories
   (conversations, logs, caches, builtin skills, projects, memories) and lists
   them in a manifest. They are stored at `<HARNESS_LAB_AUTH_DIR>/
   antigravity_cli/account/` (0700/0600), which is never in the repo and never
   in `.env` or the DB.
4. **Each run gets a private copy.**
   - The copy is `data/runs/<id>/agy-home`, mounted as the run's `HOME`.
   - The run gets no `GEMINI_API_KEY`: with a key, agy would bill the key.
   - When the run ends, only files already in the manifest return to the
     store, and only when they are regular, non-empty files and a sign-in is
     still stored. That return path is how a refreshed token is kept.
   - Everything else in the copy is deleted: conversations, memories, plugins.
   - Every other container, of any harness, gets other runs' `agy-home` and a
     sign-in's `agy-exchange` emptied with a tmpfs.
5. **Billing follows the credential.** On the sign-in, a run is
   `billing_mode: subscription`, `cost_usd: null`,
   `cost_source: subscription_quota`, exactly like codex/gemini. On a key it
   stays ADR-0096's list-price estimate.
6. **Models come from the plan.** On the sign-in, preflight needs a model and
   a well-formed slug, but not one in the lab's Gemini API price map: the plan
   decides what is served. `agy models` lists the plan's models, and the test
   uses the plan's default unless a model is picked.
7. **Third-party code is still refused by default.** A command the agent runs
   can read the token in the run's home, so SWE-bench instances and external
   targets need `HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1`, as with a key.
8. **Never:** the macOS keychain, a token copied from the operator's machine,
   the IDE's session, more than one account, account rotation, or a proxy.
   "Sign out" deletes only the lab's copy.

## Consequences

- Antigravity arms can run on the operator's AI Pro quota. Their cost is
  "subscription quota", the same instrument as codex and claude on a plan.
- **Account risk, accepted by the operator.** Google may treat this use as
  outside consumer-plan support. The UI states it beside the button.
- **The token is in the run's home.** Code the agent runs can read it, just as
  it can read the API key. The mitigations:
  - a per-run copy;
  - masks in every other container, the validation lane included;
  - the untrusted-code guard;
  - the run's container lifetime.

  The container has network access. The copy is never indexed as an artifact
  nor archived by `make backup`. A run returns a refreshed token only to the
  sign-in it was copied from (a `generation` in the manifest), so a run that
  ends after a new sign-in cannot write the old account's token over it.
- **Masks are taken when a container starts.** A copy created later (a run, or
  a sign-in, started while this container runs) is readable through the shared
  `/app` bind until this container ends. This is the same window that already
  existed for codex/gemini session copies (ADR-0094). Closing it means keeping
  per-run credential copies outside the repo bind, which is a later change.
- **Not verified with a real account yet.**
  - Whether a refreshed token is written to the same file.
  - Whether the scope file is always written.
  - Which models `agy models` lists for AI Pro.
  - The plan's quota per run.

  The manifest records whatever the first real sign-in writes. The first
  sign-in from the UI is the end-to-end check.
- ADR-0096's text keeps describing the key mode. Its "never on a personal
  sign-in" rule is amended by this ADR.

## Alternatives considered

- **API key only (ADR-0096 as it was).** Kept as the fallback. It does not use
  the plan the operator pays for, and AI Pro adds no API quota.
- **Copy the IDE's or the host's sign-in.** The token sits in the macOS
  keychain, and this was ruled out from the start.
- **Keep the whole home between runs.** It would carry conversations, memories
  and plugins from one arm to the next. The allow-list keeps only the sign-in.
- **`agy -p` print-mode sign-in.** It waits only 60 seconds for the code, too
  short for a person switching to a browser and back.
