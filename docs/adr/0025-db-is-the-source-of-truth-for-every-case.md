# ADR-0025: The DB is the source of truth for every case, not just SWE-bench ones

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** [ADR-0002](0002-celery-docker-per-run-execution.md) (worker owns docker),
  [ADR-0009](0009-swebench-case-type.md) (extended here),
  [ADR-0013](0013-web-backoffice-and-overlay-model.md) (the browser is the
  control surface), `scripts/bench_run.py`,
  `src/harness_lab_app/{tasks,case_service,run_service,health_service}.py`,
  [docs/guide-web-ui.md](../guide-web-ui.md)

## Context

A case created in the web UI could not run. Not "ran badly" — could not run at
all, for three independent reasons, only the outermost of which was visible:

```
$ uv run python scripts/bench_run.py --case multi_step-case --run probe-... --prompt default
Unknown case 'multi_step-case'. Known cases: ['tetris-v1']
```

1. `RUNS_FILES` in `scripts/bench_run.py` is a hardcoded one-entry map
   (`tetris-v1` → `benchmarks/tetris/runs.yaml`) with no discovery, and the
   script exits on any case that is not in it.
2. ADR-0009's `case_spec.json` escape hatch — the channel that already carries a
   DB-only case into the DB-blind runner — was written **only** for the two
   SWE-bench types, and `_load_case_spec` discarded any spec whose `repo` did
   not look like `owner/repo`.
3. The runner image's absence was discovered inside the container, after the run
   had been persisted and queued, and the remedy printed was `make app-up` — a
   shell command, in a lab whose stated point is self-management from the
   browser.

So the only runnable cases were the one curated YAML benchmark and whatever the
SWE-bench importers created. Everything an operator could author was inert, and
the failure told them to go to a terminal.

The lab has two lanes for a case and they had drifted into a hierarchy: the
curated YAML lane (`benchmarks/<case>/task.yaml` + `runs.yaml`) behaved like the
real one, and the DB lane like a special case bolted on for SWE-bench.

## Decision

**Invert that: the DB is the source of truth for every case, and `benchmarks/`
is an export target rather than an input.**

- **`case_spec.json` is the channel for all case types.** `_write_case_spec` no
  longer returns early for non-SWE cases. Every run of every type gets a spec
  staged at `data/runs/<id>/case_spec.json` carrying `case_type`, `case_id`,
  `prompt`, `prompt_variant`, `step_prompts`, `max_steps`, `constraints` and
  `success_criteria`. This widens ADR-0009's DB → container bridge instead of
  adding a second mechanism.

- **`repo` is the provisioning discriminator, and it supersedes ADR-0009's
  "spec present ⇒ repo task".** ADR-0009 said `bench_run.py` "takes a repo-task
  branch when `case_spec.json` is present". Presence now only means *the DB
  described this run*. The question "should the runner clone and install a
  subject repo?" is answered by `spec_provisions_repo(spec)` — true when `repo`
  holds an `owner/repo` slug, false for the empty string a generic case writes.
  Every former `case_spec is not None` test that meant "this is a repo task"
  now calls that helper, which is what keeps SWE-bench behaviour bit-identical.

- **The curated YAML lane still wins where it exists.** The main fork is ordered
  `RUNS_FILES` → `case_spec.json` → informative exit. `tetris-v1` resolves
  exactly as before; the DB lane serves everything else. A prompt variant stored
  as a repo-relative path (as the YAML-imported cases store it) is resolved to
  its file contents by the worker, so the runner only ever sees prompt text.

- **Prompts, not just prompt.** For a multi-step case that provisions no repo,
  step *n* uses `step_prompts[n]` when present, falling back to the generic
  continuation prompt once the list runs out. The old fallback was written for
  game-building and framed every step in those terms.

- **Cases are editable in the browser.** Name, description, type, max steps,
  multiple prompt variants, step prompts, success criteria, constraints and
  validation commands, plus Sync to YAML / Clone / Delete. `case_id` stays
  read-only: it is the key every run in the history joins on. SWE-bench cases
  render read-only — their prompts and test lists *are* the benchmark, and
  editing them would silently invalidate every comparison already recorded.
  `update_case` no longer flips a never-synced case to `modified_since_sync`; a
  case that lives only in the DB stays `not_synced`, because a drift alarm about
  a YAML file that never existed is noise.

- **The runner image is built from the Health panel.** A `build_runner_image`
  Celery task builds `Dockerfile.runner` on the **worker**, which is the only
  process holding the docker socket (ADR-0002) and whose `/app` bind mount is
  already the build context. The docker health row carries it as a heal action,
  and `RunService.preflight` refuses a docker-backed run when the worker reports
  the image absent — so the operator gets a message before enqueueing instead of
  a dead run in the history. Worker facts are cached for 30 s so the preflight
  does not pay a 3 s broadcast per run in a batch.

## Consequences

Easier: create → edit → run is a closed loop in the browser with no CLI step.
Any case type an operator can author is runnable, and the `not_synced` badge
stops meaning "broken" — it correctly means "never exported to YAML", which for
a DB case is the normal state.

Harder: `case_spec.json` is now on the critical path for *every* run, not only
SWE-bench ones, so a bug in `_write_case_spec` breaks Tetris-adjacent cases too.
The stale-spec unlink hygiene matters more for the same reason.

Unchanged by design: the CLI lane. `uv run python scripts/bench_run.py --case
cli-textstats` still exits with the unknown-case message. `benchmarks/cli-textstats`
becomes runnable by importing it as a case and running it from the web, which is
the direction this ADR chooses. Scoring and validation are untouched —
`build_validation_spec` already read `case.validation_commands` from the DB, and
the SWE-only validators are reachable only through `validation_commands`, which
only the two SWE importers populate.

## Alternatives considered

- **Auto-sync every case to `benchmarks/` and discover `RUNS_FILES` from the
  directory** — rejected. It writes into the repo tree on every case edit, makes
  the working copy dirty as a side effect of using the UI, and reintroduces the
  `runs.yaml` write race that `resolve_or_synthesize_run` explicitly documents
  avoiding.
- **A second, non-SWE spec file** — rejected. Two channels doing the same job,
  with two sets of staleness rules.
- **Let the run fail and explain the image in the error** — rejected as the
  whole fix. It is the status quo: a failed run in the history for a condition
  one button repairs. The reworded error stays as the last line of defence.
- **Build the image from the API process** — impossible without giving the API
  the docker socket, which ADR-0002 forbids.
