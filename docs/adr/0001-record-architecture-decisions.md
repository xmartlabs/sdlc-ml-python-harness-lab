# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Template maintainers
- **Related:** `docs/adr/README.md`, `docs/agentic-workflow.md`, `memory/`

## Context

This repository is a template for AI-assisted ML projects. Significant decisions —
framework choices, architectural boundaries, trade-offs — were previously left
implicit in code and commit messages. That forces every new contributor, human or
agent, to reverse-engineer the *why* behind the code, which is slow and error-prone.
Agents in particular extend a system more consistently when the rationale is written
down and reachable.

## Decision

We will record significant, hard-to-reverse decisions as Architecture Decision
Records under `docs/adr/`, using the format in `0000-template.md`. Each record is
numbered sequentially and immutable once accepted; a changed decision is captured in
a new ADR that supersedes the old one.

## Consequences

- Reviews and onboarding are faster because rationale is one click away.
- Settled questions are not re-litigated.
- Writing under `docs/adr/` satisfies the repository rule that code changes ship with
  documentation, and builds a durable, compounding decision log.
- Small cost: contributors must spend a few minutes writing a record for each major
  decision. Routine changes are intentionally exempt.

## Alternatives considered

- **Keep decisions in commit messages only** — rejected: not discoverable, hard to
  index, and easily buried in history.
- **A single growing `DECISIONS.md`** — rejected: merge-conflict prone and harder to
  reference a specific decision by stable id.
