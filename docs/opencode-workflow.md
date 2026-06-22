# OpenCode workflow: plan big, execute self-hosted

How to drive a task in OpenCode using two models — a large cloud model for
planning and a self-hosted model for execution — without juggling model switches
by hand. See `.github/portability.md` for the underlying config.

## The one principle: the model follows the agent

Each primary agent has its model pinned in `opencode.json`:

```jsonc
"agent": {
  "plan":  { "model": "{env:OPENCODE_MODEL_PLAN}" },   // big "thinking" model (default: Zen free Nemotron Ultra)
  "build": { "model": "{env:OPENCODE_MODEL}" }          // self-hosted (e.g. LM Studio Qwen Coder)
}
```

So **switching agent with `Tab` switches the model automatically.** You do **not**
change the model by hand. Avoid `/models` — a manual pick *overrides* the agent's
model for the session (that override is usually why the wrong/catalog model shows up).

The active model is shown in the TUI bottom bar; after `Tab` it should change to the
new agent's model. If it doesn't, a manual `/models` override is still in effect.

## The loop

1. **Plan mode** (big model). Design the change. `plan` is read-only — it cannot edit
   files, which is exactly what you want while thinking.
2. **`Tab` → Build mode** (self-hosted). The conversation (your plan) carries over; the
   model switches to local automatically.
3. In Build, say "implement the plan above". Heavy code generation runs on your GPU,
   at zero token cost.

The `Tab` happens at the **plan → implement** boundary of the SDLC.

## Where skills run

A skill is just instructions the active model reads — it runs on **whatever agent's
model is active**, and does not switch models itself. Pick the mode by what the skill does:

| Skill | Mode | Why |
|---|---|---|
| `brainstorm_quick`, `brainstorming` | **Plan** | Ideation/design — use the big model; read-only anyway |
| `plan_and_execute_feature` (`mode: full`, phases 1-2) | **Plan** | Planning |
| `research_current_info` | **Plan** | Search/analysis, writes no code |
| `create_domain_contract` | **Build** | Writes code |
| `generate_e2e_tests` | **Build** | Writes tests |
| `plan_and_execute_feature` (`mode: execute_only`) | **Build** | Implements |
| `generate_implementation_docs` | **Build** | Writes docs |

Mental rule: a skill that **thinks/designs** → Plan (big model); a skill that
**writes/executes** → Build (local). Invoking a writing skill in Plan mode will fail to
edit — that failure is the signal "stop planning, `Tab` to Build".

## Worked example (the Tetris task)

1. **Plan** (Nemotron): `use brainstorm_quick for the tetris game` → diverge on options
   (curses vs pygame, states, levels). Then `plan_and_execute_feature` writes the plan.
2. **`Tab` → Build** (local Qwen Coder): "implement the plan: start with
   `tetris_game/domain/board.py`…". Code is written by the local model.
3. Stay in Build for tests and docs (`generate_e2e_tests`, `generate_implementation_docs`).

## Quick reference (`.env`)

```bash
OPENCODE_MODEL_PLAN=opencode/nemotron-3-ultra-free            # Plan agent (Zen, free)
OPENCODE_MODEL=lmstudio/qwen/qwen3-coder-30b                  # Build agent (self-hosted)
OPENCODE_SMALL_MODEL=ollama/qwen3:8b                          # lightweight (titles, etc.)
```

The default planner uses **OpenCode Zen** (free): run `opencode auth login` once, no
extra key needed. Launch with `make opencode` (loads `.env`); check local endpoints
with `make opencode-doctor`. Other free Zen planners: `opencode/deepseek-v4-flash-free`,
`opencode/mimo-v2.5-free`, `opencode/north-mini-code-free`. To use NVIDIA NIM instead,
set `OPENCODE_MODEL_PLAN=nvidia/nvidia/nemotron-3-super-120b-a12b` + `NVIDIA_API_KEY`.
