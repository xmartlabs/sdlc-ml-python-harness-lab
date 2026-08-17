# ADR-0023: Queue many runs per request as an all-or-nothing cross-product

- **Status:** Accepted
- **Date:** 2026-08-16
- **Deciders:** Marcos Soto
- **Related:** [ADR-0006](0006-auto-incremental-run-ids.md),
  [guide-web-ui.md](../guide-web-ui.md),
  [2026-08-open-source-readiness-and-batch-runs.md](../plans/2026-08-open-source-readiness-and-batch-runs.md)

## Context

The web lane created exactly one run per request: one case, one prompt variant, one
harness, one model. Every comparison the lab exists to produce needs more than
one — a harness A/B is two runs, a model sweep is four, and separating a real
difference from run-to-run variance needs each of those repeated. Producing a
2×2×3 grid meant twelve trips through the form, by hand, with twelve chances to
vary a field that was supposed to be held constant.

`benchmarks/harness-regression-v1/suite.yaml` already models the shape (cases ×
repetitions, with declarative gates) but is only reachable from the lifecycle CLI,
and it is fixed rather than composed ad hoc.

## Decision

`POST /api/runs/batch` takes one request whose array fields are axes — cases,
prompt variants, harnesses, executor models, planner models — plus a
`repetitions` count and the scalar settings every generated run shares. It
returns a `batch_id` and the generated run ids. `Run.batch_id` is a nullable
grouping label; `GET /api/runs?batch_id=` filters by it.

Three properties are load-bearing.

**A case is paired only with the prompt variants it defines.** The variant axis
is offered as the union across the selected cases — an intersection empties as
soon as one case is unusual — so a plain cross-product generates pairs that do
not exist. Those are not caught by preflight: they reach `scripts/bench_run.py`,
which exits only once a container is up, so an invalid combination costs real
setup time to discover something that was knowable before queueing.
`_resolve_variants` narrows the axis per case. A case that defines *none* of the
selected variants is rejected rather than dropped: filtering some combinations
of a case is a narrowing, dropping the case is a different request.

**Validation is all-or-nothing.** The whole cross-product is validated before
anything is persisted. A batch is a single intent: persisting combinations 1-16
and then rejecting 17 leaves a half-executed request that the user did not ask
for and now has to unpick — while the first sixteen containers are already
running and spending model budget.

**Preflight is deduplicated by what it actually reads.** `preflight` makes
network calls: gateway reachability and a context-window probe that can take ~40s
on a cold model. It consults `dry_run`, `harness`, `score_mode`,
`executor_model`, `skills_disabled` and `skills_extra` — not `case_id`,
`prompt_variant` or `planner_model`. A 24-run batch over two executors therefore
issues two probes, not twenty-four. `_preflight_key` encodes exactly that tuple
and is deliberately adjacent to `preflight`: **if preflight grows a dependency,
the key must grow with it**, or a batch will skip a check it should have made.

**The size is capped, and counted before it is built.** `HARNESS_LAB_MAX_BATCH_RUNS`
(default 50) is enforced server-side and surfaced through
`GET /api/settings/runtime` so the run form can show the count and block before
submitting. Reading the cap rather than mirroring it as a client constant means
the two cannot disagree. Four axes multiply into the hundreds from one careless
click, and every unit is a Docker container.

`_batch_size` computes the cardinality arithmetically so the cap is enforced
*before* expansion allocates one object per run. `repetitions` has no upper
bound of its own, so expanding first let a single request — unauthenticated, on
a default deployment — spend seconds of CPU building a list it was always going
to reject.

`resume` is absent from the batch schema. It resumes one specific workspace,
which is meaningless when the request asks for N fresh trials.

## Consequences

- The single-run path is unchanged. `POST /api/runs` still exists, still assigns
  ids the same way, and a batch with one entry per axis produces exactly what it
  would have. Batch mode in the UI is opt-in and seeds itself from the current
  single-run selection.
- Runs are published to the queue only after every row exists, so a batch is
  never half-queued. Persistence itself is not wrapped in a transaction, so a
  database failure *during* the write loop can still leave earlier rows
  committed — unpublished and inert, but present. See
  [known-limitations.md](../known-limitations.md).
- The stated run count can be lower than the product of the axes, because the
  variant axis is summed per case rather than multiplied. The UI computes it the
  same way, so the number on the button is the number that gets queued.
- `suite.yaml` is not yet wired to this endpoint. It should become a producer of
  batches rather than a second execution path — deferred, not rejected.
- Batch ids are opaque, so the runs table makes them searchable rather than
  offering a filter dropdown of unreadable identifiers.
