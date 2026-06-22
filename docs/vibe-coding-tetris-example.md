# Vibe-Coding a Tetris Game: How Each AI Tool Executes the Same Task

## The Prompt

```
Operas dentro de un repositorio para python con docker, tu objetivo es construir
un juego funcional de tetris en python, el juego debe ser completamente jugable
mediante teclado, incluir estados, menu juego, niveles y renderizado grafico
```

**Intent:** Build a fully playable Tetris game in Python inside this Docker-enabled
repository. Requirements: keyboard input, game states (menu, playing, paused,
game-over), level progression, and graphical rendering (curses or pygame).

---

## 1. Claude Code

### Session bootstrap
Claude Code reads `CLAUDE.md` and immediately loads three governance files:
`.github/architecture.md`, `.github/standards.md`, `.github/domain-boundaries.md`.
Native skills are discovered from `.claude/skills/` (symlinks refreshed with
`make setup-claude-skills`).

### Skills activated

| Skill | Why |
|---|---|
| `plan_and_execute_feature` | New feature from scratch — `mode: full` for the plan; `mode: execute_only` for each scoped module implementation |
| `create_domain_contract` | Game loop, state machine, input handler are discrete application-layer use cases (`contract_type: use_case`) |
| `generate_e2e_tests` | Keyboard-driven flow needs end-to-end test coverage (simulate keypress → assert state) |
| `generate_implementation_docs` | CI docs-quality rule blocks any `src/` change without a matching `docs/` update |
| `validate_module_structure` | Final review: verify domain/application/infra layers don't bleed into each other |

`create_domain_contract` with `contract_type: repository` is unused (no persistence). `generate_migration_plan` is skipped (greenfield).

### Agents involved (real sub-agent delegation)

The `orchestrator` (`.claude/agents/orchestrator.md`, tier: `planner` → `claude-opus-4-8`)
drives the lifecycle and delegates each phase:

1. `planner` — produces the implementation plan (tier: `planner`).
2. `implementer` — writes all code (tier: `executor` → `claude-sonnet-4-6`).
3. `tester` — writes and runs tests (tier: `executor`).
4. `documenter` — updates `docs/` (tier: `fast` → `claude-haiku-4-5`).
5. `reviewer` — adversarial final review (tier: `planner`).

The orchestrator reads `.github/sdlc.md` and refuses to advance a phase without
evidence its exit gate is green.

### SDLC phase sequence

| Phase | Owner | Exit gate |
|---|---|---|
| plan | `planner` | Plan written and reviewed |
| implement | `implementer` | `make fix` clean locally |
| test | `tester` | `make check` green |
| document | `documenter` | `docs/tetris.md` created; CI docs rule passes |
| review | `reviewer` | `make check` + `make check-sync` both green |

### Expected output tree

```
src/tetris/
  domain/
    board.py          # Board entity, cell values, boundary logic
    piece.py          # Tetromino shapes, rotation state machine
    scoring.py        # Score, level, lines-cleared value objects
  application/
    game_loop.py      # Use case: tick, move, rotate, lock, clear
    state_machine.py  # States: MENU, PLAYING, PAUSED, GAME_OVER
    input_handler.py  # Keyboard event → game command mapping
  infrastructure/
    renderer.py       # curses/pygame rendering adapter
    clock.py          # Game clock / frame-rate adapter
  main.py             # Entry point: wires all layers, starts loop
tests/tetris/
  test_board.py
  test_piece.py
  test_state_machine.py
  test_e2e_keyboard.py  # Simulated keypress → assert state transitions
docs/
  tetris.md           # What changed, why, how to run, validation evidence
```

---

## 2. OpenCode

### Session bootstrap
OpenCode reads `opencode.json` → `OPENCODE.md`. That file uses five explicit levels
(governance → skills → automation → orchestration → agents/SDLC), so OpenCode
processes them in declared order. Same three governance files load.
Native skills in `.opencode/skills/`, agents in `.opencode/agents/`
(both refreshed with `make sync-skills` / `make sync-agents`).

### Skills activated
Identical set — same governed source. OpenCode reads
`.opencode/skills/plan_and_execute_feature/SKILL.md` (symlinked from `.github/skills/`).

