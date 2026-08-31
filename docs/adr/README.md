# Architecture Decision Records (ADRs)

An ADR captures a single significant decision: the context that forced a choice, the
choice made, and its consequences. ADRs are immutable once accepted — if a decision
changes, write a new ADR that supersedes the old one rather than editing history.

## When to write an ADR

Write one whenever you make a decision that is **hard to reverse** or that future
contributors (human or agent) would otherwise have to reverse-engineer:

- Choosing a framework, library, datastore, or external service.
- Defining or changing an architectural boundary or module layout.
- A non-obvious trade-off (performance vs. simplicity, build vs. buy).
- A convention everyone must follow.

Skip an ADR for routine, easily reversible changes — that is what `memory/` and
commit messages are for.

## How to write one

1. Copy `0000-template.md` to `NNNN-short-kebab-title.md` (next number, zero-padded).
2. Fill in Context, Decision, Consequences, and Alternatives considered.
3. Set status to `Proposed`; change to `Accepted` once agreed, or `Superseded by
   ADR-XXXX` when replaced.
4. Keep it short — one page is plenty. Link related ADRs and `memory/` entries.

The `/adr` slash command (Claude Code) scaffolds a new record from the template.

## Index

- [0001 — Record architecture decisions](0001-record-architecture-decisions.md)
- [0002 — Celery + Docker-per-run execution](0002-celery-docker-per-run-execution.md)
- [0003 — Trial provenance and harness fingerprint](0003-trial-provenance-and-harness-fingerprint.md)
- [0004 — Objective validation and multidimensional score](0004-objective-validation-and-multidimensional-score.md)
- [0005 — Structured harness attribution](0005-structured-harness-attribution.md)
- [0006 — Auto-incremental run IDs](0006-auto-incremental-run-ids.md)
- [0007 — Run deletion semantics](0007-run-deletion-semantics.md)
- [0008 — LLM audit exposed in the web app](0008-llm-audit-in-web.md)
- [0009 — SWE-bench case type](0009-swebench-case-type.md)
- [0010 — In-platform validation sandboxes](0010-in-platform-sandboxes.md)
- [0011 — Per-run skill ablation and staged-skill trials](0011-per-run-skill-ablation-and-staging.md)
- [0012 — Dynamic gateway model list and opencode.json sync](0012-dynamic-gateway-model-list.md)
- [0013 — Web backoffice with an overlay configuration model](0013-web-backoffice-and-overlay-model.md)
- [0014 — Web-editable `.env` behind an allowlist](0014-web-editable-env-allowlist.md)
- [0015 — Single-user session authentication](0015-single-user-session-auth.md)
- [0016 — opencode config overlay for model routing](0016-opencode-config-overlay-for-model-routing.md)
- [0017 — Multi-file staged skills and remote import](0017-multi-file-staged-skills-and-remote-import.md)
- [0018 — Health and drift panel](0018-health-and-drift-panel.md)
- [0019 — Always-on local deployment](0019-always-on-local-deployment.md)
- [0020 — Context-window preflight and gateway alias merge](0020-context-window-preflight-and-alias-merge.md)
- [0021 — Guarantee that a run's artifacts survive and its verdict is visible](0021-benchmark-artifact-integrity.md)
- [0022 — Split the silence clock by what the run was waiting for](0022-watchdog-splits-model-wait-from-agent-stall.md)
- [0023 — Queue many runs per request as an all-or-nothing cross-product](0023-batch-run-execution.md)
- [0024 — Pin container base images to the toolchain CI verifies](0024-base-images-track-ci-verified-toolchains.md)
- [0025 — The DB is the source of truth for every case](0025-db-is-the-source-of-truth-for-every-case.md)
- [0026 — A continuation is a child run, not a reopened one](0026-run-continuation-as-child-runs.md)
- [0027 — Langfuse traces are rebuilt from run artifacts, not emitted live](0027-langfuse-traces-rebuilt-from-run-artifacts.md)
- [0028 — Make the evaluation mode an explicit property of a comparison](0028-evaluation-modes.md)
- [0029 — Make the target repository first-class and pin every run to a resolved SHA](0029-target-repositories-pinned-per-run.md)
- [0030 — Provision every workspace through one source interface](0030-one-workspace-source-interface.md)
- [0031 — Make the harness a condition of the run, not a property of the machine](0031-harness-source-is-a-run-condition.md)
- [0032 — Derive organizational cases from a target's own history, deterministically](0032-cases-are-derived-from-a-targets-own-history.md)
- [0033 — Put `Experiment` above `Run`, and refuse a design its mode cannot answer](0033-experiment-above-run.md)
- [0034 — Record every metric against a declared, versioned definition](0034-score-registry.md)
- [0035 — Make a finding a testable claim, and keep the maturity assessment human](0035-findings-and-manual-maturity-assessment.md)
- [0036 — Judge every run against the governance surface it actually had](0036-audit-judges-a-run-against-its-own-governance.md)
- [0037 — Make the regression suite produce experiments, not run them](0037-regression-suite-produces-experiments.md)

## Why ADRs exist

Decisions are the highest-value context to preserve: code shows *what*, ADRs explain
*why*. They make reviews faster, prevent re-litigating settled questions, and give
agents the rationale they need to extend the system consistently. See
`docs/agentic-workflow.md`.
