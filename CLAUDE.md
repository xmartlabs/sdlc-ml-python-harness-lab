# Claude Code Adapter

Use this repository-level structure as the canonical source of instructions.

## Governance

Always read and apply these files before generating code or plans:

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

## Operating playbook

How to work in this repository to produce high-quality, well-grounded changes. These
are defaults, not ceremony — scale them to the task. The full rationale and the
end-to-end loop live in `docs/agentic-workflow.md`.

1. **Ground before doing.** Read and search the relevant code before editing. Skim
   `memory/context.md` and `memory/learnings.md` for prior context. Explore first;
   blind edits cause rework.
2. **Plan multi-step work.** For anything beyond a trivial change, start with a
   `TodoWrite` plan and the `writing-plans` / `brainstorming` (or `brainstorm_quick`)
   skill. Complete one phase before the next.
3. **Delegate independent work in parallel.** When subtasks are independent, dispatch
   them to subagents concurrently (one message, multiple `Task` calls) per the
   `subagent-driven-development` skill. Use the governed agents in `.github/agents/`.
4. **Verify continuously.** Run `make test` (or focused `pytest`) after each
   substantive change, and close work with `make check` and the
   `requesting-code-review` skill. The `/verify` command bundles the gate.
5. **Compound knowledge.** When you finish a unit of work, record decisions in
   `docs/adr/` and durable learnings in `memory/`. Use `/adr` and `/retro`. Leave the
   repo's memory richer than you found it.
6. **Prefer CLI, load tools on demand.** Reach for `make` / `uv` / CLI over MCP where
   both work (leaner and reproducible). Use ToolSearch to load tool schemas on demand
   rather than assuming a tool is unavailable.
7. **Recover, don't thrash.** On an error, diagnose the cause before retrying; do not
   hammer the same file repeatedly. Get it right early rather than iterating blindly.
8. **Match the model to the task.** Use the planner / executor / fast tiers in
   `.github/portability.md`; offload routine sub-work to cheaper models and reserve
   the strongest model for planning and hard reasoning.

## Native Skills

Claude Code discovers internal and synced external skills from:

- `.claude/skills/`

Generate or refresh that native layout with:

- `make setup-claude-skills`

The governed internal source of truth remains:

- `.github/skills/`

External synced/vendor skills remain in:

- `.github/skills-external/`

Antigravity uses a separate generated native workspace mirror at:

- `.agents/skills/`

If overlap exists, prefer `.github/skills/` over `.github/skills-external/`.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `.claude/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

**Internal skills:**


Refresh this layout with `make sync-skills` (or `make check-sync` to verify it is current).
<!-- END GENERATED SKILLS -->

## Automation

Prefer system-enforced quality over model-only behavior:

- Automation policy: `.github/automation.md`
- Local sequence: `make format` -> `make fix`; CI-safe gate: `make check`.
- CI is read-only (`make ci` = `make check` + `make check-sync`) — never relies on
  CI to format or fix.

Check `Makefile` before suggesting commands.

## Orchestration

Use explicit orchestration for complex tasks:

- Orchestration policy: `.github/orchestration.md`
- Plan first, then execute.
- Complete each phase before moving to the next.
- Review diffs before finalizing.
- Validate results against automation requirements.

## Agents and SDLC

- Governed, tool-agnostic agents live in `.github/agents/`; their Claude Code
  native subagents are generated into `.claude/agents/` (refresh with
  `make sync-agents`).
- The AI-assisted lifecycle (plan -> implement -> test -> document -> review) with
  its `make` exit gates is defined in `.github/sdlc.md`.
- Runtime/model portability and the `planner`/`executor`/`fast` tier abstraction
  are documented in `.github/portability.md`.

## Runtime Rules

- Interact in the same language as the user.
- Keep all code artifacts in English (identifiers, docstrings, comments, docs).
- Prefer `make` targets and `uv` workflows.
- When implementing or testing changes, create or update documentation in `docs/`.
- Use absolute imports only.
