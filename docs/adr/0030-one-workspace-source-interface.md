# ADR-0030: Provision every workspace through one source interface

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0009](0009-swebench-case-type.md),
  [ADR-0026](0026-run-continuation-as-child-runs.md),
  [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [roadmap-lab-v2.md](../roadmap-lab-v2.md)

## Context

`provision_workspace` in [bench_run.py](../../scripts/bench_run.py) already serves
three different ways of producing the directory an agent works in: a bare workspace
(build-from-scratch cases), a git clone at `base_commit` with a test patch applied
(SWE-bench), and a copy out of a prebuilt docker image (SWE-bench Pro, pre-provisioned
worker-side because the runner has no docker socket). Continuations add a fourth
path — `_seed_continuation_workspace` copies the parent's final workspace before the
run starts ([ADR-0026](0026-run-continuation-as-child-runs.md)).

Each arrived separately and branches inside the same function on `spec["case_type"]`.
Adding external target repositories ([ADR-0029](0029-target-repositories-pinned-per-run.md))
would add a fifth branch, and harness injection a sixth. The failure mode of that
shape is not ugliness: it is that "which lane am I in" gets decided in several places
that can disagree, and cleanup/finalization only ever gets fixed for the lane whose
bug was noticed.

## Decision

We will express workspace production as **one interface with several
implementations**, rather than one function with several branches:

```text
WorkspaceSource.materialize(workspace, spec) -> None
```

with `LocalWorkspaceSource` (bare / lab-repo, the current default),
`GitWorkspaceSource` (an external target at a resolved SHA), `SweBenchWorkspaceSource`
(clone + test patch) and `SweProWorkspaceSource` (docker image extraction).

Selection is by an explicit `source` discriminator carried in `case_spec.json`, not
inferred from the shape of the data. `case_type` remains what it is — a property of
the task — and stops doubling as a provisioning strategy.

**The local repository stays a supported source, not a legacy path.** It becomes
`LocalWorkspaceSource` and goes through the same interface as everything else. This
is the property that keeps the current workflow working while preventing the lab
from acquiring a second execution engine for external targets.

Every implementation shares one post-condition: the provisioned state is **committed**
before the agent runs, so `git diff HEAD` isolates the agent's changes. That is
already how the SWE lanes work; making it part of the interface is what lets diff
collection, validation and finalization stay lane-agnostic.

## Consequences

A new lane becomes an implementation with its own tests instead of a branch inside a
function that four other lanes depend on. Harness injection (a later phase) becomes
a step layered on a source rather than a fifth thing `provision_workspace` must know
about.

The interface is introduced **incrementally**: the discriminator and the new git
source land first, with the existing branches kept behind it and migrated as each
gains a test. A big-bang rewrite of a function that four working lanes depend on
would risk the one thing this repo cannot cheaply verify — real provisioning against
real repositories.

Cost accepted: an indirection where a reader previously saw one function, and a
discriminator that must be written correctly by the worker for the runner to act on
it. The latter is the same trust boundary `case_spec.json` already carries
([ADR-0009](0009-swebench-case-type.md)) — the runner has no DB, so the worker
staging the spec is the only channel.

## Alternatives considered

- **Keep branching on `case_type`.** Rejected: `case_type` describes the task, and a
  target repository is orthogonal to it — the same `repo_task` case should be able to
  run against a local workspace or an external clone. Overloading one field to mean
  both makes that combination inexpressible.
- **Move all provisioning to the worker.** Rejected: the worker has the docker socket
  but the runner has the network and the git binary in a reproducible image; SWE-bench
  provisioning already lives in the runner deliberately. Moving it would make every
  lane depend on the socket-holding process.
