# ADR-0054: A repository is audited before the lab runs anything on it

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0042](0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md),
  [ADR-0043](0043-one-llm-transport-gateway-first-claude-cli-second.md),
  [ADR-0045](0045-a-checkout-the-lab-can-be-given-not-only-watched.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0053](0053-an-experiment-is-read-back-as-prose.md)

## Context

The guided audit's second step was a checklist: rules file present or not, N skills, N
agents, a runtime-compatibility line. Accurate, and not what somebody deciding whether
to spend agent time on a repository needs to hear. They need three sentences — *this
repository ships a harness made of these parts, in this language, and here is what
could be tried on it* — and a clear stop when there is nothing to evaluate or the lab
cannot execute the code at all. The checklist also let the wizard walk a Go or COBOL
repository all the way to a launched run that would fail in provisioning, and it let a
repository with nothing written for an agent be "audited" as if its harness were the
question.

The lab already has one LLM reading pattern (ADR-0053: evidence in, prose out, stored
beside the evidence) and one transport (ADR-0043). What was missing was the reading
itself and a rule for who decides whether the wizard continues.

## Decision

**A `RepositoryAudit` per target, generated on demand at the pinned commit.** The lab
reads the tree through the same blobless mirror readiness uses — rules files, the
opening of every skill and agent, the commands, the hooks declared in
`.claude/settings.json` and `.claude/hooks/`, pre-commit and husky, CI workflow names,
Makefile targets, build manifests, the README — and hands that evidence to the auditor
model with a fixed JSON contract: summary, harness verdict, components with a one-line
assessment each, language notes, standards, gaps, and three to five use cases with a
validation hint. The report is rendered by the lab from that JSON, so every audit has
the same sections in the same order. An answer that is not JSON is stored with a
`parse_error` and shown as what the model said, never re-interpreted.

**The gate is decided from facts, not by the model.** `next_step` lives in the
evidence and takes three values: `unsupported_runtime` when no ecosystem the runner
image ships was detected (marker files first, then a file-extension census, so a tree
that is mostly `.cbl` is named as COBOL rather than shrugged at as unknown);
`no_harness` when nothing in the tree is written for an agent (CI and pre-commit are
engineering standards, not a harness); `continue` otherwise. The runtime gate is
checked first: a repository the runner cannot build stops however good its harness is,
because nothing measured afterwards would be about the agent. The model's
`harness_verdict` is printed beside the lab's line and cannot override it.

**The wizard stops where the gate says.** An unsupported runtime ends the audit with no
continue button. A missing harness ends the primary flow and leaves one secondary
door — compare the repository as it is against the lab harness — because that is the
comparison the lab already recommends for a team with no governance. Only a supported
runtime with a harness reaches the case step and the experiment step.

**The experiment step offers the two honest designs for a target repository.** One arm
(`native`, a subject validation: does the case pass here at all) or two arms
(`native` versus `injected`, a harness evaluation: the repository's own governance
against the lab's ml-python-base harness in the same workspace at the same commit). A
`bare` arm is not offered: it is a masked lab container and cannot be put inside
somebody else's repository (ADR-0049).

## Consequences

- One more LLM call per repository, worker-side, re-runnable; the row records the
  commit it read so a re-pin makes the previous audit visibly stale.
- The client-facing report gains a section quoting the audit's structured half,
  labelled a judgement, with the runtime line labelled a fact.
- The evidence caps what the model sees (three rules files, 25 skills, 12 agents,
  capped bodies). A repository with more is described by its first entries and the
  classification counts, which is a description of the harness, not a dump of it.
- The use-case list is the model's proposal only. A person drafts the case from it; the
  audit does not create cases.

## Alternatives considered

- **Let the model decide whether to continue.** Rejected: a judgement would be able to
  open a door a fact has closed, and an audit that says "Python" of a COBOL tree with a
  Python wrapper would launch a run that fails in provisioning.
- **A free-form markdown reading, like the run audit.** Rejected: two audits a month
  apart would not line up section by section, and the wizard needs the verdict and the
  use cases as data.
- **Extend the deterministic readiness checklist instead.** Rejected: it already said
  everything a checklist can; the missing piece was the reading.
