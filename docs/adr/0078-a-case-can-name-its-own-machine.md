# ADR-0078: A case can name its own machine, and its game can be played

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Marcos Soto
- **Related:** [ADR-0002](0002-celery-docker-per-run-execution.md),
  [ADR-0056](0056-cases-are-matched-against-runner-capabilities.md),
  [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md),
  [ADR-0062](0062-a-check-the-environment-failed-is-not-a-verdict.md),
  [ADR-0067](0067-a-frontend-case-is-graded-in-a-real-browser.md),
  [ADR-0071](0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md),
  [ADR-0073](0073-a-measurement-names-its-real-instrument-and-its-reach.md)

## Context

The newest frontier models are expected to build working 3D games, not just code
that passes unit tests. The lab could not measure that. Every case ran in one runner
image, and adding a game engine to that image would make every Tetris and SWE-bench
run pull and start it. Every case's tests were also pytest or Playwright, so nothing
could check a 3D scene.

Measuring this needs three things the lab lacked:

1. A toolchain only some cases need.
2. A grader that drives a game engine rather than a function call.
3. A way for a person to play what a run built, since "high quality" is partly a
   judgement only a player can make.

A spike settled the technical question first. Godot 4.7.2 in Docker on arm64, with
no GPU, renders through Xvfb and Mesa llvmpipe with the Compatibility renderer. A
mouse event injected at a square's projected pixel reaches the game, and pixels can
be read back. All of this runs inside the hardened validation lane: no network,
`cap_drop=ALL`, 4 GB. One finding constrained the design: under `--headless`,
`frame_post_draw` never fires, so pixel checks need the virtual display.

## Decision

1. **A case may name its own runner image, sandbox image and play command** in
   `task.yaml` (`runner_image`, `sandbox_image`, `play_command`), stored in
   `Case.metadata_json` and refreshed on re-seed like `starter_dir`.
   `case_profile.py` validates them.
   - Images must be the lab's own local images (`harness-lab-<name>[:tag]`). A case
     file can therefore never make the worker pull or run an arbitrary image.
   - A play command is a single short line.
   - A bad value fails at seed time, or fails the run before any container starts,
     with the reason. It never falls back silently to the shared image.
2. **One image for the agent and its grader.** `RunExecutor.start` takes `image=`.
   The agent's run, every validation command and its setup step, and per-step
   reward all use the case's image. A target repository's own gates keep the shared
   runner, because they are the repository's commands. A call is unchanged when the
   case names no image.
3. **The 3D images are built on demand.** `Dockerfile.runner-godot` and
   `Dockerfile.sandbox-godot` extend the shared images with a pinned Godot and a
   software OpenGL stack. `make images-3d` builds them; `make app-up` does not.
4. **A Play tab.** A third sandbox kind, `play`, opens the noVNC desktop in the
   case's sandbox image and starts its `play_command` there. The command runs once
   under `bash -c`; its exit report is written to a file and displayed with `cat`,
   so a quote in the command can never break or inject into a second shell. The
   run page shows the tab only when the case declares a game.
5. **Held-out data is generated, never hidden.** A workspace without its own
   repository is a directory of the lab's tree. The runner needs the lab's `.git`
   to snapshot steps, so anything committed is one `git show` away from the agent,
   however its working-tree copy is masked. A `hidden_paths` mask was written and
   removed for that reason: it would have given false confidence. Graders seed
   their probes with the run id instead.
6. **Calibration references stay out of git.** `data/reference/*` is ignored, except
   the pre-existing `swebench_official.json`. A test holds the repository to that
   rule.

`chess3d-v1` is the first case.

- **Grading.** A GDScript harvester drives the agent's `Chess` autoload and 3D
  scene. Python then decides 39 checks against python-chess. The rules checks
  cover legal moves, en passant, castling, pins, double check, under-promotion,
  standard and random perft, SAN in and out with disambiguation, clocks, undo,
  every draw rule, and forced mates in one and two. The scene checks cover grid
  geometry, visibility, contrast, piece placement, a mouse line with en passant
  and both castles, the board following the engine, click promotion, the status
  HUD, the AI answering a mouse move, rendering, and frame time.
- **Calibration.** Run through the worker's real `ValidationService` in the
  hardened lane: an empty workspace scores 0/39, a plausible weaker build 24/39,
  and the reference 39/39.

## Consequences

- The lab can measure whether a harness lets a model build a playable 3D game, and
  how far up a 39-step ladder each model gets. Smaller models are expected to stop
  partway, and the per-step curve shows whether more iterations move them.
- The 3D images add about 230 MB each and are only built when someone wants these
  cases.
- A full harvest takes about a minute, so validation takes one to two minutes per
  run. Per-step reward pays that again for each step.
- Frame time is measured on a CPU renderer. It is an observation that is comparable
  between runs and says nothing about a GPU (ADR-0073).
- The tamper guard passes only once the grader is committed, as for every case that
  ships its tests (ADR-0071).
- The racing and open-city cases reuse every mechanism here: the image, the Play
  tab, a harvester that drives the engine, and run-seeded held-out content (tracks,
  an OpenStreetMap neighbourhood).

## Alternatives considered

- **Install Godot in the shared runner.** Rejected: every other case would pay for
  an engine it never uses. This is what the user asked to avoid.
- **A separate grader image only.** Rejected: the agent could not run its own game,
  and the grade would come from an engine the agent never saw.
- **Hide held-out files from the agent with a mount.** Rejected: the lab's `.git` is
  readable in the agent's container, so the mask would not hold (Decision 5).
- **Grade from screenshots with a vision judge.** Deferred: exact rules and
  scene-graph checks are reproducible and cheap. A visual-quality judgement can be
  added later as a separate JUDGEMENT score, never inside the pass rate.
