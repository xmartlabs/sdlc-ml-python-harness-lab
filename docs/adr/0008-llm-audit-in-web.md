# ADR-0008: LLM audit exposed in the web app

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (worker owns slow/side-effecting work), ADR-0005
  (structured attribution — the machine-readable counterpart), ADR-0006 (web
  run ids), `scripts/bench_audit.py`, `src/harness_lab_app/audit_service.py`,
  `src/harness_lab_app/tasks.py`

## Context

`scripts/bench_audit.py` produces a valuable *qualitative* diagnostic of a run:
a strong LLM reads the run's artifacts (logs, diff, usage, score, available
rules) and writes `data/runs/<id>/audit.md` describing skill/rule usage,
efficiency/loops, SDLC adherence and failure points. It was CLI-only — the web
app surfaced the machine-readable `Attribution` (ADR-0005) but not this prose
audit. The run → test → audit loop was therefore incomplete in the UI.

Two forces shaped the design: the audit is an LLM HTTP call that can take up to a
few minutes (must not block an API request), and `bench_audit.py` is the
battle-tested source of truth we did not want to duplicate into `src/`.

## Decision

Expose the audit through the same worker-enqueue + poll pattern as objective
validation, reusing the CLI script.

- **New `Audit` table + `AuditService`** own only the DB record and the status
  lifecycle (`running` → `done` | `failed`) that the frontend polls. No gateway
  or subprocess logic lives in the service, so the API can import it freely.
- **Execution runs in the worker** (`audit_run` Celery task), which **shells out
  to `scripts/bench_audit.py`** with `build_run_environment()` — the same helper
  the runner uses — so the `.env` gateway URL's `localhost` is rewritten to the
  docker host alias (the worker is a container). The task reads back `audit.md`
  and stores it on the `Audit` row. Reusing the script keeps one source of truth
  and avoids an `src → scripts` import coupling.
- **Endpoints:** `POST /api/runs/{id}/audit?auditor_model=…` marks the row
  running and enqueues; `GET /api/runs/{id}/audit` returns the row (404 until
  requested). The frontend renders `markdown` in an "LLM audit" panel with a
  "Run audit" button, an optional auditor-model field, and status polling.
- **`bench_audit.py` made web-tolerant:** its `find_run` hard-exit is replaced by
  `resolve_or_synthesize_run` (mirroring ADR-0006) so web-lane `K-NNN` runs
  absent from `runs.yaml` can be audited — the auditor only needs a few optional
  context fields, and the full `metadata.json` is fed into the prompt anyway.

## Consequences

- The web app now closes the run → validate → audit loop: attribution answers
  "what did it touch" quantitatively; the audit answers "how well did it behave"
  qualitatively.
- Rendering is plain pre-wrapped Markdown text (no markdown lib / no external
  dep, honoring the artifact CSP constraints); prettier rendering is a future
  enhancement.
- The worker must reach the gateway; on Docker Desktop `host.docker.internal`
  resolves by default. On Linux the worker would need an explicit `extra_hosts`
  entry (documented, not yet added since the current environment is macOS).
- The auditor model defaults to `bench_audit.py`'s `DEFAULT_AUDITOR_MODEL` /
  `AUDITOR_MODEL` env when the request omits one; the gateway must actually
  serve that model.
