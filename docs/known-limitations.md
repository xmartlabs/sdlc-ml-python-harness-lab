# Known limitations

Things this repository does not do, or does less completely than the surrounding
documentation might suggest. They are accepted debt, not oversights: each one is
recorded here so it is discovered before a clone rather than after.

For the properties that are *inherent* to running a benchmarking lab — untrusted
model output, credentials in run containers, host-root reachability — see
[SECURITY.md](../SECURITY.md). Those are design, not debt.

## Measurements archived before 2026-10-21

Two defects in `scripts/bench_run.py`, fixed on 2026-10-21, bias every run archived
before that date. Read any earlier comparison of cost, steps or time with them in mind.

### Build-from-scratch runs stopped at step 2

`workspace_fingerprint` asked git for status and diff inside a workspace with no
`.git` of its own. git answered for the lab repository, where `/workspace/` is
ignored, so the fingerprint never changed and the loop stopped as `no_progress`
after step 2 whatever the agent had done — 36 archived runs. A bare arm that
signalled completion at step 1 and a harness arm cut at step 2 therefore stopped
for different reasons, which makes the earlier "the harness costs more" readings on
`tetris-v2` and `cli-textstats-v1` unreliable. Such a workspace is now fingerprinted
by its file tree.

### Cost lost when usage was merged

The usage merge let a remote source that returned `cost_usd: None` overwrite the
cost parsed from Claude's own `result` events: 37 archived claude runs show no cost
while their events report $56.09 in total — more than the $47.57 the lab had
recorded. `merge_usage` now never replaces a value with an empty one. Archived
`usage.json` files were **not** rewritten; recompute a run's cost from
`collect_claude_usage(<run dir>)` before using it.

## Batch runs

### `create_batch` is not transactional

[ADR-0023](adr/0023-batch-run-execution.md) says validation is all-or-nothing, and
it is: the cross-product is fully resolved, sized and preflighted before any row is
written, and nothing is published until every row exists.

The guarantee stops at the persistence loop. `RunService.create_batch` commits per
run rather than in one transaction, so a failure *during* persistence — a database
error, a lost connection — can leave the rows written so far committed and the rest
absent. Those rows are never published, so they cannot start; they sit in `pending`
until deleted by hand.

The window is narrow and the failure mode is inert rather than destructive, which is
why it is accepted. Closing it means wrapping the loop in a single transaction and
deciding what `_next_run_id` should do on rollback.

### `repetitions` has no upper bound of its own

The cross-product size is capped by `HARNESS_LAB_MAX_BATCH_RUNS` and checked
arithmetically before expansion, so an oversized request costs six integer
multiplications and a `422`. But `repetitions` itself accepts any positive integer,
so the rejection message can quote an implausible number. Harmless; noted so the
absence of a field-level bound is not read as an oversight.

### `suite.yaml` produces experiments, not batches

`benchmarks/harness-regression-v1/suite.yaml` models cases × repetitions with
declarative gates. It is now a *producer* of experiments rather than a second
execution path ([ADR-0037](adr/0037-regression-suite-produces-experiments.md)) — it
was never wired to the batch endpoint, and no longer needs to be: an experiment is
the unit above a batch ([ADR-0033](adr/0033-experiment-above-run.md)). The lifecycle
CLI's own suite path still exists alongside the API route.

## Run failure reporting

### The "endpoint down" verdict can misreport its error count

When the watchdog stops a run after `run_max_stream_errors` consecutive provider
failures, the loop keeps draining the lines already in flight — and the first
non-error line among them resets the very counter the verdict is about to quote.
Run `…ansible…-K-015` was stopped after 5 back-to-back failures and its stored
reason reads "0 provider requests failed back-to-back". The verdict itself is
correct and the run is properly excluded from the leaderboard; only the number in
the message is wrong. The fix is freezing the count when the verdict forms
(reporting the peak, not the final value) in `tasks._stream_run`.

### Most models declare no completion-token cap

A `gateway/*` entry in `opencode.json` that carries only a display name leaves
opencode to pick the `max_tokens` it sends for an unknown model (observed:
32000, in `…qutebrowser…-K-003`). Providers that enforce a smaller completion
cap — OpenAI's `gpt-4o` and `gpt-4o-mini` reject anything above 16384 — refuse
every request before a token is generated.

