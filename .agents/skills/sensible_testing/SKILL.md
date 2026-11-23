---
name: sensible_testing
description: "Design, review, and audit automated tests and benchmark graders for meaningful bug detection. Use when assessing test quality, adding regression coverage, finding missing failure cases, or evaluating whether existing tests justify their cost."
---

# Sensible testing

A test earns its cost by detecting a plausible defect in the system's behavior.
Name that defect and the independent evidence that distinguishes correct from
incorrect behavior. Coverage and test counts describe execution, not correctness.

## Scope and discovery

Use this guidance for application tests and benchmark graders. Distinguish them:
application tests check the evaluator and its workflows; benchmark checks assess
an evaluated agent's output. Neither is evidence that the other works.

Read the repository's test commands, fixtures, markers, dependency setup, and
relevant contracts before editing. Follow its own tooling and governance rather
than importing commands or assumptions from another project. Reuse an existing
test-first workflow for behavior changes; this skill assesses test usefulness.

## Questions for each test

- Which plausible defect would make it fail, and what user-visible or contractual
  behavior does that defect violate?
- Is the asserted result independent of the code under test, or computed by the
  same implementation? Prefer a hand-computed result, contract, trusted reference,
  or independently derived invariant.
- Does it exercise our transformation, boundary, configuration, or integration,
  rather than restating a library default or an assignment?
- What negative case, boundary, ordering, or environment difference matters?
- Does another test already catch the same defect? Similar-looking tests can
  protect different callers, historical regressions, or failure mechanisms.
- Is the fixture and runtime cost justified by the risk being checked?

Do not require a unit test for every trivial function. Do not remove tests solely
because they pass, look simple, overlap coverage, or lack a known historical bug.
Preserve distinct regression evidence and contract guarantees.

## Evidence-driven audit

1. Inventory the requested suite: entry points, domain contracts, shared fixtures,
   expensive dependencies, and skipped or uncollected tests. Group tests by risk;
   drill into individual cases when assessing a proposed change.
2. Classify findings as keep, strengthen, remove, or add. For a removal, explain
   which remaining assertion protects the behavior, or why no meaningful behavior
   was asserted. Distinguish confirmed findings from unprobed suspicions.
3. Probe important or questionable assertions with a small deliberate defect in
   an isolated copy or worktree. Examples: remove a filter, ignore a subprocess
   failure, reverse a comparison, omit a condition field, or drop a state update.
   The relevant test should fail because of that defect. Restore every mutation;
   never apply probes to a shared deployment or leave them in the contribution.
4. Add the smallest fixture and assertion that expose a missing risk. Mock external
   I/O at its boundary while preserving the computation or orchestration being
   tested. Exercise a real adapter separately when its behavior is the risk.
5. Run focused tests during edits, then the repository's required gates. Separate
   deterministic unit/service tests from live infrastructure and model calls.
   Report skipped checks and prerequisites; do not convert absence into success.

Mutation probes are selective evidence, not a requirement to mutate every line.
When a probe is impractical, state the limitation instead of claiming it was run.

## Common weak tests

- Comparing a constant with a copied literal without checking a contract that
  depends on it; setting a field and asserting the assigned value.
- Round-tripping an unmodified framework model without custom conversion behavior.
- Replacing the behavior being evaluated with a mock that already returns the
  expected answer; asserting only that the mock was called.
- Generating expected values with the same function or formula under test.
- Checking statistical self-consistency without testing against a known truth.
- Treating screenshots, snapshots, test counts, or coverage alone as correctness.
- Depending on live credentials, current dates, uncontrolled randomness, shared
  state, or an assumed checkout layout in a unit test.

These are review signals, not automatic deletion rules. A snapshot, round trip,
mock interaction, or exact string can protect a meaningful public contract.

## Report

Summarize risks reviewed; tests kept, strengthened, removed, or added; the defect
each change addresses; mutation probes and results; focused/full checks; runtime
impact when measured; and gaps or skipped validation. Distinguish a complete
inventory from a deep audit of selected risks. Do not describe a sampled review
as exhaustive proof of the suite's quality.

## Domain-specific checks

Select relevant risks from this catalogue. Do not manufacture a test for every
row. Extend it when a new task's observable behavior needs a different oracle.

### APIs, services, persistence, and asynchronous work

- Check request parsing, authorization, invalid input, cross-field rules, and
  response/status contracts through the real boundary where mapping is the risk.
- Check transaction rollback, uniqueness, referential integrity, pagination, and
  idempotency. A mid-batch failure should expose partial persistence if the public
  contract promises all-or-nothing behavior.
- Exercise out-of-order events, retries, duplicate delivery, cancellation, worker
  restarts, stale leases, and terminal-state transitions with controlled clocks.
- Use integration tests where a database dialect, broker, filesystem, or process
  boundary matters. In-memory substitutes do not prove production semantics.

### CLI tools, hooks, containers, and configuration

- Non-zero child exits, collection failures, missing tools, and empty results must
  have the intended exit status and diagnostic; never test only printed success.
