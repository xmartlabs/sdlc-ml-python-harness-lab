# ADR-0018 — Health and drift panel

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** ADR-0013 (web backoffice and overlay model); ADR-0002 (the worker
  owns the docker socket); ADR-0011 (per-run skill ablation); ADR-0016 (opencode
  config overlay); ADR-0017 (multi-file staged skills)

## Context

The lab moved from a laptop to a box that stays on. That changed what "something
is wrong" feels like: on a laptop you notice, because you are already in a
terminal. On a server you notice hours later, when a run fails for a reason that
has nothing to do with the run.

The failure modes are known and each has a shell command that diagnoses it —
`make check-sync`, `git status`, `docker compose logs worker`, `docker images`,
a curl at the gateway. The backoffice exists so none of those require a shell.

Two of them are structural rather than incidental:

- **Ablation mutates the tracked tree.** A run that disables a governed skill
  regenerates `.opencode/skills/` and the adapter blocks, then restores them.
  A `SIGKILL` in between leaves the repository ablated. The brainstorming
  decision was explicitly *not* to re-architect execution for this, but to make
  the resulting drift visible and repairable.
- **The overlay is a materialization.** `data/skills_staging/` (ADR-0017) and
  `data/app/gateway-models.json` (ADR-0016) are derived from the database. They
  can be wiped, hand-edited, or simply not written yet, and nothing today says so.

## Decision

Add `GET /api/health/detail` and `POST /api/health/heal`, backed by
`src/harness_lab_app/health_service.py`, plus a Health page in the SPA.

**One row per check, and every probe is wrapped.** `collect_health()` returns
`{id, label, status, detail, latency_ms, heal, items}` per check, sorted worst
first. A probe that raises becomes an `unknown` row carrying the exception text.
This is not defensive habit: the panel is what an operator opens when the lab is
already broken, and a 500 there would remove the only remaining diagnostic.

Statuses are `ok` / `warn` / `error` / `unknown`, and the distinction is load-bearing.
An unconfigured gateway is a `warn` (a setup step), a missing runner image is a
`warn` (build it), an unusable docker socket is an `error`. Overstating severity
trains the operator to ignore the panel.

**Docker facts come from the worker, over a Celery control command.** ADR-0002
gives the socket to the worker alone; the API can never launch a container. So
`harness_facts` is registered with `@control_command()` in `tasks.py` and reached
with `celery_app.control.broadcast(..., reply=True)`. A control command — not a
task — because the API needs a synchronous answer and the app runs with
`task_ignore_result` and no result backend. No reply is itself the answer: the
worker is down, and runs will sit in `pending`.

**Healing is an allowlist of four named actions**, not a command runner:

| Action | Fixes | Runs in |
|---|---|---|
| `skills_sync` | stale adapter regions, native views, a tree left ablated | api |
| `materialize_staged_skills` | `data/skills_staging/` disagreeing with the DB | api |
| `refresh_models` | a stale `data/app/gateway-models.json` | api |
| `reconcile_runs` | runs stuck `running` after a worker crash | **worker** |

`reconcile_runs` is enqueued rather than executed because stopping a leftover
container needs the socket. The other three are pure filesystem/HTTP work the API
already does elsewhere. An action outside `HEAL_ACTIONS` is refused with a 422
before any work happens — the check is the first statement in `heal()`.

`/api/health/detail` is **not** in `PUBLIC_PATHS`. `/api/health` stays public
because the compose healthcheck uses it; the detail endpoint reports gateway
configuration, repository state and run ids, so it sits behind the session
(ADR-0015).

The stuck-run threshold is six hours. It is a heuristic, so it produces a `warn`
with a heal button, never an automatic action: a genuinely long SWE-bench Pro run
must not be killed by a timer.

## Consequences

- One place answers "is this lab healthy?", and each answer carries the repair.
- The API gains no new privileges. It still cannot see docker except through what
  a worker chooses to report.
- Adding a check is a function plus a row in `collect_health()`. Adding a *heal*
  is deliberately harder: a new key in `HEAL_ACTIONS` and a branch in `heal()`,
  so the set of things the browser can trigger stays reviewable at a glance.
- The panel polls every 30 s and each load runs a live gateway probe. That is one
  cheap request per half-minute against LiteLLM — acceptable for a single-operator
  lab, and the reason the page is not mounted on every screen.
- `reconcile_stale_runs` now has two callers (worker boot and the heal task).
  It was already idempotent; that is now a property the panel depends on.

## Alternatives considered

- **Shell out to `make check-sync` and parse stdout.** Rejected: Phase 0 already
  extracted `check_drift()` precisely so drift detection has one implementation
  shared by CI and the API. Parsing our own output would be a second one.
- **Give the API a read-only docker socket.** It would simplify the docker check
  and quietly undo ADR-0002 — a read-only mount is not a thing docker offers in
  any meaningful sense, and the socket is the whole privilege.
- **A generic `POST /api/health/run-command`.** Faster to write, and it turns the
  session cookie into remote code execution on the host. The allowlist is the
  point of the endpoint, not a limitation of it.
- **Auto-heal on detection.** Rejected: `skills_sync` rewrites tracked files and
  `reconcile_runs` fails live runs. Both are correct when an operator asks and
  destructive when a poller decides.
- **Poll from the server and push over websockets.** More machinery than a
  single-operator panel justifies; a 30 s interval is well inside the timescale
  of every problem it reports.