The six OpenAI entries now declare a `limit` block with the real numbers, read
from the gateway's own `/model/info`. The remaining ~39 entries do not. That is
tolerable rather than fixed: the ones in use accept 32000 today, and nothing
keeps a declaration honest when a provider changes a cap.

Two guards surround what is left. The preflight probe sends the same completion
budget the run will carry — the declared `limit.output` when there is one,
opencode's default when there is not — so "validate config" and run creation
fail fast with the provider's own numbers. And a run that slips through anyway
is voided as **endpoint down** rather than recorded as a completed attempt (see
[guide-web-ui.md](guide-web-ui.md)).

Note which number is which: for `gpt-4o-mini` the 16384 is the *output* cap,
fixed by OpenAI and not raisable at any layer. Its context window is 128000,
comfortably past the harness minimum of 32768 (`MIN_CONTEXT_TOKENS`).

### A billing failure is reported as an unreachable endpoint

An exhausted account answers HTTP 429 `You have no credits remaining` to every
request, including the preflight probe. That falls through the probe's marker
lists to "ambiguous", so "validate config" reports green; the run then dies on
five back-to-back stream errors and is correctly voided (`…qutebrowser…-K-005`).
The verdict is right and the provider's sentence is stored verbatim in the
failure reason, but the advice above it — check reachability, model id and
context window — sends the reader to the wrong place. A quota marker set
alongside `_COMPLETION_CAP_MARKERS` would let both layers name billing directly.

### A Claude model the gateway does not serve is caught only by id

In Docker the `claude` harness reaches Anthropic through the gateway
(`ANTHROPIC_BASE_URL`), so a model without a gateway card dies on its first
request with `Invalid model name`. Experiment `con-o-sin-harness` (2026-10-23)
lost eight runs of `claude-haiku-5-5` that way, each listed as an "agent error".
Preflight now asks the gateway and refuses such a model before anything is queued,
the forms mark it "not on your gateway" with a warning under the picker, and the
run list labels the old failures `model not served` / `harness error` and keeps
them out of the counts. Two gaps remain: only the ids in `native_models.py` are
checked (an id typed by hand is not), and a gateway that does not answer is
treated as unknown, so the run is allowed.

The same day, two `claude-opus-5-5` runs (K-006/007) — a model the gateway does
serve — died on `claude_code_version_too_old`: the runner image's unpinned
`npm install` had stayed on a cached Claude Code 2.1.197. The CLI is now pinned and
labelled, Health and preflight read the label, and a harness error prints a short
`Cause:` line last so the stored failure reason names it. Images built before the
label existed report no version and are not blocked; `MIN_CLAUDE_CLI_VERSION` holds
only floors the API has stated.

### The batch endpoint has no HTTP-level test

`tests/harness_lab_app/test_run_batch.py` covers `RunService.create_batch`
thoroughly — expansion, per-case variant filtering, cap ordering, preflight
deduplication, all-or-nothing persistence. It does not exercise
`POST /api/runs/batch` itself, so the request/response mapping (including the
`422` translations for `RunPreflightError` and `RunBatchTooLargeError`) is covered
by inspection only.

This is partly environmental: `fastapi.testclient` requires `httpx`, which the
current lockfile does not provide, so `TestClient` raises on import. Adding it is
the prerequisite for closing this gap.

## Lab v2 (experiments, scores, findings)

The [v2 roadmap](roadmap-lab-v2.md) is implemented. These are the parts that landed
deliberately short, so the gap is discovered here rather than in use.

### DeepEval covers three metrics, and only three

`task_completion`, `step_efficiency` and `tool_correctness` are wired: a run's
recorded trajectory is mapped onto DeepEval's test-case shape and the results are
persisted with their evaluator, its adapter version, and the rubric or judge model
used. Two of the three are `GEval` rubrics rather than DeepEval's own metrics —
DeepEval's task-completion metric reads a *live* trace and the lab evaluates archived
runs, and step efficiency has no DeepEval metric at all. Both are declared
`judgement` in the registry, which is what they are. Plan quality and plan adherence
are **not** wired, deliberately — the lab owns the
evaluation model and DeepEval is an adapter to it, so each metric is added only when
the lab can say what it means.