- Check environment precedence, inherited process state, checkout location, paths
  with spaces, working directories, and repository/index isolation.
- Verify generated references resolve and selected configuration reaches every
  relevant launch, clone, retry, resume, and reporting path.
- Exercise real command construction and dispatch with controlled external
  boundaries; do not mock the orchestration being tested.
- Separate simulated adapter tests from real container/network checks and name
  which environment actually ran. Test resource limits and cleanup when relevant.

### Frontend, accessibility, and interactive 3D websites

- Test user-visible interaction, keyboard/focus flow, validation, state updates,
  persistence, responsive behavior, and safe rendering of untrusted text.
- Prefer semantic browser queries and observable outcomes over private component
  structure. Test loading, disconnected, empty, and error states where meaningful.
- For animation and 3D, check scene readiness, controls, resize, reduced motion,
  and fallback behavior without relying only on pixel identity.
- Treat aesthetic quality as a declared judgement with a rubric, rater provenance,
  and agreement checks. Functional tests do not establish visual creativity.
- Record renderer, viewport, hardware, and timing conditions for performance or
  screenshot comparisons; separate environment differences from regressions.

### Data transformations and machine learning

- Use small hand-computable fixtures for filters, aggregation, normalization,
  missing values, schema changes, empty input, ties, and out-of-order records.
- Check temporal cutoffs, split/key disjointness, preprocessing fit scope, and
  held-out evaluation to detect leakage.
- Check join cardinality, unmatched keys, duplicate handling, and idempotent writes.
  A test fixture that rewrites names can hide environment-resolution bugs.
- Assert meaningful dtype, nullability, range, and artifact reload contracts.
  Use explicit numerical tolerances and controlled random seeds where applicable.
- Compare trained results against a justified baseline or held-out threshold.
  A model that trains without raising has not necessarily learned the task.

### Mathematics, reasoning, and statistical analysis

- Use exact or independently calculated examples, including zero denominators,
  degenerate samples, ties, missing values, and extreme boundaries.
- Verify problem constraints and solutions across varied instances; a persuasive
  explanation is not an oracle. Keep tool-assisted and unaided tracks distinct.
- For uncertainty or power claims, simulate data with known truth and measure the
  stated property. An interval containing its own estimate does not prove coverage.
- Match the sampling unit and estimand: clustered cases, repeated trials, paired
  observations, weighting, exclusions, and effect direction can change conclusions.
- Check invariance to irrelevant input ordering and reproduce seeded calculations.
  Separate fast deterministic regressions from costly statistical calibration.

### Knowledge, grounded factuality, and creative outputs

- Freeze the evidence/reference set and declare domain, date, and tool access.
- Distinguish false claims, unsupported claims, missing answers, and appropriate
  abstention. Report answer coverage alongside factuality so silence cannot win.
- Verify references against the supplied evidence; valid-looking citations alone
  do not establish support. Closed-book recall is a different instrument.
- For open-ended outputs, combine objective constraints with a declared judgement
  rubric. Record the actual judge configuration when observable, disagreement,
  rubric version, and unavailable scores rather than replacing them with zero.

### Games, simulations, and interactive planning

- Version rules, physics, observation/action interfaces, seeds, opponents, and
  decision budgets. Separate building the environment from acting within it.
- Test illegal actions, collision/rule invariants, resets, deadlines, terminal
  conditions, and full trajectories as well as isolated mechanics.
- Score authoritative state and event logs: goals achieved, violations, wasted
  resources, and excess actions. Calibrate with a simple baseline policy.
- Distinguish simulation time, real elapsed time, rendering overhead, and model
  latency. State whether simulation pauses while the model decides.
- Logic-only and rendered execution establish different properties. Exercise the
  actual scene and input path when rendering or user control is part of the task.

### Model serving, evaluation provenance, and grading

- Check requested versus served model/settings, capability mismatches, malformed
  tool calls, timeouts, partial streams, reload behavior, and resource contention.
- Use captured or synthetic responses for deterministic adapter tests; separately
  validate protocol assumptions against a real server when authorized.
- Ensure task, prompt, model, runtime, harness, and grading versions identify the
  recorded condition. Missing fields must not silently merge different conditions.
- Calibrate graders on empty, known-good, and partial artifacts in the runtime
  where grading occurs. Do not trust the evaluated agent's own tests as the oracle.
- Keep held-out facts independent from agent-visible artifacts. A filesystem mask
  is not proof that committed evaluation material is secret.
- Distinguish agent failure from infrastructure failure and unmeasured outcomes.
  Test that aggregates disclose exclusions and compare compatible instruments.

### Security and privacy boundaries

- Probe path traversal, archive extraction, secret redaction, authorization, and
  attacker-controlled text where those are part of the application contract.
- Use synthetic secrets and identities. Assertions should verify that prohibited
  material stays absent from exported artifacts while useful evidence survives.
- Keep credentials, private datasets, internal locations, and identifying project
  details out of fixtures and public audit reports. Test cleanup after failures.
