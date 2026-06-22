# OpenCode Adapter

Use this repository-level structure as the canonical source of instructions.

## Level 1 — Governance

Always read and apply these files before generating code or plans:

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

## Level 2 — Operational Skills

Each skill exposes a `SKILL.md` file with purpose, required input, output format, and execution rules.
When a task matches a skill, read its `SKILL.md` before generating code or plans.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `.opencode/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

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
- `validate_module_structure` — Use when validating module placement, dependency direction, and structure against repository governance.

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

## Level 3 — Automation

Prefer system-enforced quality over model-only behavior:

- Automation policy: `.github/automation.md`
- Quality gate sequence: `make format` → `make fix` → `make lint` → `make test`

Check `Makefile` before suggesting commands.

## Level 4 — Orchestration

Use explicit orchestration for complex tasks:

- Orchestration policy: `.github/orchestration.md`
- Plan first, then execute.
- Complete each phase before moving to the next.
- Review diffs before finalizing.
- Validate results against automation requirements.
- Do not generate large outputs without first invoking the relevant skill.
- Size each step to the active model. On a weak/self-hosted build model, work in
  one-file chunks and reach a runnable milestone before expanding — see
  `docs/task-sizing.md`.

## Level 5 — Agents and SDLC

- Governed, tool-agnostic agents live in `.github/agents/`; OpenCode native agents
  are generated into `.opencode/agent/` (refresh with `make sync-agents`).
- The lifecycle (plan -> implement -> test -> document -> review) and its `make`
  exit gates are defined in `.github/sdlc.md`.
- Runtime/model portability for self-hosted fallback (Ollama / LM Studio) and the
  `planner`/`executor`/`fast` tier abstraction: `.github/portability.md`.

## Models and Providers

Provider/model config is env-driven in `opencode.json` (`{env:...}` interpolation):
self-hosted `ollama` / `lmstudio` plus built-in `openai` and `opencode` (Zen). Set
hosts and models in `.env` (see `.env.example`); launch with `make opencode` and
verify endpoints with `make opencode-doctor`. Full guide: `.github/portability.md`.

## Runtime Rules

- Interact in the same language as the user.
- Keep all code artifacts in English (identifiers, docstrings, comments, docs).
- Prefer `make` targets and `uv` workflows.
- When implementing or testing changes, create or update documentation in `docs/`.
- Use absolute imports only.
