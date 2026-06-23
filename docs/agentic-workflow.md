# Agentic workflow

How to work in this repository when assisted by an AI coding agent (the primary
target is Claude Code, but the loop is tool-agnostic). The goal is simple: make the
high-quality path the path of least resistance. The harness — `CLAUDE.md`, the
governed skills, the slash commands, the hooks, and the memory/ADR conventions — all
exist to support this one loop.

> **Read this as learning, not just rules.** Each section explains *why* a practice
> exists and which failure it prevents. If you internalize the reasoning, you can
> apply it in any repo and with any tool — the specific commands here are just the
> local shortcuts.

## Mental model: why a "harness" at all?

A coding agent is fast but has no memory of yesterday and no instinct for *your*
project. Left to itself it will happily edit a file it never read, skip the tests,
and forget the reason for a decision the moment the session ends. None of those are
"the model being dumb" — they are **missing scaffolding**.

Harness engineering is the practice of building that scaffolding *around* the model
so the good path is the default path. Three ideas drive everything below:

1. **Context is the bottleneck, not intelligence.** Most bad output traces back to
   the agent acting without the right context loaded. So we front-load context
   (Ground) and we persist it across sessions (Compound).
2. **Cheap feedback beats careful generation.** It is faster to generate, run the
   tests, and react than to try to get it perfect blind. So we make verification
   constant and cheap (Verify).
3. **Leverage comes from parallelism and reuse.** One agent doing ten things in
   series is slow; ten agents doing one thing each is fast. Reusable skills and
   parallel subagents turn effort into leverage (Plan, Delegate).

The loop below is just these three ideas arranged in the order you hit them.

## The loop: Ground → Plan → Delegate → Verify → Compound

```
   ┌─────────┐   ┌──────┐   ┌──────────┐   ┌────────┐   ┌──────────┐
   │ Ground  │──▶│ Plan │──▶│ Delegate │──▶│ Verify │──▶│ Compound │
   └─────────┘   └──────┘   └──────────┘   └────────┘   └────┬─────┘
        ▲                                                     │
        └─────────────────────  next task  ──────────────────┘
```

### 1. Ground

Understand before you act. Read the relevant code, search broadly, and skim
`memory/context.md` and `memory/learnings.md` for prior context. Blind edits cause
rework — the most expensive failure mode in agentic coding.

- Tools: Read, Grep, Glob, or an `Explore` subagent for broad fan-out.
- Skill: `research_current_info` when facts may have changed since training.

### 2. Plan

For anything beyond a trivial change, write the plan down before touching code.

- Start a `TodoWrite` list of phased steps, each with the files it touches and how it
  will be verified.
- Ideation: `brainstorm_quick` (scoped) or the external `brainstorming` skill
  (design-impacting work that deserves a written, approved spec).
- Execution planning: `writing-plans` / `plan_and_execute_feature`.
- Command: `/plan`.

### 3. Delegate

When subtasks are independent, run them in parallel instead of grinding serially.

- Dispatch multiple subagents in one message (parallel `Task` calls).
- Use the governed agents in `.github/agents/` (orchestrator, planner, implementer,
  tester, documenter, reviewer) and the `subagent-driven-development` skill.
- Command: `/orchestrate`. Sequence only the genuinely dependent steps.

### 4. Verify

Confirm correctness continuously, not just at the end.

- Run `make test` (or focused `pytest`) after each substantive change.
- Close work with `make check` and the `requesting-code-review` / `verify_changes`
  skills. For bugs, drive `systematic_debugging` (reproduce → isolate → fix → verify).
- Command: `/verify`. CI is read-only — fix locally with `make fix` / `make format`.

### 5. Compound

Leave the repository's knowledge richer than you found it.

- Record significant, hard-to-reverse decisions as ADRs in `docs/adr/` (`/adr`).
- Record durable, non-obvious learnings in `memory/` (`/retro`, `retrospective`).
- Update `docs/` alongside code (CI enforces this for `src/` and `tests/` changes).

## What the harness gives you

| Piece | Where | What it does |
|-------|-------|--------------|
| Operating playbook | `CLAUDE.md` | The loop, as standing instructions for the agent. |
| Governed skills | `.github/skills/` → projected natively | Reusable, governed procedures for each step. |
| Slash commands | `.claude/commands/` | One-keystroke entry to `/plan`, `/orchestrate`, `/verify`, `/adr`, `/retro`. |
| Hooks (nudge) | `.claude/settings.json`, `.claude/hooks/` | Inject the loop at session start; remind to verify/compound when code changed. Non-blocking. |
| MCP servers | `.mcp.json` | Optional extra tools (library docs, git). Opt-in. |
| Memory | `memory/` | Persistent working memory across sessions. |
| Decisions | `docs/adr/` | The durable "why" behind the code. |

## Why this works

These are not arbitrary ceremonies; each step removes a known failure mode of
AI-assisted coding: acting without context (Ground), thrashing without a plan (Plan),
serial bottlenecks (Delegate), unverified output (Verify), and re-deriving lost
context every session (Compound). They also happen to be exactly what good agentic
practice looks like — which is why an honest assessment of agentic skill rewards them.
See `docs/agentic-scoring.md` for how this loop maps to such an assessment, and what a
template can and cannot influence.

## A worked example

Task: *"Add retry-with-backoff to the HTTP client."* Here is the loop in motion.

1. **Ground.** Read the current client, grep for where it is called, and skim
   `memory/learnings.md` — which warns that one caller already retries, so double
   retries would compound. *Without this step you would have shipped a bug.*
2. **Plan.** `/plan`: a `TodoWrite` list — (a) add backoff util, (b) wire it in,
   (c) make the existing caller stop retrying, (d) tests. One decision-changing
   question surfaced: max attempts? You ask before coding.
3. **Delegate.** Steps (a) and (d) are independent — dispatch two subagents in
   parallel via `/orchestrate`. Steps (b) and (c) are sequenced after (a).
4. **Verify.** `/verify` runs `make check`; a flaky-timeout test fails. You switch to
   `systematic_debugging`, reproduce it, find the test used real sleeps, fix it with a
   fake clock, and re-run green. *Cheap feedback caught it in minutes.*
5. **Compound.** `/adr` records "backoff lives in the client, callers must not retry"
   so nobody re-introduces double retries. `/retro` notes the fake-clock trick in
   `memory/patterns.md`. The next person — human or agent — inherits both.

Notice no step was heavy. The loop is a habit, not a process you "do" — it scales
down to a one-line fix and up to a multi-day feature.

## Common mistakes (and the step that prevents each)

| Mistake | Why it happens | Prevented by |
|---------|----------------|--------------|
| Editing a file you never read | The agent is eager; you didn't slow it down | **Ground** |
| Thrashing — 8 edits to the same file | No plan, so each edit reacts to the last failure | **Plan** + `systematic_debugging` |
| Doing independent subtasks serially | Easier to think one-at-a-time | **Delegate** |
| "It looks right" without running it | Verification feels like overhead | **Verify** (make it cheap and it stops feeling that way) |
| Re-learning the same gotcha every week | Knowledge died with the session | **Compound** |
| Ceremony for its own sake (ADR for a typo) | Cargo-culting the loop | Judgment — scale each step to the task |

## If you remember one thing

**Load the right context before acting, and persist what you learn after.** Grounding
and Compounding are the two highest-leverage habits because they attack the real
bottleneck — context — at both ends of the loop. The middle three (Plan, Delegate,
Verify) are how you spend that context well.
