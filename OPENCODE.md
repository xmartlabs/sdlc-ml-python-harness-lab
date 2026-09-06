# OpenCode Adapter

Use this repository-level structure as the canonical source of instructions.

## Level 1 — Governance

Always read and apply these files before generating code or plans:

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

Each skill exposes a `SKILL.md` file with purpose, required input, output format, and execution rules.
When a task matches a skill, read its `SKILL.md` before generating code or plans.

<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
The governed skills below are projected into `.opencode/skills/`. Internal skills are the source of truth and take precedence over external synced skills on name conflicts.

**Internal skills:**


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
  are generated into `.opencode/agents/` (refresh with `make sync-agents`).
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
