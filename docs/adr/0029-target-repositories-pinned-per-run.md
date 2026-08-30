# ADR-0029: Make the target repository first-class and pin every run to a resolved SHA

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0009](0009-swebench-case-type.md),
  [ADR-0025](0025-db-is-the-source-of-truth-for-every-case.md),
  [ADR-0030](0030-one-workspace-source-interface.md),
  [roadmap-lab-v2.md](../roadmap-lab-v2.md)

## Context

The repository an agent works in is currently a property of the **case**:
`Case.subject_repo` + `Case.base_commit`, consumed by `provision_workspace` in
[bench_run.py](../../scripts/bench_run.py). That is the right model for SWE-bench,
where the instance *is* a repository at a commit — the case and the repo are the
same fact.

It is the wrong model for everything else the lab now needs to do. Running one task
family across several repositories (Mode B) requires the same case paired with many
repositories. Auditing a client repository requires pairing *their* repo with a case
derived from their work. With the repo pinned to the case, both mean cloning the
case per repository, which duplicates the task definition and destroys the ability
to say "the same case, elsewhere".

There is a second, sharper problem. `subject_repo` is not consistently cloneable —
Tetris carries `ml-python-base` as provenance, not as a clone target — and nothing
resolves a moving ref. A run recorded against `main` is not reproducible: the commit
it actually used is unrecoverable the moment someone pushes.

## Decision

We will introduce **`TargetRepository`** as its own entity — URL, default ref, setup
commands, validation commands, and metadata — and pair it with a case **at the run**,
not at the case.

`RunConfig` gains three fields: `target_repo_id` (which repository), `target_ref`
(what the user asked for) and `target_commit` (**the resolved SHA**). All three are
empty for a run with no external target, which is every run that exists today.

**The ref is resolved to an immutable SHA at run creation, before anything is
persisted or enqueued.** `git ls-remote` is consulted once, in the API, and the
answer is stored on the config. A run therefore carries the commit it will use
before it starts, and two runs at different SHAs are — correctly — different
conditions.

`target_commit` enters `condition_hash`, following the rule already established for
skill ablation in [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md):
**only when non-empty**, so no historical arm re-bins.

The case keeps `subject_repo` / `base_commit`. For the SWE lanes those remain the
source of truth — the instance is the repo — and a target repository is simply not
used there. The two do not compete: a case may name a repo *or* a run may pair one,
and the SWE lanes are the case where the former is correct.

## Consequences

Mode B becomes expressible: one case, N repositories, the harness held fixed. Mode A
against a client repository becomes expressible at all.

Reproducibility improves for a class of run that previously had none. The cost is a
network call at run creation, on a path that already makes them (preflight probes
the gateway). A repository that cannot be reached, or a ref that does not exist,
fails **at creation with the git error**, rather than after a container is up — the
same principle ADR-0023 applied to invalid batch combinations.

Two things get harder. Run creation now has a failure mode that depends on a third
party's git server being reachable, which is a new way for a run to not start.
And `condition_hash` grows a term, so the rule about non-empty inclusion has to be
honored by every future field or the historical corpus silently re-bins.

Deliberately **not** decided here: whether a `Case` may declare that it *requires* a
target repository, and what an experiment does when a case and a target are
incompatible. Today an unsuitable pairing is the operator's responsibility.

## Alternatives considered

- **Keep the repository on the case; clone the case per repository.** Rejected: it
  duplicates the task definition N times, and two clones of a case are not
  detectably "the same case" for any later aggregation.
- **Resolve the SHA in the runner, at provisioning time.** Rejected: the condition
  would then be unknown until the run executes, so runs could not be grouped by arm
  before execution — and a batch of repetitions could silently straddle a push,
  producing runs that look like one arm and are two.
- **Store only the ref and treat `main` as good enough.** Rejected outright: it is
  precisely the property that makes a benchmark reproducible, and the lab's entire
  provenance story ([ADR-0003](0003-trial-provenance-and-harness-fingerprint.md))
  exists to avoid this class of hole.
