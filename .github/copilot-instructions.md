# Copilot Instructions (Template Governance Adapter)

## `local_model_32k` mode — read this first, it overrides everything below

When the task is a spike / prototype / vibe-coding session (the user mentions
`local_model_32k` or `LOCAL_AGENT.md`, or asks to build/fix step by step):

- Apply `LOCAL_AGENT.md` only. **Skip Levels 1-5 below** — no governance, no skill lookups.
- **These override the general guidance in this file:** do NOT "gather as much context as
  needed", do NOT "read large chunks", do NOT keep calling tools until fully done. Instead:
  one whole-file read, one targeted edit, one result line, then stop.
- **Never rewrite a whole file** and keep output small — a large response fails with
  "Response too long" and loses the turn. Fix one root cause per turn.
- **End each increment by telling the user to open a NEW chat** — long chats get auto-compacted
  and you lose the code state. Never output a "what's implemented / what's missing" summary; on
  resume, re-read only what the next increment needs.

Output is also capped at the gateway (`max_tokens`) so over-long responses fail fast by
design — see `docs/local-model-runtime-config.md`.

---

Use the following 4-level structure as the single source of truth:

## Level 1 — Governance

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

Always read and apply these files before generating code or plans.

## Level 2 — Operational Skills

Internal governed skills are the source of truth:

- `.github/skills/` (internal curated)
- `.github/skills-external/` (synced external/vendor)

Prefer internal curated skills when both define overlapping capabilities.

Claude Code has a generated native view at `.claude/skills/`, Antigravity has a generated native view at `.agents/skills/`, and OpenCode has a generated native view at `.opencode/skills/`. All three are refreshed with `make sync-skills`; each can also be refreshed individually with `make setup-claude-skills`, `make setup-antigravity-skills`, or `make setup-opencode-skills`. Copilot should continue to use `.github/skills/` and `.github/skills-external/` as inline instruction references.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
Read the governed skills below directly from `.github/skills/` (internal) and `.github/skills-external/` (external). Internal skills take precedence over external synced skills on name conflicts.

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

Each skill must:

- receive explicit input,
- produce structured output,
- comply with governance files.

## Level 3 — Real Automation

Prefer system-enforced quality over model-only behavior:

- strict lint rules
- CI checks
- structure enforcement
- PR bots

Automation policy reference:

- `.github/automation.md`

## Level 4 — Orchestration

Use explicit orchestration for complex tasks:

- plan-first requirement
- step-by-step execution
- mandatory diff review
- validation against automation
- no direct large generation without relevant skill invocation

Orchestration policy reference:

- `.github/orchestration.md`

## Level 5 — Agents and SDLC

- `.github/agents/` — governed, tool-agnostic agent definitions.
- `.github/sdlc.md` — lifecycle phases gated by `make` targets.
- `.github/portability.md` — model tier abstraction and self-hosted fallback design.

## Additional Rules

- Interact with user in the language used by the user.
- Keep all code artifacts in English.
- Prefer `Makefile` commands and `uv` workflow.
- When implementing and testing new changes, create or update documentation in `docs/`.
- The developer is the sole author of every commit and PR: never commit, push or branch unless asked, never set a git identity, never add AI co-author or "Generated with" lines (see `.github/standards.md` → Authorship).