### Agents involved (real sub-agent delegation)
Same six agents via `.opencode/agents/` (projected from `.github/agents/` by
`make sync-agents`), each carrying a `permission` map so tool access is governed
(planner read-only, implementer may edit/run). OpenCode's delegation primitive maps
to its own task tool.

**Model tier difference (self-hosted path):** when the Claude token budget runs out,
the tier → model mapping in `.github/portability.md` maps:
`planner → qwen2.5-coder:32b`, `executor → qwen2.5-coder:14b`, `fast → llama3.1:8b`
(Ollama or LM Studio). Not yet active in `opencode.json` — activate when needed.

### SDLC phase sequence
Identical gate sequence to Claude Code. Behavioral difference: `OPENCODE.md` Level 4
states "Do not generate large outputs without first invoking the relevant skill."
This makes skill-invocation *mandatory* before bulk code generation.

### Expected output
Same file tree. SDLC phases, make gates, and docs requirement are identical because
both tools read `.github/sdlc.md`.

---

## 3. Codex (OpenAI Codex UI)

### Session bootstrap
Codex reads `AGENTS.md` at session start. Skills are referenced directly from
`.github/skills/` — there is no `.codex/skills/` native directory. The model must
read skill files proactively when a skill is invoked in the prompt context.

### Skills activated
Same governed set. The model reads `.github/skills/<name>.md` files directly.
Risk: if the model skips reading the skill file, it may omit the structured
execution phases defined in the skill (no manifest or trigger enforces it).

### Agents involved — collapsed mode
Codex has **no native sub-agent delegation**. The governed agents in `.github/agents/`
are read as behavioral prompts within the single context. Codex internalizes all
five phases in one conversation thread, using each agent's `<instructions>` block
as a labeled section.

Per `.github/sdlc.md` ("Degraded runtimes"):
> On a runtime without sub-agent delegation, the orchestrator runs the same phases
> itself in order, treating each specialist agent's prompt as a labeled section.

The make gates still apply — Codex must demonstrate `make check` passing before
advancing to the document phase.

### SDLC phase sequence
Same five phases, same gates. All phases run inside one context window. Large tasks
may hit context limits; Codex will need to summarize and continue rather than
delegating to a fresh context.

### Expected output
Same file tree. The absence of sub-agent delegation changes *efficiency*, not *what
gets built*.

---

## 4. Antigravity (Google Gemini IDE)

### Session bootstrap
Antigravity reads `.agents/rules/GEMINI.md` with `trigger: always_on` — injected
into every session automatically, before any prompt is processed. Governance files
load via `@`-style references:

```
@.github/architecture.md
@.github/standards.md
@.github/domain-boundaries.md
```

Native skills in `.agents/skills/` are **copied files** (not symlinks) plus
`.agents/skills/.generated-manifest.tsv`. Governed source: `.github/skills/`.

### Skills activated
Same governed set. Antigravity reads `.agents/skills/plan_and_execute_feature/SKILL.md`.
The `@`-style reference allows explicit skill injection (e.g. `@.agents/skills/plan_and_execute_feature/SKILL.md`).

### Agents involved — collapsed mode
Antigravity has no native sub-agent delegation protocol. The `.github/agents/`
files serve as behavioral context references. All SDLC phases collapse into one
session (same as Codex).

**Key advantage over Codex:** the `always_on` trigger guarantees governance and skill
context is pre-loaded before any code generation — the model cannot skip it.

### SDLC phase sequence
Same five phases and gates. The `always_on` trigger is the main operational
advantage: governance context is guaranteed active before any code is generated.

### Expected output
Same file tree.

---

## 5. GitHub Copilot

### Session bootstrap
Copilot reads `.github/copilot-instructions.md` at workspace open. It loads the
same three governance files and references skills directly from `.github/skills/`
and `.github/skills-external/`. No native skill directory.

### Skills activated
Same governed set. Copilot reads `.github/skills/plan_and_execute_feature.md` when
it recognizes this is a feature-delivery task. Activation depends entirely on the
model reading the instruction to check skill files before generating.

