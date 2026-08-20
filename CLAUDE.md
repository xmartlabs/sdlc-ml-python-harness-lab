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

- `bootstrap_project` — Use when starting a new project from a fresh clone of this template — guides the rename via `make init`, environment setup, template-remote configuration, and the first green quality gate, in any supported AI tool.
- `brainstorm_quick` — Use for fast ideation on a scoped feature when no written spec or formal approval is needed — diverge on options, weigh trade-offs, converge on a recommendation, then hand off to `plan_and_execute_feature`. For new features or design-impacting work that needs a written, user-approved spec, use the external `brainstorming` skill (full design gate) instead.
- `create_domain_contract` — Use when defining a typed domain contract — an application use case (business flow) or a repository interface (persistence boundary) — with clean architecture boundaries.
- `create_mle_agent_package` — Use when designing a reusable pip-installable MLE agent package with governed scaffolding, runtime adapters, and validation plans.
- `generate_e2e_tests` — Use when generating end-to-end tests for critical user, API, CLI, or service flows.
- `generate_implementation_docs` — Use when creating or updating implementation documentation for completed code or test changes.
- `generate_migration_plan` — Use when planning low-risk code, data, or architecture migrations with validation and rollback steps.
- `plan_and_execute_feature` — Use when delivering a feature through explicit planning, phased execution, validation, and governed handoff — or when implementing/fixing already-scoped engineering work via the execute_only mode.
- `refactor_to_clean_architecture` — Use when refactoring modules to align dependency direction, responsibilities, and boundaries with clean architecture.
- `research_current_info` — Use when the user asks for up-to-date or current information, to confirm something is still accurate, or when a task depends on facts that may have changed since training (library versions, APIs, pricing, releases, news, current best practices). Runs a governed web search with a curated domain allow/deny policy and cited, recency-checked results.
- `retrospective` — Use at the end of a unit of work to capture durable, non-obvious knowledge into project memory (memory/) and flag decisions worth an ADR. Turns one-off discoveries into compounding, persistent context.
- `systematic_debugging` — Use when diagnosing a bug, failing test, or unexpected behavior — drive a methodical reproduce → isolate → hypothesize → fix → verify loop instead of guessing. Prevents thrashing and repeated edits to the same file.
- `validate_module_structure` — Use when validating module placement, dependency direction, and structure against repository governance.
- `verify_changes` — Use before considering work done — run the read-only quality gate and tests, interpret failures, and confirm the change is correct. The verification step of the working loop.

**External synced skills:**

- `brainstorming` — You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation.
- `executing-plans` — Use when you have a written implementation plan to execute in a separate session with review checkpoints
- `finishing-a-development-branch` — Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup
- `requesting-code-review` — Use when completing tasks, implementing major features, or before merging to verify work meets requirements
- `subagent-driven-development` — Use when executing implementation plans with independent tasks in the current session
- `test-driven-development` — Use when implementing any feature or bugfix, before writing implementation code
- `ui-ux-pro-max` — UI/UX design intelligence for web and mobile. Includes 50+ styles, 161 color palettes, 57 font pairings, 161 product types, 99 UX guidelines, and 25 chart types across 10 stacks (React, Next.js, Vue, Svelte, SwiftUI, React Native, Flutter, Tailwind, shadcn/ui, and HTML/CSS). Actions: plan, build, create, design, implement, review, fix, improve, optimize, enhance, refactor, and check UI/UX code. Projects: website, landing page, dashboard, admin panel, e-commerce, SaaS, portfolio, blog, and mobile app. Elements: button, modal, navbar, sidebar, card, table, form, and chart. Styles: glassmorphism, claymorphism, minimalism, brutalism, neumorphism, bento grid, dark mode, responsive, skeuomorphism, and flat design. Topics: color systems, accessibility, animation, layout, typography, font pairing, spacing, interaction states, shadow, and gradient. Integrations: shadcn/ui MCP for component search and examples.
- `using-git-worktrees` — Use when starting feature work that needs isolation from current workspace or before executing implementation plans - ensures an isolated workspace exists via native tools or git worktree fallback
- `writing-clearly-and-concisely` — Apply Strunk's timeless writing rules to ANY prose humans will read—documentation, commit messages, error messages, explanations, reports, or UI text. Makes your writing clearer, stronger, and more professional.
- `writing-plans` — Use when you have a spec or requirements for a multi-step task, before touching code

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
