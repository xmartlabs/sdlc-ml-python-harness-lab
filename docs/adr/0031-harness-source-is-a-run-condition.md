# ADR-0031: Make the harness a condition of the run, not a property of the machine

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0011](0011-per-run-skill-ablation-and-staging.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [roadmap-lab-v2.md](../roadmap-lab-v2.md)

## Context

Until now the harness under test was the repository the lab runs from: `repo_root`
defaults to `Path.cwd()`, and selecting a harness in the UI picked which CLI was
invoked, not which governance surface was loaded. That was recorded honestly as
accepted debt in [known-limitations.md](../known-limitations.md#harness-coupling).

[ADR-0029](0029-target-repositories-pinned-per-run.md) made the *repository* a
run-level choice. That immediately raises the question the lab exists to answer for
someone else's codebase:

> What does our harness add over how this team already works?

Answering it needs two arms on the same repository, the same case and the same
model, differing only in the harness the agent can see. One of those arms is the
team's own conventions — which are not a harness the lab hosts, they are whatever is
already in their repo.

Two mechanisms already existed and were not connected. `agent_cwd` was set to the
workspace for one hardcoded case (a bare Claude arm on the SWE lane), because
"Claude Code auto-loads `CLAUDE.md` and `.claude/skills/` from its cwd" — that is,
**cwd already decided which harness the agent saw**, as a side effect. And
`skills_sync` turned out to be fully root-parameterized (`discover_skills(root)`,
`link_tool(root, …)`, `render_tool(root, …)`, `project_agents(root, …)`); only
`bench_run.py` hardcoded `REPO_ROOT`.

## Decision

We will add **`RunConfig.harness_source`** with three values, and make it part of the
run's condition.

- **`lab_root`** — the agent runs from the lab repository and sees the lab's
  harness, editing the workspace from outside. The historical behaviour, and the
  default for every run that predates this ADR.
- **`native`** — the agent runs *inside* the target repository with whatever
  governance that repository ships, and nothing else.
- **`injected`** — the agent runs inside the target repository with the lab's
  harness projected into it.

`native` and `injected` require a target repository and are rejected at preflight
without one.

**Projection copies, never symlinks.** `link_tool`'s symlink strategy encodes a path
relative to the root it writes into, and a skill living in the lab is not reachable
by a relative path from a cloned repository. The target must also stand on its own
once the run is archived and the lab has moved on.

**The projection lands before the baseline commit.** Provisioning commits the
workspace so `git diff HEAD` isolates the agent's work; a harness projected into an
already-committed tree would appear in that diff as though the agent had written it,
and every diff-based measurement would be wrong.

**The projection carries the run's effective skill set**, so an ablated arm stays
ablated inside the target. Projecting the full governed set would silently undo the
ablation for exactly the runs meant to measure it.

**The fingerprint moves to where the harness is.** For `native` and `injected`, the
harness fingerprint is computed over the *workspace* — the native skill directories,
projected agents, adapter files, governance docs and hooks actually readable from the
agent's cwd — instead of over the lab's `skills-lock.json`.

This is the load-bearing decision. A cloned repository has no `skills-lock.json`, so
fingerprinting both arms against the lab would make them **hash identically**: the two
conditions would collapse into one `condition_hash` and the comparison would silently
stop being a comparison. `harness_source` also enters `condition_hash` directly, under
the same non-default-only rule as skill ablation, so no historical arm re-bins.

**A collision is recorded, not resolved silently.** When the target already ships a
`CLAUDE.md`, `AGENTS.md` or `OPENCODE.md`, projection displaces it to
`<name>.native.bak` and records the path in `harness_projection.collisions`.
Replacing a repository's own governance changes the thing being measured, so it has
to be visible in the run's manifest.

## Consequences

Mode A on someone else's repository becomes expressible, which is the shape most
external engagements start from: *baseline (their conventions) vs treatment (our
harness), their repo, their task, N repetitions*.

The `native` arm is a genuine control for the first time. Previously a "bare" run
launched from the repo root silently inherited the whole lab harness through cwd —
a control arm that was quietly a second treatment arm.

Three costs, accepted:

- **Projection is destructive inside the workspace.** It is a throwaway clone and
  every displaced file is kept as `.native.bak`, but it is a mutation of the subject
  under measurement and must stay visible in the manifest.
- **`native` and `lab_root` are different arms, not the same arm described twice.**
  A run whose agent works from outside the repo behaves differently from one working
  inside it, independently of the harness. Comparing `lab_root` against `native` mixes
  two changes; the honest comparison is `native` against `injected`.
- **The SWE lane's pre-existing `agent_cwd` hygiene keeps its lab-root fingerprint.**
  Moving it would be more correct in principle and would re-bin every historical SWE
  arm against a hash it never had. The rule from
  [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md) wins: never silently
  re-bin history.

Not decided here: how a `native` run should be *audited*. The auditor still reads the
lab's governance documents, which is the wrong document for a `native` arm — tracked
as [Phase 5](../roadmap-lab-v2.md#phase-5--evaluation-schema).

## Alternatives considered

- **Merge the lab harness into the target's existing governance.** Rejected for now:
  a merged `CLAUDE.md` is a third artifact that is neither arm, and it would make the
  treatment un-nameable. Backup-and-replace keeps both arms describable.
- **Keep the harness in the lab and pass its path to the agent.** Rejected: the
  auto-loading behaviour that makes a harness effective (`CLAUDE.md` and native skill
  dirs read from cwd) is exactly what cannot be redirected by an argument.
- **Support an arbitrary third harness repository.** Deferred, as
  [the roadmap states](../roadmap-lab-v2.md#explicitly-out-of-scope-for-v2). The two
  sources here — the lab's own and the one already in the target — cover the
  comparison that matters without multiplying the state space.
