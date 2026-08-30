# ADR-0032: Derive organizational cases from a target's own history, deterministically

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0025](0025-db-is-the-source-of-truth-for-every-case.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [roadmap-lab-v2.md](../roadmap-lab-v2.md)

## Context

[ADR-0029](0029-target-repositories-pinned-per-run.md) made it possible to run any
case against any repository. It did not make that meaningful: running "build a Tetris
clone" against a payments API measures nothing, and the harness comparison of
[ADR-0031](0031-harness-source-is-a-run-condition.md) is only as informative as the
task both arms are given.

A case is only evidence about a team's work if it *is* their work. That leaves the
question of where such cases come from, and the obvious answer — ask a model to
invent "a typical task for this repository" — has a specific failure: a model that
knows which harness is about to be evaluated will produce a task that suits it. The
bias is invisible in the result and fatal to the claim.

## Decision

We will mine candidate cases from the repository's **commit history**, using git and
path heuristics only, and give them the `organization` provenance.

**A candidate is a commit that changed source and its tests together.** That pairing
is the signal: it is a task the team actually performs *and* already knows how to
verify. Merges, reverts, version bumps and chores are dropped, and so is any commit
touching more source files than one task plausibly does — a forty-file commit is a
release, not a case.

**No model is in the loop.** A proposal a human must edit anyway does not need to be
fluent, and determinism is what makes the selection rules reviewable. They live in
`case_derivation.py` as named predicates with their own tests, so "what counts as a
case" is a decision on the record rather than a prompt's mood.

**Nothing is persisted by derivation.** Candidates are proposals. A derived case is
only worth running if the team recognises it as their work, and that judgement is not
the lab's to make.

**A candidate carries the commit it came from**, and the suggested case id embeds the
short sha. This is what lets someone check later that a case predates the results
rather than having been written to fit them.

**The suggested prompt states the task and the surface, and never quotes the original
diff.** A case that hands over the answer measures transcription.

`Case` also gains `files_expected` and `files_forbidden`. These are distinct from the
existing `allowed_files`, which is an agent-facing permission: these two are the
*measurement*. A solution that edits a forbidden path is wrong even when the tests
pass, which is exactly the failure an organizational benchmark exists to catch.

## Consequences

Mode B and the `native`/`injected` comparison become interpretable on a client
repository, because the task is theirs.

The mining reads a **blobless bare mirror** (`--filter=blob:none`), cached under
`data/target-mirrors/`. It needs commit metadata and file *names*, never contents;
fetching every blob to list paths would make this unusable on the large repositories
that matter most. The cache is disposable — deleting it costs one slower derivation.

Costs accepted:

- **Heuristics are wrong sometimes.** They are tuned to over-produce: a false
  positive costs a candidate a human discards, a false negative silently hides the
  commits worth mining. This is a deliberate asymmetry, not sloppiness.
- **A repository whose tests do not live in recognisable paths yields nothing.** The
  patterns cover the common Python/JS/Go/Ruby shapes; an unusual layout needs a new
  predicate, which is a code change with a test — the right amount of friction.
- **Freezing the case before running is a discipline, not an enforced constraint.**
  The recorded commit lineage makes a violation *detectable*, which is as far as the
  tool can honestly go.

## Alternatives considered

- **Ask an LLM to propose cases from the repository.** Rejected as the primary
  mechanism for the authoring-bias reason above. It remains a reasonable *second*
  pass over deterministically selected candidates — turning a commit subject into a
  better-worded task — because at that point the selection is already fixed.
- **Derive from open issues or PR descriptions.** Attractive and deferred: it needs a
  forge API and credentials per host, where git history needs only the clone URL the
  lab already has. The same selection rules apply once that plumbing exists.
- **Use the commit's own diff as the expected patch (SWE-bench style).** Rejected for
  the general case: it turns every derived case into a ground-truth benchmark, which
  requires the test suite to fail before the change and pass after — true for
  bugfixes, false for most feature work.
