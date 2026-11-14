# ADR-0079: A hard case is paced in phases, and its agent cannot read its grade

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Marcos Soto
- **Related:** [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0071](0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md),
  [ADR-0076](0076-per-step-reward.md),
  [ADR-0078](0078-a-case-can-name-its-own-machine.md)

## Context

The first frontier run of `chess3d-v1` (`K-001`, claude-opus-4-8, variant `default`,
`lab_root`) scored 41/41. Reading it showed that it measured less than the score
suggests.

- **One session did everything.** The model got the whole game in one prompt, worked
  for 59 minutes and 135 turns (US$10.43), and ended its own run in step 1. Per-step
  reward had a single point. A smaller model would get the same all-at-once prompt
  and nothing would show how far it got. Planning was only *asked* for, in the
  `harness` variant's preamble, and only step 1 carried that preamble, because each
  step is a fresh agent session.
- **The multi-step mechanism had an off-by-one.** The case form and the drafting
  prompt say `step_prompts[0]` drives step 2. The runner read
  `step_prompts[step_n - 1]`, so a multi-step case skipped its first authored step
  and never ran its last. No stored case used it, which is how it went unnoticed.
  `task.yaml` could not declare step prompts at all.
- **The agent read the lab's notes on its own case.** It grepped
  `memory/learnings.md` and read the entry written that day about the grader's
  pitfalls. In a `lab_root` run `memory/` is part of the treatment (ADR-0049), so
  the note did not belong there.
- **The answer was on the agent's disk.** `data/reference/chess3d-v1/` holds a
  solution that passes every check. It is git-ignored, but the agent's container
  bind-mounts the whole repository, and `cat` does not read `.gitignore`.
- **The location header told every case to build `main.py`.** That is right for the
  pygame cases and wrong for a web page or a Godot project.

## Decision

1. **A case can declare phases.** `phases` in `task.yaml` lists one prompt file per
   phase after the first; phase 1 is the case prompt itself. The seed stores them
   as `step_prompts` and sets `max_steps = 1 + len(phases)`, and a re-seed
   refreshes both. Phase files must live inside the case's directory.
2. **Every phase stands alone.**
   - Each stored phase carries the case's contract: the block of its default prompt
     between `## What is being judged` and `## How the work is paced`. The agent that
     runs a phase has not seen step 1's prompt.
   - Under the `harness` variant, the worker prepends the same governance preamble
     `make prompts` uses, so governance applies to every phase, not only the first.
3. **The runner paces the phases.**
   - `step_prompts[step_n - 2]` drives step `step_n`, as documented.
   - While authored phases remain, a completion signal does not end the run, and the
     phase's prompt says to stop at the end of that phase rather than inviting the
     agent to declare victory.
   - `no_progress` still ends a run whose step changed nothing.
4. **The agent's container cannot read its grade.** `RunExecutor.start` takes
   `hidden`. For the agent's container it is always passed: the lab's
   `data/reference/` plus the case's own `agent_hidden` paths (its grader, rubric and
   notes), each restricted to `benchmarks/<case>/`. Directories are masked with
   tmpfs and files with empty binds, the same mechanics as ADR-0049. The validation
   lane is never given these masks.
5. **The lab never writes how a case is graded where a run can read it.** `memory/`
   records general lessons; a case's grading details go in its own hidden files or
   in an ADR.
6. **A case names its entry point.** `entry_point` in `task.yaml` replaces `main.py`
   in the prompt's location header: `project.godot` for chess and `index.html` for
   the two web cases. Unset, it stays `main.py`, so the pygame cases are unchanged.

`chess3d-v1` now runs in five phases:

1. Rules core: movement, castling, en passant, pins, promotion, FEN clocks, perft.
2. Notation and end of game: SAN in and out, history, status, result and every draw
   rule.
3. The 3D board and the mouse.
4. The computer opponent.
5. Finish: play end to end and polish.

The phases follow the rubric's ladder, so the per-step curve says which milestone a
model reached.

## Consequences

- A run of a phased case is up to five agent sessions. A frontier model's cost and
  time are spread over phases instead of one long session, and a smaller model's
  partial progress is measured per phase instead of lost.
- The `default` vs `harness` comparison now applies the treatment to every phase.
  That comparison is what answers whether the governance earns its keep.
- Verified through the worker's real code path:
  - The agent's container saw no references, no grader and an empty rubric.
  - The validation lane saw all three, and the reference still passes 41/41.
  - The `harness` variant carried its preamble into all four later phases.
- **Not closed:** a committed grader is still readable with `git show HEAD:…`, because
  the runner needs the lab's `.git` for step snapshots. The grader's probes are
  generated per run (ADR-0078), so reading it reveals the method, never the answers.
  Moving targetless workspaces outside the lab tree, with a `.git` of their own, would
  close it, and is a larger change than this one.
- The 3D lane's lessons in `memory/learnings.md` were rewritten without the grader's
  details.
