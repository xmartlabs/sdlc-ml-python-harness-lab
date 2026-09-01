# Known limitations

Things this repository does not do, or does less completely than the surrounding
documentation might suggest. They are accepted debt, not oversights: each one is
recorded here so it is discovered before a clone rather than after.

For the properties that are *inherent* to running a benchmarking lab — untrusted
model output, credentials in run containers, host-root reachability — see
[SECURITY.md](../SECURITY.md). Those are design, not debt.

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
zero-config through the lab's own gateway — `DEEPEVAL_MODEL` overrides it and
`DEEPEVAL_MODEL=off` is the cost lever. Host-mode development still needs
`uv sync --extra deepeval`, and a plain `uv sync` removes it again — that
remains the price of keeping it an optional extra.

### Runs from before scoring existed have no scores

Every terminal run is now scored automatically, and re-validating rescores. Runs that
finished before that landed carry none until someone opens them and presses **Score
this run**; they are absent from the corpus aggregate and from any experiment summary
until then. The Dashboard's "worth attention" panel counts them.

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

`repo_root` defaults to `Path.cwd()`, so **the harness under test is the repository
the lab runs from**. Selecting a harness in the UI picks which CLI is invoked, not
which governance surface is loaded. Testing a different harness means cloning it
and running the lab inside it.

Partly lifted for runs against an external target
([ADR-0031](adr/0031-harness-source-is-a-run-condition.md)): `harness_source` picks
between the lab's harness projected into the target (`injected`) and the target's own
conventions (`native`), which is enough for a harness A/B on someone else's
repository. What remains true is the general statement: a harness sourced from a
*third* repository is still not supported.

The audit half of that gap is closed. Each run is now judged against the governance
surface it actually had — the target's own for a `native` arm, the injected harness
for an `injected` one — and the surface's source, file list and fingerprint are
persisted with the audit
([ADR-0036](adr/0036-audit-judges-a-run-against-its-own-governance.md)). Audits
recorded before that carry no surface and say so rather than being backfilled.

This is the deliberate narrowing described in
[the readiness plan](plans/2026-08-open-source-readiness-and-batch-runs.md), and it
is the prerequisite for most of the "out of scope" items there. The *target* side of
that coupling — running a case against an arbitrary external repository — is
planned in [the v2 roadmap](roadmap-lab-v2.md#phase-1--targetrepository); the
harness side stays out of scope there too. It is stated in the
README as well, because it is the limitation most likely to surprise someone who
expected a harness-agnostic platform.
