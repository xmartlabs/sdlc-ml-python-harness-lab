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

## Why ADRs exist

Decisions are the highest-value context to preserve: code shows *what*, ADRs explain
*why*. They make reviews faster, prevent re-litigating settled questions, and give
agents the rationale they need to extend the system consistently. See
`docs/agentic-workflow.md`.