A run whose trajectory carries too little signal returns **no score**, never a zero:
"not measured" and "scored badly" are different claims, and an aggregate must never
confuse them.

The metrics ride in the **default scoring profile** (the graceful skips are what
make that safe), the Docker images ship the dependency, and the judge resolves
zero-config over whichever transport the lab has — the gateway when one is
configured, otherwise the Claude Code CLI
([ADR-0043](adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)).
`DEEPEVAL_MODEL` overrides the model and `DEEPEVAL_MODEL=off` is the cost lever.
Each judged score records which transport produced it, because two transports
mean two judges: scores measured under different ones are comparable only in the
loose sense that any two judges are, and nothing in the aggregate flags the
difference for you. Host-mode development still needs
`uv sync --extra deepeval`, and a plain `uv sync` removes it again — that
remains the price of keeping it an optional extra.

### Runs from before scoring existed have no scores

Every terminal run is now scored automatically, and re-validating rescores. Runs that
finished before that landed carry none until someone opens them and presses **Score
this run**; they are absent from the corpus aggregate and from any experiment summary
until then. The Benchmarks page's "worth attention" panel counts them.

### The regression suite runs through experiments, with one caveat

`POST /api/experiments/from-suite` turns a suite into an ordinary
`harness_evaluation` experiment — the suite supplies the cases and their per-case
repetitions, the caller supplies the two arms, and everything after that is the
existing design validation and case matrix
([ADR-0037](adr/0037-regression-suite-produces-experiments.md)). There is no second
runner.

What remains: the lifecycle CLI's own suite path still exists alongside it, and a
suite invocation that varies a field the suite controls is now *refused* rather than
silently accepted.

### Findings are authored by hand, on purpose

The corpus is mined for **suggestions** — regressions, cases that have never passed,
harness items the auditor keeps judging unused — and they appear in a review queue
labelled `suggested`. None of them becomes a finding without a person accepting it
and their name being recorded against it. A rejection is written down too, so the
same pattern is not re-raised next week.

Nothing here auto-promotes: a generated claim with nobody accountable for it is the
opposite of what a finding is for.

### A harness from a third repository is still unsupported

`native` (the target's own) and `injected` (the lab's, projected in) cover the harness
comparison. `target repo A + harness repo B + plugin repo C` remains out of scope —
see [ADR-0031](adr/0031-harness-source-is-a-run-condition.md).

## Local stack operations

### The database cannot be rewritten from the host while the stack runs

`data/app/harness_lab.db` is SQLite on a bind mount, opened concurrently by the
`api` and `worker` containers. SQLite coordinates concurrent readers through a
WAL and a `-shm` shared-memory index, and that index is not coherent across the
container/host boundary on macOS (virtiofs) — each side can believe it is the
only connection.

So a host-side command that rewrites the file — `make app-reset-db`, or
restoring a backup over it — while the containers are up leaves them reading
**different files**. The API keeps serving and writing to the file it already
had open; the worker reads the new one. Nothing errors:

- the UI accepts a new experiment and shows its runs as `pending`;
- Celery delivers `execute_run` and the worker logs `succeeded in 0.004s`,
  because `execute_run` looks the run up, gets `None`, and returns;
- the runs sit `pending` indefinitely, and the API's rows exist only in an
  unlinked inode that is lost the moment the container restarts.

`make app-reset-db` now refuses while `api` or `worker` are running. The safe
sequence is `make app-down && make app-reset-db && make app-up`. `FORCE=1`
bypasses the check and is only ever correct when the stack is genuinely down.

The same hazard applies to any host process that opens the database while the
stack is up, including an ad-hoc `sqlite3` session: closing it can unlink the
`-wal`/`-shm` the containers are still using. Read the database through
`docker compose exec api` instead, or stop the stack first.

