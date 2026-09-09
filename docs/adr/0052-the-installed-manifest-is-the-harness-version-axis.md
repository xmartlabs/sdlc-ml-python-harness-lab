# ADR-0052: The installed manifest is the harness version axis

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0037](0037-regression-suite-produces-experiments.md),
  [docs/plans/2026-07-harness-continuous-improvement-cycle.md](../plans/2026-07-harness-continuous-improvement-cycle.md)

## Context

The continuous-improvement plan built releases (`releases.py`), candidate worktrees
(`sync.py`), gates and reports — and never wrote the one file they all read.
`.harness-version.yaml` did not exist, so `bench_run.read_template_ref` returned `""`
on every run, `metadata.template_ref` was empty across the corpus, and the
leaderboard's "harness version" axis silently fell back to a content hash. Nothing the
lab measured could be attributed to a release of `ml-python-base`.

## Decision

**`harness-adopt REF=vX.Y.Z` writes the installed manifest.** The new lifecycle
subcommand validates the ref is a release tag, resolves its commit on the template
remote, and writes `.harness-version.yaml` (`InstalledManifest`: repository, ref,
commit, channel, adopted-at, sync protocol). From then on every run stamps
`template_ref` from it, and the leaderboard and the overview trend group by it.

**Adopting is a record, not a sync.** It states which release the governance tree
*is*; the operator brings the tree there first (`harness-sync-branch`, merge). Rollback
is adopting the previous ref. A candidate-vs-installed comparison is two experiments in
one family, read by `template_ref` — a `harness_ref` *condition axis* inside a single
experiment would need the runner to mount a candidate worktree as the run's harness,
and is out of scope here.

## Consequences

The improvement loop closes at the cheapest possible point: change → release → adopt →
re-run the suite → compare by version. Historical runs keep their empty `template_ref`
and their hash fallback; nothing is rewritten.
