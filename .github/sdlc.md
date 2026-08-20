# AI-Assisted SDLC (Level 5)

This file defines the governed software development lifecycle. Each phase has a
single owning **agent** (`.github/agents/`), a bound **skill** (`.github/skills/`),
and an **exit gate** that is a `make` target — so progression is enforced by the
system, not by model goodwill.

## Phase order and gates

| # | Phase     | Owner agent   | Bound skill                     | Exit gate (must pass to advance)      |
|---|-----------|---------------|---------------------------------|---------------------------------------|
| 1 | plan      | `planner`     | `plan_and_execute_feature`      | Plan written, scoped, and reviewed    |
| 2 | implement | `implementer` | `plan_and_execute_feature` (`execute_only` mode) | `make fix` clean (local) |
| 3 | test      | `tester`      | `generate_e2e_tests`            | `make check` green                    |
| 4 | document  | `documenter`  | `generate_implementation_docs`  | `docs/` updated (CI docs rule passes) |
| 5 | review    | `reviewer`    | `validate_module_structure`     | `make check` + `make check-sync` green|

The `orchestrator` agent is the runtime embodiment of this lifecycle: it delegates
each phase to its owner and refuses to advance a phase without evidence its exit
gate is green.

## Rules

1. **Plan first.** No non-trivial change starts without a plan (Level 4,
   `.github/orchestration.md`).
2. **One phase at a time.** Complete and gate a phase before starting the next.
3. **Gates are system-enforced.** A red `make` gate blocks the phase — do not
   bypass it or weaken checks to pass.
4. **Docs are a phase, not an afterthought.** The CI docs rule fails any `src/` or
   `tests/` change without a matching `docs/` update; the `document` phase satisfies
   it deliberately.
5. **CI is read-only.** Use `make fix` / `make format` locally; CI only verifies
   (`make ci` = `make check` + `make check-sync`). See `.github/automation.md`.

## Operating discipline for agents

These rules apply to any agent (any tool) executing a non-trivial coding task.

6. **Mandatory skill flow for full features.** For a new feature, behavior change,
   UI/game, or multi-step implementation, use the governed skills in order — do not
   jump straight to code:
   1. `brainstorming` when requirements/design are not already fixed.
   2. `writing-plans` or `plan_and_execute_feature` before editing code.
   3. `test-driven-development` before implementation when behavior is testable.
   4. `verify_changes` before declaring completion.
   5. `requesting-code-review` for major features or benchmark deliverables.

   If you skip a skill, state the reason in the run trace. Skip straight to a tiny
   edit only when the user explicitly asks for one.
7. **Validate before claiming done.** Never report completion without running the
   repository's validation gate (prefer `make` targets) and reporting the exact
   commands and their results. If no suitable target exists, run the most relevant
   tests/lints directly and explain the fallback.
8. **Minimize human interventions.** Before asking the user, inspect the repo, read
   relevant files, run safe read-only commands, and make a reasonable assumption when
   the next step is low-risk and reversible. Ask only for destructive actions,
   secrets/credentials, ambiguous product decisions, or irreversible choices — and
   when you do, state the exact blocker, what you tried, and 1-3 concrete options.
9. **Stay in scope.** For task- or benchmark-scoped work, edit only inside the
   requested directory unless the user authorizes repository-level changes. If a
   change outside scope seems necessary, stop and explain why before editing.

## Run trace & handoff

Leave an auditable trace so a reviewer (human or LLM) can reconstruct the run:

- **At start:** state the governed files you read, the skills you will use (and why),
  the edit/path boundary, and a short plan with validation steps.
- **At end:** a concise handoff — skills actually used, files changed, gates/tests run
  with results, any skipped validation and why, assumptions made, and any human
  intervention required.

## Degraded runtimes

On a runtime without sub-agent delegation, the `orchestrator` runs the same phases
itself in order, treating each specialist agent's prompt as a labeled section. The
phase gates are unchanged. Portability across runtimes (e.g. Claude Code →
self-hosted via OpenCode) is governed by `.github/portability.md`.
