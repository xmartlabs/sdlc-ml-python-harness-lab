# Codex UI Adapter

Use this repository-level structure as the canonical source of instructions.

## Level 1 — Governance

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

## Working loop

Default working loop for this repository: **Ground → Plan → Delegate → Verify →
Compound.** Read `memory/context.md` and `memory/learnings.md` before starting;
explore before editing; verify with `make check` / tests; record decisions in
`docs/adr/` and durable learnings in `memory/`. Full guide:
`docs/agentic-workflow.md`.

## Level 2 — Operational Skills

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `.codex/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

**Internal skills:**


Refresh this layout with `make sync-skills` (or `make check-sync` to verify it is current).
<!-- END GENERATED SKILLS -->

## Level 3 — Automation

- `.github/automation.md`

## Level 4 — Orchestration

- `.github/orchestration.md`

## Level 5 — Agents and SDLC

- `.github/agents/` — governed, tool-agnostic agent definitions.
- `.github/sdlc.md` — plan -> implement -> test -> document -> review, each gated by
  a `make` target.
- `.github/portability.md` — runtime/model tier abstraction and self-hosted fallback.

## Runtime Rules

- Use user language for interaction.
- Keep all code artifacts in English.
- Prefer Makefile and uv workflows.
