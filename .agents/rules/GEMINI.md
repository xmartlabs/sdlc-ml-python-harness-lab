---
trigger: always_on
---

# Antigravity Workspace Rule

Use this repository's governed structure as the canonical source of truth.

## Governance

Read and apply:

- @.github/architecture.md
- @.github/standards.md
- @.github/domain-boundaries.md

## Skills

Antigravity discovers workspace-native skills from:

- @.agents/skills/

The governed sources remain:

- @.github/skills/
- @.github/skills-external/

Refresh the Antigravity-native layout with:

- `make setup-antigravity-skills`
- `make sync-skills`

If an internal governed skill and an external synced skill share the same name, prefer the internal governed skill. When skills overlap in purpose, choose the stricter applicable one:

- `brainstorming` — for new features, creative work, behavior changes, UI/game design, or anything needing design exploration (full design gate).
- `brainstorm_quick` — only for narrow ideation when no written spec, approval gate, or major implementation will follow.
- `writing-plans` / `plan_and_execute_feature` — for implementation once requirements are clear.

If unsure which applies, state the chosen skill and why in the run trace.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `@.agents/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

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

## Automation And Orchestration

Read and follow:

- @.github/automation.md
- @.github/orchestration.md

## Agents And SDLC

- @.github/agents/ — governed, tool-agnostic agent definitions.
- @.github/sdlc.md — lifecycle phases gated by `make` targets.
- @.github/portability.md — model tier abstraction and self-hosted fallback design.

## Runtime Rules

- Interact in the same language as the user.
- Keep all code artifacts in English.
- Prefer `make` targets and `uv` workflows.
- Update docs in `docs/` when implementation or tests change.
- Use absolute imports only.
- The developer is the sole author of every commit and PR: never commit, push or branch unless asked, never set a git identity, never add AI co-author or "Generated with" lines (see `.github/standards.md` → Authorship).

## Operating discipline (Antigravity)

Follow the operating discipline and run-trace rules in @.github/sdlc.md. In short:

- **Follow the mandatory skill flow** for full features (brainstorm → plan → TDD →
  verify → review); do not jump straight to code. If you skip a skill, say why.
- **Minimize human interventions.** Inspect the repo, read files, run safe read-only
  commands, and make a reasonable assumption when the next step is low-risk. Ask only
  for destructive actions, secrets, ambiguous product decisions, or irreversible
  choices — with the exact blocker, what you tried, and 1-3 options.
- **Validate before claiming done** and report the exact commands and results. Never
  declare completion without them.
- **Stay inside the requested task/workspace directory** unless authorized otherwise.
- **Leave a run trace:** at the start, state files read, skills to use, edit boundary,
  and a short plan; at the end, a handoff (skills used, files changed, gates run +
  results, skipped validation + why, assumptions, human interventions).
