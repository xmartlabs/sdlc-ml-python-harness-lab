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

If an internal governed skill and an external synced skill share the same name, prefer the internal governed skill.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `@.agents/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

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
