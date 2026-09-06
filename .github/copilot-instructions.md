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
