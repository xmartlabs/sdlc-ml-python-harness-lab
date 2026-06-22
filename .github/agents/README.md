# Governed Agents (tool-agnostic source of truth)

Each `*.md` file defines one agent in a tool-neutral way. The skills sync engine
(`src/ml_python_base/skills_sync`) projects them into each AI tool's native agent
format — refresh with `make sync-agents` (or `make sync-skills`, which includes
agents).

## Frontmatter schema

```yaml
---
name: implementer                 # unique id (kebab/snake)
description: One line — when to use this agent.
kind: worker                      # orchestrator | worker
mode: subagent                    # primary | subagent
tier: executor                    # planner | executor | fast (NEVER a model id)
allowed_tools: [read, grep, edit, bash, task]   # agnostic vocabulary
governance: [architecture, standards, domain-boundaries]
skills: [plan_and_execute_feature]              # bound governed skills
delegates_to: []                  # orchestrators list their workers
context_budget: medium            # small | medium | large
---
```

The body below the frontmatter is the agent's system prompt (XML-structured per
`.github/standards.md`).

## Tier abstraction

Agents reference a **tier**, never a concrete model id, so the same definition maps
to `claude-opus` on Claude Code and to a self-hosted `qwen`/`llama` on OpenCode.
The tier → model mapping per runtime is documented in `.github/portability.md`.

## Agnostic tool vocabulary

`read`, `grep`, `edit`, `bash`, `task`, `web` — each tool's projector maps these to
its own tool names (e.g. `read → Read`, `task → Task` for Claude Code). `task` is
the delegation primitive used by orchestrators.
