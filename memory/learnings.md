# Learnings

> Non-obvious facts discovered while working: gotchas, why-it-is-this-way, dead ends
> to avoid. Append new entries at the top. One fact per entry.

## Roadmap checkboxes need a three-way done-ness distinction, not binary — 2026-07-07

Auditing `docs/roadmap-local-webapp.md` against real code (not memory or
assumption) surfaced that "done" splits into three states that a plain
checkbox can't express on its own: (1) fully done, (2) backend/model
implemented but no frontend action exists yet (e.g. case edit/delete/clone,
score submission, artifact indexing all have working endpoints/services but
no UI), and (3) declared/scaffolded but never actually exercised by any code
path (e.g. `RunState.draft`/`scoring`/`auditing` are valid enum members that
no transition ever assigns; `POST /api/runs/{run_id}/start` was never built
because the worker's poll loop makes it unnecessary).

**Why it matters:** checking a box because the enum member or database column
exists overstates progress; leaving it unchecked with no note understates it
and hides real backend work already done. Either mistake makes the roadmap
stop being trustworthy as a status source.
**How to apply:** when marking a roadmap/checklist item, grep for the concrete
code artifact that proves it (function name, enum assignment, route
decorator, frontend call site) before flipping `[ ]` to `[x]`. If the backend
exists but nothing calls it from the UI, leave it unchecked with a one-line
note naming what's missing, rather than checking it or leaving it silently
blank.

## Cross-process cancellation needs a DB-persisted signal, not shared memory — 2026-07-07

The API and worker run as separate Docker Compose processes/containers with no
shared memory, so `cancel_run` cannot directly reach into a running
`subprocess.Popen`. Instead, `Run.cancel_requested` is a DB column the API sets
when `state == running` (without touching `state` itself), and the worker's
drain loop in `_run_process_streaming` polls it every 0.5s, calling
`process.terminate()` when true. The worker itself then makes the terminal
`cancelled` transition via `RunService.mark_cancelled`, never the API.

**Why it matters:** if `cancel_run` had flipped `state` to `cancelled`
directly, a still-running worker would silently overwrite it back to
`completed`/`failed` via `mark_finished` once the subprocess exited — a race
between two processes writing the same row. Only one process should ever own
the terminal-state write for a running run.
**How to apply:** for any cross-process action on a live run/job when
API and worker are separate processes, signal through a DB column the owning
process polls, and let the owning process write the terminal state — don't
let the initiator guess the outcome.

## Worker log streaming needs threads per stream, not sequential reads — 2026-07-07

`subprocess.Popen` with separate `stdout`/`stderr` pipes will deadlock if you
read one stream to completion before touching the other (the child blocks once
the unread pipe's OS buffer fills). `worker.py` reads each stream in its own
thread into a shared `Queue`, and the main loop drains the queue into
`RunEvent` rows as lines arrive — this is what makes logs appear while the
process is still running instead of only at exit.

**Why it matters:** a naive `for line in process.stdout: ...` followed by
`for line in process.stderr: ...` works for tests with tiny output but silently
hangs on any real command that fills a pipe buffer.
**How to apply:** always drain multiple subprocess pipes concurrently (threads
+ queue, or `selectors`), never sequentially, when both streams matter.

## Docker-first local app wiring — 2026-07-07

The local web app is now practical to run under Docker Compose with the repo
mounted into the API and frontend containers. The API image must copy
`README.md` and `src/` before `uv sync` or Hatchling/uv editable builds can fail
while resolving the package.

**Why it matters:** the Docker path is the most stable way to keep macOS and
Linux behavior aligned for this project.
**How to apply:** prefer `docker compose build api frontend` and keep the
compose services pointed at the mounted repo root.

## Run config contract must stay aligned — 2026-07-07

`RunConfigCreate` needs to expose every flag consumed by the runner adapter
(`resume`, `dry_run`, prompt/model settings, and max steps) or preview and run
creation tests will fail even when the database model already has the field.

**Why it matters:** the runner adapter is the first real contract between UI and
benchmark execution.
**How to apply:** update the schema and the adapter together whenever command
flags change.

## How this template is wired for agents — 2026-06-22

The single source of truth for governance and skills lives in `.github/`; native
tool layouts (`.claude/`, `.opencode/`, `.agents/`, `.codex/`) are **generated** by
the `skills_sync` engine. Never hand-edit generated skill links or the region
between the `BEGIN/END GENERATED SKILLS` sentinels in adapter files — run
`make sync-skills` instead, and `make check-sync` to verify nothing is stale.

**Why it matters:** hand-edits to generated artifacts are silently overwritten and
break the CI drift gate.
**How to apply:** edit `.github/skills/*.md` (source) and regenerate.

<!-- Add new learnings above this line -->