### Agents involved — collapsed mode
No sub-agent delegation. All SDLC phases collapse into chat or inline editor
suggestions. Copilot's workflow is the most tightly integrated with the editor:
it generates code inline, which makes the plan/implement separation weaker unless
the developer explicitly asks for a plan first.

### SDLC phase sequence
Copilot does not enforce phase ordering automatically. A disciplined developer can
replicate the full SDLC by asking Copilot Chat for a plan first. Make gates apply
at the developer's discretion (Copilot cannot run `make check` autonomously without
Copilot Workspace).

### Expected output
Same file tree when following the governed workflow. Without explicit phase
discipline, Copilot may produce a flat implementation that skips the clean-architecture
layer separation the plan phase would have established.

---

## Cross-Tool Comparison

| Dimension | Claude Code | OpenCode | Codex | Antigravity | Copilot |
|---|---|---|---|---|---|
| **Bootstrap file** | `CLAUDE.md` | `opencode.json` → `OPENCODE.md` | `AGENTS.md` | `.agents/rules/GEMINI.md` (`always_on`) | `.github/copilot-instructions.md` |
| **Skill discovery** | Symlinks in `.claude/skills/` | Symlinks in `.opencode/skills/` | Direct read from `.github/skills/` | Copies in `.agents/skills/` + manifest | Direct read from `.github/skills/` |
| **Agent delegation** | Real sub-agents (`.claude/agents/`) via `Agent` tool | Real sub-agents (`.opencode/agents/`) | Collapsed inline | Collapsed inline | Collapsed inline |
| **Model tiers used** | planner=opus-4-8, executor=sonnet-4-6, fast=haiku-4-5 | Cloud default; self-hosted via `.github/portability.md` | Single model (GPT-4o/o1) | Single model (Gemini 2.x) | Single model (GPT-4o) |
| **Governance auto-load** | Explicit instruction | Explicit + Level 5 structured | Explicit instruction | Guaranteed (`always_on`) | Explicit instruction |
| **Phase gate enforcement** | System-enforced via orchestrator | System-enforced via orchestrator | Model-honor only | Model-honor only | Manual |
| **`make check` gate** | Enforced between phases | Enforced between phases | Referenced in `AGENTS.md` | Referenced in `GEMINI.md` | Referenced in instructions |
| **`make check-sync` gate** | Enforced at review phase | Enforced at review phase | Referenced | Referenced | Referenced |
| **Self-hosted fallback** | No (cloud only) | Yes (Ollama/LMStudio via portability.md) | No | No | No |

---

## Why Polyvalent: Same Result, Different Paths

Each tool takes a structurally different path:

- **Claude Code / OpenCode** fan the work across five specialist sub-agents, each
  with a fresh context and a scoped model tier, with automated gate enforcement.
- **Codex / Antigravity / Copilot** collapse all phases into a single context and
  rely on model discipline rather than tooling primitives.

Despite this, the **expected final output is equivalent** because all five tools
share the same governed source of truth: `.github/skills/` definitions,
`.github/agents/` behavioral prompts, `.github/sdlc.md` gate table, and `make`
targets that mechanically verify quality. The polyvalent design separates *what*
the AI should do (governed, tool-agnostic) from *how* each tool executes it
(adapter-specific).

Adding a sixth tool requires one entry in `adapters/registry.toml` + one Jinja2
template — the skills, agents, and SDLC gates remain untouched. The governed
constraints travel with the repository, not with any specific AI product.

---

## Lessons from running this on self-hosted models

Strong cloud models (Copilot, Codex) converge on this prompt. Weak self-hosted
models (e.g. `qwen3-coder-30b` via OpenCode) tend not to — the interaction runs
long and produces no playable game. The cause is rarely the prompt; it is runtime
and sizing: a too-small served context window, too-high temperature, a
strong-planner/weak-executor gap, and a task handed over whole instead of sliced
into a runnable-first backlog.

The full reasoning and the heuristics (chunk size per tier, complexity tiers, the
"first runnable milestone" loop) are in [`task-sizing.md`](task-sizing.md); the
runtime levers are in [`../.env.example`](../.env.example) and
[`../.github/portability.md`](../.github/portability.md). For a weak self-hosted
build model, treat this prompt as a `spike` (flat, runnable-first), not the full
five-phase `feature` flow shown above.
