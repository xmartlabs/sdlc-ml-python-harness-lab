# ADR-0055: The projection reads its adapter files from outside the mask

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0042](0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md)

## Context

Every `injected` arm died in provisioning:

```
SkillsSyncError: Adapter file is missing the generated-skills sentinels.
```

Two correct decisions collided. ADR-0049 masks the lab's governance from a run whose
cwd sits *under* the lab root: Claude Code collects `CLAUDE.md` from every ancestor, so
without the mask a `native` or `injected` arm would read the lab's rulebook from `/app`
on top of whatever it was given, and the arms would stop differing by what the
experiment says they differ by. The mask is a read-only bind of an empty file over
`/app/CLAUDE.md`, and `RUNNER_GOVERNANCE_FILES` lists every adapter file for exactly
that reason.

`inject_harness` reads those same files. The adapter file is not only something an
agent auto-loads — it is the *document the projection splices into*, the host for the
generated-skills region rendered against the run's effective skill set. So the runner
masked `/app/CLAUDE.md` to empty, `project_harness` copied that empty stub over the
client repository's own `CLAUDE.md`, and `splice` then refused a file with no
sentinels.

The same hazard had already been seen and solved one level up: `RUNNER_GOVERNED_SOURCE_DIRS`
carves `.github/` out of the `ancestors` mask, with a comment saying that masking it
would make an injected arm project nothing. The carve-out was made for the *directories*
the projection reads and never extended to the *files* it reads.

The error message made it worse. It named the target's adapter file, which is the one
file that is not at fault, and said nothing about which tool, which root, or why —
sending a reader into the client repository to look for a problem that was in the
container's mount table.

## Decision

**`project_harness` takes an `adapter_root`.** Adapter files are read from it;
everything else — Jinja templates under `adapters/`, skills and agents under
`.github/`, the governance documents — keeps coming from `source_root`, which no mask
covers. Unset, it is `source_root`, so an unmasked lane behaves exactly as before.

**The worker stages the real bytes.** The worker is not masked (the mask is a runner
mount), so for an `injected` run it copies each registered tool's adapter file into
`data/runs/<run_id>/harness_source/`, preserving the relative path. The run directory
is under the lab root and no mask covers it, which is the same property `case_spec.json`
already relies on. `bench_run.inject_harness` prefers that directory when it exists and
falls back to the repo root otherwise, so old run directories and unmasked lanes are
unaffected.

**A source that cannot be spliced is refused before anything is copied.**
`_require_spliceable` checks the source adapter file for the sentinels first and raises
naming the tool, the path and which of the two repairs applies — an empty file is the
mask (and says so, citing this ADR), a file without sentinels wants `make sync-skills`.
The target keeps its own governance untouched when the projection is refused; a
half-projected harness is a silently different experiment.

## Consequences

- The `injected` arm works, which restores the lab's central comparison: a
  repository's own governance against the lab's harness at the same commit.
- Adding a file to `RUNNER_GOVERNANCE_FILES` is now safe by default. If it is also an
  adapter file, the staging picks it up from the registry rather than needing a second
  list to be kept in step.
- The staging is best-effort and logs on failure rather than raising: provisioning
  reports the real failure with a message an operator can act on, and killing the run
  in the worker would replace that with a stack trace.
- Cost of the failure before it was found: a paid control arm whose comparison was
  lost to a deterministic crash in its treatment. The refusal is now immediate and
  self-describing, but it still happens inside the container — a launch-time preflight
  that checks the adapter files are spliceable before spending an arm is a further
  improvement, not taken here.

## Alternatives considered

- **Drop the adapter files from the `ancestors` mask.** Rejected: the injected arm
  would then read the lab's *unablated* adapter file from the ancestor while its
  workspace carried the ablated projection, which is precisely the confound ADR-0049
  was written to remove.
- **Mount the lab root a second time, unmasked, at another path.** Works, and costs a
  new container mount and a second notion of "the lab root" for every lane. Staging
  three small files into a directory the runner already reads is narrower.
- **Have the worker render the finished adapter file.** Rejected: the render needs the
  run's effective skill set, which `bench_run` computes, so this would move ablation
  logic out of the runner to serve a file-copy problem.
