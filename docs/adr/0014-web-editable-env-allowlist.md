# ADR-0014 — Web-editable `.env` behind an allowlist

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** ADR-0013 (web backoffice and overlay model), ADR-0016 (model routing
  overlay), ADR-0015 (single-user auth)

## Context

Connection settings live in a single `.env` at the repository root: gateway base URL
and token, model defaults, local runtime URLs, harness credentials, Langfuse keys.
Changing any of them means editing a file on the server over SSH.

Two properties of the existing code shape the design:

- `config.read_env_file_value()` re-reads `.env` on every call, so values it serves
  take effect immediately — **no restart is needed** for gateway settings. The 502
  the API used to raise said the opposite, and was wrong.
- `config.get_settings()` is `lru_cache`d, so `HARNESS_LAB_*` settings only take
  effect on process start; `REPO_ROOT`, `DATABASE_URL` and `EXECUTION_BACKEND` would
  actively break running containers if changed underneath them.

`.env` is also parsed by three quote-unaware readers in this repo (the compose file,
`config.read_env_file_value`, and the runner's loader), so a value containing quotes
or spaces means different things to different consumers.

## Decision

We will expose `.env` through a narrow, allowlisted service rather than as a text
editor.

1. **`src/harness_lab_app/env_file.py`** — a stdlib-only editor that preserves every
   raw line (comments, blank lines, ordering), updates the **last** occurrence of a
   key (dotenv is last-wins while `read_env_file_value` is first-wins, so duplicates
   are surfaced as a warning), and writes atomically: temp file in the destination
   directory → `fsync` → `chmod 0600` → `os.replace`. Rolling backups (20) land in
   `data/app/env-backups/`.
2. **Quoting is rejected, not supported.** A value containing whitespace, quotes,
   `#` or `\` is refused with an explanatory error. Supporting it would require all
   three parsers to agree, which they do not.
3. **`src/harness_lab_app/settings_service.py`** — an explicit `EDITABLE_KEYS`
   allowlist of 18 keys in five groups (gateway, models, runtimes, credentials,
   tracing), each with a label, help text and an optional validator (URLs must be
   http(s); the gateway URL must end in `/v1`; model ids must be
   provider-qualified). Anything not on the list is rejected.
4. **Secrets never leave the process.** Reads return a mask (`sk-…abcd`); the form
   sends a key only when the user typed into it, so an untouched secret is
   physically absent from the request.
5. **`HARNESS_LAB_*` is read-only**, exposed as runtime facts via
   `GET /api/settings/runtime`. Auth material
   (`HARNESS_LAB_ADMIN_PASSWORD_HASH`, `HARNESS_LAB_SESSION_SECRET`) is in
   `PROTECTED_KEYS` and written only by the password tooling.

The misleading "restart the app" 502 is corrected to point at the Settings page and
to state that the change takes effect immediately.

## Consequences

- Rotating a gateway token is a browser action that takes effect on the next
  request, with the previous `.env` recoverable from `data/app/env-backups/`.
- The allowlist has to be extended by hand when a genuinely new setting appears.
  That is the point: it is the audit surface.
- A `.env` written by hand with quoted values can still be *read*; it just cannot be
  *edited* through the web until the quoting is removed. The error says so.
- Mode `0600` on `.env` is now enforced on every write, which is stricter than what
  a hand-edited file usually has.

## Alternatives considered

- **A free-text editor for `.env`.** Rejected: one typo in `DATABASE_URL` or
  `REPO_ROOT` bricks the running stack, and secrets would have to be sent to the
  browser in the clear to be editable.
- **Move settings into the database.** Rejected: the CLI lane, the runner container
  and `docker-compose.yml` all read `.env`; a second source of truth would drift
  from the one the runner actually uses.
- **`python-dotenv` for the write path.** Rejected: it rewrites the file its own
  way, and the repo's own readers are quote-unaware, so round-tripping through it
  changes semantics for the other consumers.
