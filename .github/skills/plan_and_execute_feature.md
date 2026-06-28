---
name: plan_and_execute_feature
description: Use when delivering a feature through explicit planning, phased execution, validation, and governed handoff — or when implementing/fixing already-scoped engineering work via the execute_only mode.
---

# Skill: plan_and_execute_feature

## Purpose

Deliver a feature through explicit orchestration phases with architecture-safe execution.

## Mode

- `mode: full` (default) — run all five phases below, from planning to validation.
- `mode: execute_only` — a plan is already approved; skip Phases 1-2 and enter directly
  at Phase 4 (Execution), then Phase 5 (Validation). Use this for implementing a feature,
  fixing a bug, or executing scoped engineering work through governed orchestration
  instead of ad-hoc generation.
- `mode: local_model_32k` — for small self-hosted models (Qwen 3.x / 3.6, 27B-35B @ 32k)
  driving spikes / vibe coding via Copilot / OpenCode. Never deliver in one response: emit a
  small single-file backlog and execute one step per turn. See the weak-model section below
  and `LOCAL_AGENT.md`.

## Required Input

- Feature request.
- Business outcome.
- Affected modules and boundaries.
- Non-functional requirements (performance, security, observability).
- Test expectations.
- Optional `mode` (`full` | `execute_only`). For `execute_only`: the approved plan,
  scope constraints (files/modules in and out of scope), and acceptance criteria.

## Output Format

- Phase report (1 to 5).
- Approved implementation plan.
- Execution backlog: the plan as an ordered list of steps where each step is
  independently verifiable (compiles / a test passes / it runs), names the one or
  few files it touches, and is annotated with the target tier
  (`planner` / `executor` / `fast`). Size steps to that tier — see
  `docs/task-sizing.md`.
- Selected skills map.
- Code/test/doc change summary.
- Validation checklist with pass/fail evidence.

## Phases

### Phase 1 - Planning

- Clarify scope, assumptions, and acceptance criteria.
- Build a step-by-step implementation plan.

### Phase 2 - Architecture Validation

- Validate intended changes against:
  - `.github/architecture.md`
  - `.github/domain-boundaries.md`
- Adjust plan to preserve dependency direction and module placement.

### Phase 3 - Skill Selection

- Select internal operational skills needed for execution.
- Define invocation order and dependencies between skills.

### Phase 4 - Execution

- Before editing files, publish a short visible plan: scope, files likely to change,
  tests to add or run, risks/assumptions. (In IDE agents like Antigravity that lack a
  separate plan surface, state it inline before the first edit.)
- Implement changes following selected skills and plan order.
- Execute in small checkpoints; update the plan when it changes.
- Keep modifications scoped and documented.

### Phase 5 - Validation

- Validate against `.github/automation.md` and `.github/orchestration.md`.
- Review diffs, run required checks, and produce final summary.

## Small-context / weak-model mode

When the executor is a weak or self-hosted model (small context window, weaker
instruction-following), the default full flow can loop without converging. Adapt:

- Treat the work as a `spike` first (flat structure, runnable milestone) and
  promote to clean architecture only once it runs — see `docs/task-sizing.md`.
- Execute **one file (or one function) per turn**; run or test it before the next.
- Make the first deliverable the smallest thing that **runs**, not the most
  correct or complete one; add one capability per loop.
- Keep the same model for planning and execution, or a planner only slightly
  stronger, so backlog steps stay sized to what the executor can chew.
- If a step cannot be stated as "produce/modify this one file and verify it", it
  is too big — split it before executing.

Under Copilot agent mode specifically (`mode: local_model_32k`), add an output discipline —
weak models otherwise try to regenerate whole files and fail with `Response too long`:

- **Edit, never rewrite.** Change only the wrong lines; never regenerate a whole file.
- **One root cause per turn.** If asked to "fix everything", fix the single most critical
  cause, list the rest deferred (one line each), and stop.
- **Small output, fixed shape:** `Target file:` / `Expected change:` / `Validation:`, then the
  edit, then a one-line result. No analysis or summary prose (it accelerates compaction).
- This is enforced mechanically by a gateway `max_tokens` cap — see `LOCAL_AGENT.md` and
  [`../../docs/local-model-runtime-config.md`](../../docs/local-model-runtime-config.md).

## Execution Rules

1. Internal skills have precedence over external synced skills.
2. In `mode: full`, no direct implementation without completing Phase 1 and Phase 2.
3. Prefer minimal file churn and strict scope boundaries.
4. Include tests and `docs/` updates when behavior changes.
5. Escalate blockers with explicit assumptions and next actions.
6. Report open risks and deferred items explicitly.
