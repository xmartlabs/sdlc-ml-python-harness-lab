# ADR-0013 — Web backoffice with an overlay configuration model

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** ADR-0011 (per-run skill ablation), ADR-0012 (dynamic gateway model
  list), ADR-0014 (web-editable `.env` allowlist), ADR-0016 (opencode config
  overlay)

## Context

The lab was built for a laptop-driven CLI workflow: the browser launches runs, but
every act of *configuration* still requires a terminal in the repository. Two
frictions surface constantly once the lab runs permanently on a local server:

- A model enabled in LiteLLM appears in the run form (the form reads `GET /v1/models`
  live) and then **fails at run time**, because opencode resolves `gateway/<id>`
  against the git-tracked `opencode.json`, which only `make sync-gateway-models`
  writes.
- Trying a new external skill means pasting a single `SKILL.md` into the browser or,
  more realistically, dropping files into the repo from a shell.

Beyond those, an always-on server needs things the app does not have: visible
gateway credentials, drift visibility, and real authentication.

The obvious fix — let the web app edit and commit the tracked tree — is the wrong
one. Provenance in this lab (ADR-0003) is anchored to content hashes and a *clean*
harness tree; a backoffice that rewrites tracked files would make every run's
`harness_git_dirty` flag meaningless and turn the repository into mutable runtime
state.

## Decision

We will add a web backoffice governed by an **overlay principle**:

1. **State configured from the browser lives in the database and `data/`**, both
   gitignored. The tracked tree is never permanently modified by the app.
2. **The overlay is applied at run launch**, as environment/config layered on top of
   the tracked files, not as a rewrite of them. `git status` stays clean across a
   full configure → run → inspect cycle, and a test asserts it.
3. **Provenance uses content hashes, not commits.** An overlay that changes how a
   run behaves contributes a hash to `condition_hash`; an empty overlay contributes
   nothing, so historical arms keep their identity byte-for-byte.
4. **Promotion stays manual.** Anything the user wants permanently in the repo
   (a skill worth keeping, a model worth curating) is written to the tracked tree
   only by an explicit action, and never committed by the app — the user reviews and
   commits.

Four capability areas follow from this and are specified in their own ADRs: the
`.env` editor (ADR-0014), single-user auth (ADR-0015), the model routing overlay
(ADR-0016), multi-file staged skills with remote import (ADR-0017), the health and
drift panel (ADR-0018) and the always-on deployment (ADR-0019).

## Consequences

- The lab becomes manageable from a browser on a headless host: no shell needed to
  add a model, change a token, or trial a skill.
- Two sources of truth now exist for models and skills (tracked file + overlay).
  This is the cost of a clean tree; ADR-0018's drift panel exists to make the
  difference visible rather than silent.
- `data/` becomes load-bearing. It is a *materialization* of database state (see
  ADR-0017), so wiping it is recoverable, but backups now matter.
- Run isolation is unchanged: the runner still mutates and restores the host tree
  per run (ADR-0011) and Celery concurrency stays at 1. Rather than re-architecting
  execution, leftover drift from a `SIGKILL` becomes visible and healable.

## Alternatives considered

- **Let the backoffice commit to git.** Rejected: it destroys the clean-tree
  invariant provenance depends on, and makes an automated agent a committer in a
  repository whose history is itself an experimental artifact.
- **Keep configuration CLI-only and document it better.** Rejected: it does not
  solve the actual problem, which is that the server has no terminal in front of it.
- **Full per-run containerized harness copies (no host-tree mutation).** A better
  end state, but a much larger change than the frictions justify; deferred in favour
  of drift visibility.
