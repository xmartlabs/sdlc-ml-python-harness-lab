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

## Testing

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
