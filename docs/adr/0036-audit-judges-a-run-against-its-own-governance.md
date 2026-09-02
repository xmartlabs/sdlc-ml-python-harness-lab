# ADR-0036: Judge every run against the governance surface it actually had

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Marcos Soto
- **Related:** [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0034](0034-score-registry.md),
  [known-limitations.md](../known-limitations.md)

## Context

[ADR-0031](0031-harness-source-is-a-run-condition.md) made the harness a condition of
the run: a `native` arm sees the target repository's own governance, an `injected` arm
sees the harness the lab copied in. It closed by naming what it did not decide:

> Not decided here: how a `native` run should be *audited*. The auditor still reads
> the lab's governance documents, which is the wrong document for a `native` arm.

That is the whole comparison leaking. The audit prompt listed the lab's `CLAUDE.md`,
`.github/standards.md`, `.github/agents/` and the rest, and asked the model how well
the run adhered to them — including for the arm that never received a single one of
those files. `governance_coverage` came back as a fraction of the lab's document set,
so an honest `native` run scored badly for ignoring rules it was never given, and the
`injected` arm's advantage was partly an artifact of the measuring instrument.

The same defect reached the attribution layer, which computed `docs_read` against one
hardcoded `HARNESS_READING_SURFACE` constant, and the score registry, where
`skill_adherence` inherited the wrong denominator.

## Decision

**The governance surface is resolved per run, from the workspace that run actually
had, and is persisted with the audit.**

`harness_lab_app.governance` collects the run's readable governance files under the
root the run's `harness_source` implies:

- `native` and `injected` resolve against the run's **archived workspace** — the
  clone the agent worked in, with whatever the projection put there. `source` is
  recorded as `target_workspace`.
- `lab_root` resolves against the lab repository, recorded as `lab_root`.

The resulting `GovernanceSurface` carries the `source`, the `root`, the
`harness_source` it was derived from, the `rules_file` for the harness, the enumerated
file list (capped at 400 entries), a `fingerprint` hash of that list, and a `warning`
when a fallback had to be taken. It is stored on `Audit.governance_json`, and its
`source` / `fingerprint` / `harness_source` are echoed onto
`attribution.harness_usage` so a coverage figure never appears without its
denominator's provenance.

`source` is deliberately **separate from** `harness_source`. They can disagree — an
archived workspace that was cleaned up forces a fallback to the lab root — and a
fallback that silently masquerades as the real surface is precisely the failure this
ADR exists to prevent. When they disagree, the surface carries a warning and the run
page prints it.

## Consequences

A `native` control arm is finally scored as a control: against its own repository's
conventions, or against nothing at all when the repository has none. Comparing
`native` against `injected` now measures the harness rather than the audit prompt.

`governance_coverage` becomes readable. "2/9" against a client's own documents and
"2/9" against the lab's are different measurements that previously rendered
identically; both the API payload and the run page now say which.

Three costs, accepted:

- **Audits recorded before this ADR have an empty `governance_json`.** They are shown
  as "predates per-run governance surfaces" rather than backfilled. Inventing a
  surface for a historical audit would fabricate the very provenance this adds.
- **The surface is a file list, not a semantic model.** It says which documents the
  agent could read, not whether they say anything useful. A repository with a large
  and worthless `.github/` will look well-governed to this mechanism.
- **The fingerprint hashes paths, not contents.** Cheap and stable, but two runs
  whose governance files changed underneath them share a fingerprint. Content hashing
  belongs to [ADR-0003](0003-trial-provenance-and-harness-fingerprint.md)'s
  fingerprint, not to this one.

## Alternatives considered

- **Keep one reading surface and subtract the missing files for `native`.** Rejected:
  it still assumes the lab's documents are the universe of governance, and a target
  repository's own conventions would never enter the denominator at all.
- **Refuse to audit `native` runs.** Rejected: the exploratory question "what does
  this team's setup already do for them?" is exactly [Mode C](0028-evaluation-modes.md),
  and it needs an audit against their surface, not no audit.
- **Have the auditor discover the governance itself.** Rejected: a surface the model
  chooses is not reproducible, cannot be fingerprinted, and cannot be compared across
  the two arms of one experiment.
