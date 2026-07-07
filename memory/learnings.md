# Learnings

> Non-obvious facts discovered while working: gotchas, why-it-is-this-way, dead ends
> to avoid. Append new entries at the top. One fact per entry.

## Claude Code gateway model names must match native alias resolution — 2026-07-07

Claude Code reads the generated `.claude/agents/*.md` `model:` frontmatter for
subagents. To make gateway-routed Claude Code behave like direct Claude Code, use
Claude Code's native aliases in agent frontmatter (`opus`, `sonnet`, `haiku`,
`fable`) and pin them with `ANTHROPIC_DEFAULT_OPUS_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and
`ANTHROPIC_DEFAULT_FABLE_MODEL`. The gateway must expose the exact full model IDs
Claude Code resolves to (`claude-opus-4-8`, `claude-sonnet-5`,
`claude-haiku-4-5-20251001`, `claude-fable-5`). Nonstandard local aliases such as
`claude-opus-4.8-oauth` may work as legacy LiteLLM names, but they bypass Claude
Code's native alias/capability model and can produce misleading entitlement or
model-selection behavior.

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

## bench_run.py fails open without opencode — 2026-07-07

When the `opencode` binary is missing, `bench_run.py` does NOT fail: it prints a
manual-paste fallback and exits 0. A worker environment without opencode makes
runs look successful while the agent never executed. The Makefile guards this
with `command -v opencode || exit 1`; any new execution backend must guarantee
opencode is present (it is baked into `Dockerfile.runner`) or check explicitly.

**Why it matters:** this silent fallback was the root cause of "web runs succeed
but do nothing" while the CLI worked.
**How to apply:** treat opencode availability as part of the runner contract;
never run `bench_run.py` in an environment where its presence is unverified.

## Docker-out-of-docker bind mounts use host paths — 2026-07-07

The Celery worker runs in a container but launches runner containers through the
host daemon (`/var/run/docker.sock`). Bind-mount sources in `containers.run` are
resolved by the HOST daemon, so they must be host paths — the worker's `/app` is
meaningless there. Compose passes `HARNESS_LAB_HOST_REPO_ROOT: ${PWD}` for this.

**Why it matters:** using the container-local path silently mounts an empty or
wrong directory and the run fails confusingly.
**How to apply:** any DooD `volumes=` argument must come from
`settings.host_repo_root`, never from `settings.repo_root`.

## Env injection beats the mounted .env (setdefault) — 2026-07-07

`scripts/create_run.py:_load_env` populates `os.environ` with `setdefault`, so
env vars injected into the runner container win over the repo `.env` bind-mounted
inside it. `build_run_environment()` exploits this to rewrite
`localhost`/`127.0.0.1` gateway URLs to `host.docker.internal` without touching
the host `.env`.

**Why it matters:** it is the clean seam for host-vs-container networking; no
file rewriting or duplicated config needed.
**How to apply:** put per-environment overrides in the container env, keep
`.env` as the host-truth file.

## Trial condition_hash: identity vs provenance — 2026-07-07

`metadata.json` now carries a hashable trial condition (`provenance.py`). The
`condition_hash` (the experimental "arm" id) is computed over ONLY the
agent-facing harness (skills/agents/docs/hooks hashes), model settings
(temp/context/models), prompt-template hash, and case hash. Provenance-only
fields — `lab_git_commit`, `harness_git_dirty`, `runner_image` — are recorded
but deliberately EXCLUDED from the hash: they say *how* a trial was produced,
not *which arm* it is. So two reps of the same arm share a `condition_hash`
even across lab-code commits, while an ablation (one skill edited) flips it.

**Why it matters:** this is what makes runs groupable into cells/arms and makes
"a diff of two fingerprints = the description of the ablation" true.
**How to apply:** when adding a field, decide if it changes the *arm* (→ into
`compute_condition_hash`) or just documents provenance (→ manifest only).
Prompt hash uses the template (`prompt_source`), NOT the generated `prompt.md`,
which embeds per-run ids and would break arm stability.

## bench_run writes metadata before executing — 2026-07-07

`write_metadata` runs in phase "[1/5] Preparing", before the agent executes. So
`data/runs/<id>/metadata.json` (incl. the provenance manifest) exists even if
the run is later killed mid-execution — useful for verifying provenance without
paying for a full multi-step LLM run. But note: `--dry-run` returns BEFORE
metadata is written, so dry-run does not produce metadata.json.

## Validation runs in the worker, not the API — 2026-07-07

Objective validation executes a container (via the same `RunExecutor` as runs),
so it must run where the docker socket is mounted: the **worker**. The API
process has no socket (ADR-0002), so `POST /api/runs/{id}/validate` enqueues a
`validate_run` Celery task and the frontend polls `GET /validation`. Running it
inline in the API fails with `DockerException: ... /var/run/docker.sock ... No
such file or directory`.

**Why it matters:** any feature that spawns a container belongs in the worker.
**How to apply:** API endpoints that need docker must enqueue, never call the
executor inline.

## Runner container: sh -c, not sh -lc; pygame via uv pip — 2026-07-07

Validation commands run with `sh -c`. A login shell (`sh -lc`) re-sources
profile and drops the runner image's `/opt/venv/bin` from PATH, so `python`
resolves to a system interpreter without the installed deps (pygame not found).
Also: the uv-created `/opt/venv` has no `pip`; install extra runtime deps with
`uv pip install --python /opt/venv/bin/python <pkg>`, not `/opt/venv/bin/pip`.

**Why it matters:** both bugs made the Tetris smoke fail spuriously.
**How to apply:** run in-container commands with `sh -c`; manage the venv with uv.

## Composite score gates subjective by objective — 2026-07-07

The multidimensional score keeps objective/cost/effort/subjective separate and
only combines them in `composite`, which caps the manual functional score at 1.0
when objective validation FAILED (a high manual score is not credible when the
code does not run). Dimensions are never collapsed before that.

**Why it matters:** preserves the signal an ablation needs; prevents a
misleading single number.
**How to apply:** new metrics go in as their own dimension; only `compute_composite`
merges them, explicitly.

## opencode log signals for harness attribution — 2026-07-07

The opencode step logs (`data/runs/<id>/steps/step-*.log`) expose tool/file
behavior in a stable `key=value` format: `permission=<read|edit|bash> pattern=…`
(tool + target), `"shell tool using shell"` (shell), `agent=<mode>` (build /
plan / title / undefined). There is NO explicit skill-invocation event — skills
are injected into context, so file-based skill attribution is a lower bound
(only counts `SKILL.md` reads). `"touching file"` is the file-watcher and fires
on reads too, so it is NOT an edit signal — edits come only from
`permission=edit`.

**Why it matters:** these are the ground-truth signals for pillar 3 attribution;
misreading "touching file" as an edit wrongly flags read-only governance docs.
**How to apply:** count edits from `permission=edit`; treat skills_consulted as a
documented lower bound; parse step logs, fall back to opencode.log.

<!-- Add new learnings above this line -->
