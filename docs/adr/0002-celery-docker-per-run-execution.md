# ADR-0002: Execute web-app benchmark runs with Celery and one Docker container per run

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** `docs/roadmap-local-webapp.md` (Phase 4), `memory/learnings.md`

## Context

The web app wrapped `scripts/bench_run.py` behind a DB-polling worker that ran
`subprocess.Popen(run.command)` inside the API Docker image. A Tetris run created
from the UI never executed the agent while the equivalent `make bench-run` worked
on the host, because the worker environment did not satisfy the runner's contract:

- The `python:3.11-slim` image had no `opencode` binary or node; `bench_run.py`
  silently degrades to a manual-paste fallback and exits 0 when opencode is
  missing, so the run looked successful without running the agent.
- No env propagation: `GATEWAY_BASE_URL`/`GATEWAY_TOKEN` never reached the
  `gateway` provider in `opencode.json`.
- LiteLLM (`:4000`) and LM Studio (`:1234`) run on the host; inside a container
  `localhost` does not reach them.
- The worker service sat behind a compose profile and was not started by default.
- `dry_run` defaulted to true across schema, model, and UI.

## Decision

We will replace the polling worker with a queue-and-container architecture:

- **Celery + Redis (broker only)** for run dispatch. `RunService` publishes
  `harness_lab_app.tasks.execute_run` after commit; the `run` table stays the
  single source of truth (`task_ignore_result=True`, `acks_late`, concurrency 1).
- **One Docker container per run** (SWE-bench style) using a dedicated
  `Dockerfile.runner` image that bundles python/uv, node 20, the `opencode` CLI,
  and the conductor plugin. The Celery worker launches sibling containers via the
  host daemon (`/var/run/docker.sock` mounted; docker-out-of-docker).
- **Host networking bridge**: the run environment is built from the repo `.env`
  with `localhost`/`127.0.0.1` rewritten to `host.docker.internal` for gateway
  URLs; env injection wins over the mounted `.env` because `_load_env` uses
  `setdefault`.
- **Whole-repo rw bind mount** into the runner container: `bench_run.py` writes
  `benchmarks/*/runs.yaml`, `BENCH_CONTEXT.md`, `workspace/`, and `data/runs/`
  on the host so the CLI flow (`bench-close`, `bench-audit`) keeps working.
- **Executor port with two adapters** (`RunExecutor` → `DockerRunExecutor`,
  `SubprocessRunExecutor`) so application logic stays daemon-free and testable;
  backend selected via `HARNESS_LAB_EXECUTION_BACKEND`.
- **Web scoring contract**: `score_mode=manual` only; runs finish in
  `awaiting_manual_score` and are scored via `POST /api/runs/{run_id}/score`.
  `interactive` needs a TTY and stays CLI-only; `auto` was removed (the runner
  never accepted it).

## Consequences

- Easier: web runs execute the real agent in a reproducible environment; live
  logs stream from container logs into `RunEvent`; cancel maps to
  `container.stop`; worker restarts reconcile orphaned `running` runs to
  `failed` with a retry/`--resume` path; everything starts with one
  `docker compose up` (no hidden profile).
- Harder/accepted risks: the worker holds the docker socket (root-equivalent on
  the host — acceptable for a single-user local lab, socket mounted on the
  worker service only); DooD requires `HARNESS_LAB_HOST_REPO_ROOT` (`${PWD}` in
  compose) because bind mounts resolve against the host daemon; runner
  concurrency must stay 1 while `bench_run.py` mutates repo-root shared state.
- Follow-up: real-run validation against a live LM Studio + LiteLLM gateway;
  artifact/diff/audit panels (roadmap Phase 5).

## Alternatives considered

- **Host-run worker process** (no containers) — inherits opencode/.env/localhost
  for free but loses isolation and the single `docker compose up` story; kept
  reachable as the `subprocess` execution backend.
- **Fixing the polling worker in place** (install opencode into the api image,
  inject env) — smaller step but keeps queueless polling, no per-run isolation,
  and grows the api image; rejected in favor of the target architecture.
- **Celery result backend for run state** — redundant with the `run` table and
  adds a second source of truth; rejected.
