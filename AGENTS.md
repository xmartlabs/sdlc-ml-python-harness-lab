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

- `brainstorm_quick` — Use for fast ideation on a scoped feature when no written spec or formal approval is needed — diverge on options, weigh trade-offs, converge on a recommendation, then hand off to `plan_and_execute_feature`. For new features or design-impacting work that needs a written, user-approved spec, use the external `brainstorming` skill (full design gate) instead.
- `plan_and_execute_feature` — Use when delivering a feature through explicit planning, phased execution, validation, and governed handoff — or when implementing/fixing already-scoped engineering work via the execute_only mode.
- `research_current_info` — Use when the user asks for up-to-date or current information, to confirm something is still accurate, or when a task depends on facts that may have changed since training (library versions, APIs, pricing, releases, news, current best practices). Runs a governed web search with a curated domain allow/deny policy and cited, recency-checked results.
- `systematic_debugging` — Use when diagnosing a bug, failing test, or unexpected behavior — drive a methodical reproduce → isolate → hypothesize → fix → verify loop instead of guessing. Prevents thrashing and repeated edits to the same file.
- `verify_changes` — Use before considering work done — run the read-only quality gate and tests, interpret failures, and confirm the change is correct. The verification step of the working loop.

**External synced skills:**

- `requesting-code-review` — Use when completing tasks, implementing major features, or before merging to verify work meets requirements
- `writing-plans` — Use when you have a spec or requirements for a multi-step task, before touching code

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
- The developer is the sole author of every commit and PR: never commit, push or branch unless asked, never set a git identity, never add AI co-author or "Generated with" lines (see `.github/standards.md` → Authorship).
