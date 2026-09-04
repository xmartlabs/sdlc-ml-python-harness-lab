# ADR-0042: The lab reads a repository through the engineer's own checkout

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Marcos Soto
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0030](0030-one-workspace-source-interface.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0032](0032-cases-are-derived-from-a-targets-own-history.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md)

## Context

Auditing somebody else's repository is the lab's third use case, and the one that
worked least well. Two problems, one technical and one about shape.

**The clone had nowhere to get credentials.** A target repository was cloned inside the
run container, which deliberately runs with `GIT_TERMINAL_PROMPT=0`, no ssh agent and
no tokens (ADR-0029 keeps the lab out of the credential business). That is correct for
a public repository and fatal for every private one — which is every repository worth
auditing for a client. The obvious fix, teaching the lab to hold credentials, is the
one thing we do not want: a lab holding a client's git credentials is a liability that
has to be secured, rotated and explained.

**The flow was a dead end.** The audit was a six-step wizard: pick a repository, read
its readiness, derive cases, run discovery, review findings, turn one into an
experiment. At the end nothing remained to come back to. Cases were associated with
the repository only through a tag; the readiness report was a transient view; and
re-testing the same case with a different skill meant walking the whole wizard again.
An audit is not a transaction, it is a relationship with a codebase you return to.

## Decision

**The engineer clones the repository themselves, in their own terminal, and the lab
reads that clone.**

1. The GUI hands over the exact command (`git clone <url> <lab>/data/checkouts/<id>`),
   watches the directory, and reports what it finds. It never clones, pulls or writes
   there: it is somebody's working copy.
2. Pinning records `checkout_path` and `pinned_commit` on the target. From then on
   every reader — the runner's provisioning, readiness detection, case derivation, ref
   resolution — goes through `clone_source(target)`, which returns the local path when
   there is one and the remote URL otherwise. Git takes a path anywhere it takes a URL,
   so nothing downstream needed a second code path, and a target registered before this
   existed keeps working unchanged.
3. Local paths are accepted as a clone source only under `data/checkouts/`, and a
   target id is validated as a single safe path segment. Without both, a target id
   would be able to name any git repository on the host.
4. **A gate check** runs the repository's own validation commands at the pinned commit,
   in the runner image, with no agent, no prompt and no model — and records which phase
   failed (provision / setup / validate).
5. The wizard becomes a **three-step onboarding** that ends when one case passes
   against the repository, and each repository then gets a **workbench page** where its
   checkout, readiness, case library, experiments and report accumulate. Cases carry
   `origin_target_id` instead of relying on a tag.
6. A `native` arm that stages skills has them **placed in the workspace**, without an
   adapter file, agents, governance documents, or removing anything the repository
   already ships under its own skills directory. Under `native` the agent's cwd is the
   clone, so a skill left in the lab is invisible to it — the arm would have equalled
   its own control while appearing to differ.

## Consequences

**A private repository is now auditable at all**, with no credential anywhere near the
lab or a container. This is the change that unblocks the use case.

**Runs stop touching the network for source.** Provisioning clones from a local path, so
a run is no longer at the mercy of the remote being reachable, and cloning a large
repository twenty times costs local disk reads.

**The commit stops moving.** A pinned target answers `_resolve_target` without an
`ls-remote`, so "the same arm, another trial" cannot silently slide onto whatever was
pushed since. Re-pinning is explicit, and the previous pin stays on the historical runs.

**A failed run becomes readable.** Before the gate check, a red run against an unfamiliar
repository was ambiguous between "the agent failed" and "this repository does not build
in the evaluation image" — indistinguishable in the output and opposite in meaning. The
gate check answers that once, cheaply, before any model spend.

**Uncommitted work is invisible to runs**, because provisioning clones git objects. The
lab reports a dirty tree as a warning rather than a blocker: it is the engineer's desk,
and refusing to work while they have edits open would be an odd thing for a tool to do.

**The lab now holds a copy of client source on disk.** `data/checkouts/` is gitignored
and is the operator's own clone, but it is real client code sitting in the lab's data
directory, and deleting it is a manual act. That is the cost of not holding credentials,
and it is the cheaper of the two.

**"Their repo, plus this skill" is now a real arm.** Before, `native` plus
`skills_extra` was a condition the lab would happily hash, launch and report on, and
the agent never saw the skill — the same class of silent non-treatment ADR-0041 was
written about. It is also the arm a consultancy most wants: a client's repository, as
it is, with one governed skill added and nothing else changed.

**A target can drift from its remote.** The checkout is whatever the engineer last
pulled; the lab does not know what `origin/main` says. That is deliberate — the pin is
the point — but it means "audited at the latest commit" is a claim only the engineer can
make, not one the lab can verify.