Not fixed at the root. The structural fix is to move the database off the bind
mount onto a named volume (so WAL and `-shm` live on the VM's own filesystem),
or to stop sharing it between processes at all and have the worker reach the
API over HTTP. Both are larger than the guard, and the guard removes the silent
failure mode.

### The dev stack's UI is reachable from the LAN before a password is set

The API binds to `127.0.0.1:8009` by default (`HARNESS_LAB_API_BIND_ADDR`), but
the Vite dev frontend (`frontend/vite.config.ts`) listens on `0.0.0.0:5173` and
proxies `/api`. So under `make app-up`, anyone on the network who can reach `:5173`
can drive the lab — create runs, edit `.env` through Settings — until
`make set-password` is run. The always-on stack (`make app-up-prod`) is different:
nginx on `:8080` binds to `HARNESS_LAB_BIND_ADDR` (default `127.0.0.1`) and the
API stays on loopback. Not fixed at the root because the dev server's bind
address is what lets the compose network reach it; the guard is the password
([ADR-0015](adr/0015-single-user-session-auth.md)) and the banner the UI shows
until it exists.

### `make stop-docker` does not stop the dev stack

It stops a single container named `ml-python-base-server`, inherited from the
template, which this compose stack never creates. It is a no-op here and is
kept only for the template's own Docker target. Use `make app-down`.

## Packaging

### `pyproject.toml` metadata is minimal

`name`, `version`, `description`, `license`, `license-files` and `classifiers` are
present and the package builds with valid PEP 639 metadata. `authors`,
`[project.urls]` and `keywords` are absent. This is fine for a source distribution
and thin for a PyPI listing — the project is not published to PyPI today.

### The wheel ships two packages

`agentic-harness-lab` bundles both `harness_lab_app` (the lab) and
`ml_python_base.skills_sync` (the template engine the lab measures). They are
genuinely separate concerns and would be separate distributions in a larger
project. Splitting them would break the `template_sync` contract in
`adapters/registry.toml`, so they stay together deliberately.

## Harness coupling

`repo_root` defaults to `Path.cwd()`, so **for a `lab_root` run the harness under
test is the repository the lab runs from**. In that mode, selecting a harness in the
UI picks which CLI is invoked, not which governance surface is loaded — every CLI
reads this repo's own `.github/` governance.

A lab-native case can now be run against *nothing* as well:
`harness_source=bare` gives the agent an empty workspace and masks the lab's
governance inside the runner container, which is what a genuine control arm needs
([ADR-0049](adr/0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md)). It
needs the docker backend — on the host lane there is nothing to mask with. Two
gaps stay open there: the SWE lane's `swe_default` claude arm runs from the
workspace without declaring a condition, so it still inherits the lab's
`CLAUDE.md` from the parent directory; and `attribution._normalize_event_path`
strips the `/app/` prefix, so an in-target run's read of its *own* rulebook never
matches its surface while a read of the lab's does.

Runs against a registered target repository are different
([ADR-0029](adr/0029-target-repositories-pinned-per-run.md),
[ADR-0031](adr/0031-harness-source-is-a-run-condition.md)): there `harness_source`
is a per-run condition that picks between the lab's harness projected into the target
(`injected`) and the target's own conventions (`native`), which is enough for a
harness A/B on someone else's repository. What remains true in every mode: a harness
sourced from a *third* repository (target A + harness B) is still not supported.

The audit half of that gap is closed. Each run is now judged against the governance
surface it actually had — the target's own for a `native` arm, the injected harness
for an `injected` one — and the surface's source, file list and fingerprint are
persisted with the audit
([ADR-0036](adr/0036-audit-judges-a-run-against-its-own-governance.md)). Audits
recorded before that carry no surface and say so rather than being backfilled.

This is the deliberate narrowing described in
[the readiness plan](plans/2026-08-open-source-readiness-and-batch-runs.md). The
*target* side of that coupling — running a case against an external repository — is
implemented: the engineer clones the target into `data/checkouts/<target_id>` and the
lab pins the commit per run ([ADR-0029](adr/0029-target-repositories-pinned-per-run.md),
[ADR-0042](adr/0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md)).
The harness side — a third-repository harness — stays out of scope. It is stated in
the README as well, because it is the limitation most likely to surprise someone who
expected a harness-agnostic platform.
