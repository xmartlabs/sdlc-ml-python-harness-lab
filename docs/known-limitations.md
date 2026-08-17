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

### `suite.yaml` is not wired to the batch endpoint

`benchmarks/harness-regression-v1/suite.yaml` already models cases × repetitions
with declarative gates, and is reachable only from the lifecycle CLI. It should
become a *producer* of batches rather than a second execution path. Deferred, not
rejected — see ADR-0023.

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

This is the deliberate narrowing described in
[the readiness plan](plans/2026-08-open-source-readiness-and-batch-runs.md), and it
is the prerequisite for most of the "out of scope" items there. It is stated in the
README as well, because it is the limitation most likely to surprise someone who
expected a harness-agnostic platform.
