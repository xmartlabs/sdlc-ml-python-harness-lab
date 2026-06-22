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

## Degraded runtimes

On a runtime without sub-agent delegation, the `orchestrator` runs the same phases
itself in order, treating each specialist agent's prompt as a labeled section. The
phase gates are unchanged. Portability across runtimes (e.g. Claude Code →
self-hosted via OpenCode) is governed by `.github/portability.md`.
