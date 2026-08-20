# Claude Code slash commands

Project-scoped slash commands for Claude Code. Each file becomes a `/<name>` command
available when working in this repository. They encode the repository's recommended
agentic workflow (see `docs/agentic-workflow.md`) so the good path is one keystroke
away.

| Command | Purpose | Workflow step |
|---------|---------|---------------|
| `/plan` | Ground in the code, then produce an explicit, phased plan. | Plan |
| `/orchestrate` | Decompose into independent subtasks and run subagents in parallel. | Delegate |
| `/verify` | Run the read-only quality gate (`make check`) + sync gate and summarize. | Verify |
| `/adr` | Scaffold a new Architecture Decision Record under `docs/adr/`. | Compound |
| `/retro` | Retrospective that records durable learnings into `memory/`. | Compound |

These are native to Claude Code and intentionally tool-specific. The portable,
tool-agnostic governance lives in `.github/` and the governed skills; commands are a
thin, ergonomic layer on top for this editor.
