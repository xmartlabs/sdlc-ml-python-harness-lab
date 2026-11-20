# 3D game cases: their own machine, a grader that drives the engine, and a Play tab

Some cases ask for more than code that passes tests: a 3D game a person can play.
These cases are aimed at frontier models, and they need a game engine. Installing
that engine in the runner every case uses would slow down every other run, so a 3D
case brings its own image, and every person can play what a run built.

The lane has three cases, each harder in a different way:

| Case | What it asks for | What makes it hard |
|---|---|---|
| [`chess3d-v1`](../benchmarks/chess3d-v1/README.md) | 3D chess with the mouse and an AI | exact rules, a search |
| [`racing3d-v1`](../benchmarks/racing3d-v1/README.md) | a racing game with physics, laps and AI rivals | physics, race rules, AI on a track it never saw |
| [`opencity3d-v1`](../benchmarks/opencity3d-v1/README.md) | an open city built from OpenStreetMap data, on foot and by car, with traffic | geometry from real data, collisions, traffic on a map it never saw |

The decisions behind the lane are
[ADR-0078](adr/0078-a-case-can-name-its-own-machine.md) (own machine, Play tab) and
[ADR-0079](adr/0079-a-hard-case-is-paced-in-phases-and-cannot-read-its-grade.md)
(phases, isolation). A test holds every case with `category: game-3d` to both.

## What a case can declare

Three optional keys in `task.yaml`, stored in the case's metadata:

| Key | What it does |
|---|---|
| `runner_image` | The image the agent **and** the grader run in. Only the lab's own images (`harness-lab-<name>[:tag]`) are accepted. |
| `sandbox_image` | The image the Terminal, Desktop and Play tabs open in. |
| `play_command` | What the **Play** tab starts on its desktop, from the workspace copy at `/work`. One line. |
| `entry_point` | What the prompt names as the entry point (`project.godot`); `main.py` when unset. |
| `phases` | One prompt file per phase after the first; each runs as its own step ([ADR-0079](adr/0079-a-hard-case-is-paced-in-phases-and-cannot-read-its-grade.md)). |
| `agent_hidden` | The case's grader, rubric and notes, masked out of the agent's container; validation still reads them. |

A case without these keys runs exactly as before. The agent and its grader share
one image on purpose: an agent that cannot launch the engine cannot test its own
work, and a grader in another image would judge the code against a toolchain the
agent never saw.

## The images

```bash
make images-3d
```

This builds `harness-lab-runner-godot` and `harness-lab-sandbox-godot` on top of the
shared runner and sandbox images. `make app-up` never builds them. Each adds Godot
4.7.2 (pinned) and a software OpenGL stack, about 230 MB.

Godot runs in two modes inside the runner:

- `godot --headless …` runs logic only: rules, physics and input. Under
  `--headless`, `RenderingServer.frame_post_draw` never fires, so nothing that reads
  pixels can run there.
- `godot-render …` runs Godot on a private virtual display (Xvfb) with Mesa
  llvmpipe. That gives a real OpenGL 3.3 context with no GPU, so screenshots and
  pixel checks work. The project must use the **Compatibility** renderer.

Both modes work inside the hardened validation lane: no network, every capability
dropped, 4 GB of memory.

`make app-up` builds only the shared runner image. When a 3D case's image is missing,
it says so and names `make images-3d`.

If a case names an image that was not built, the run is refused before it is queued.
The worker reports which `harness-lab-*` images it has (`case_images_present`), and the
run preflight checks the case's `runner_image` against that list. The guided flows show
the refusal on the review screen, and **Create and launch** waits until the image is
built.

Before 2026-11-20, a missing image was caught only when the executor started, so the run
was queued and failed in its first second (chess3d-v1-K-015). That failure is recorded as
the lab's, not the agent's.

## How a game is graded

The grader runs the agent's game instead of reading its code. In `chess3d-v1` it
works in two parts:

1. **Harvest (one command).** The harvest generates this run's probes from a seed
   derived from the run id. It copies the agent's project to a scratch directory,
   adds the lab's GDScript harvester, and runs it twice: once headless for the
   rules and once rendered for the scene. Every raw answer goes to
   `<workspace>/.lab-grade/`. The harvest judges nothing.
2. **One command per check.** Each check reads those answers and decides one
   behaviour against an independent reference, so `test_pass_rate` and per-step
   reward get one step per behaviour.

Three choices make this honest:

- **Probes are generated, never stored.** A workspace without its own repository is
  a directory of the lab's tree, and the runner needs the lab's `.git` to snapshot
  each step. Anything committed is therefore one `git show` away from the agent,
  however its working-tree copy is hidden. A probe seeded with the run id cannot be
  read from anywhere before the run starts.
- **The scene is driven through the screen.** The grader projects board squares to
  pixels with the scene's own camera, injects mouse events at those pixels, and
  reads the scene graph back. A board that renders correctly but ignores the mouse
  fails, and so does a click handler that hits the wrong square.
- **A failure is attributed to whoever caused it.** A missing project, a missing
  method or a crash inside the agent's code fails the checks that depend on it. A
  harvest that never ran, or a puzzle the lab could not generate, exits 124, which
  the validation lane records as inconclusive (ADR-0062).

Per-step reward points each command at each step's snapshot by rewriting the
workspace path. Because the answers live inside the workspace, every snapshot gets
its own harvest. This costs about a minute per step.

## Pacing: one phase per step

A frontier game is too much for one prompt to measure. A strong model builds it in
one long session and a weak one fails somewhere inside it, and in both cases the
per-step curve has a single point. A phased case gives the agent the whole contract
up front, so it knows the destination, then asks for one milestone per step:

- Phase 1 is the case prompt and ends with *stop when this phase is done*. Each later
  phase is a file under `phases/`.
- Every phase is stored with the contract appended, because each step is a fresh
  agent session. Under the `harness` variant it also carries the governance preamble,
  so the treatment applies to every phase, not only the first.
- The run cannot end itself before the last phase. A step that changes nothing still
  stops it.
- Order the phases like the rubric's ladder. The per-step reward then tells you which
  milestone each model reached and what each phase cost.

## What the agent cannot read

The agent's container never sees:

- `data/reference/`: every case's calibration solution;
- the case's `agent_hidden` paths: its grader, rubric and notes.

The validation lane sees both. Two things this does not cover:

- In a `lab_root` run, `memory/` and `docs/` are the treatment and stay readable, so
  never write how a case is graded there.
- A committed file is readable with `git show HEAD:…`, which is why probes are
  generated per run.

## Playing what a run built

On a run whose case declares a `play_command`, the run page shows a **Play** tab.
It opens a Linux desktop in the browser (noVNC) in the case's sandbox image, with
the game already running. If the game exits, a window shows the last lines of its
log and the command to relaunch it from the terminal underneath. The **Desktop** and
**Terminal** tabs open in the same image, so the engine is there too.

The desktop renders on the CPU. A board game or a light scene plays well; a heavy
scene will be slow. That limit comes from the GPU-less sandbox, not from the game.

**How to play is shown above the desktop.** Two sources sit side by side:

- The case's `play_guide` in `task.yaml`: the goal, the controls the contract asked
  for, and a couple of tips.
- **The keys the game really bound.** The grader reads the game's live `InputMap`
  during its harvest, because an agent may register keys in code rather than in
  `project.godot` (the racing K-001 build did), and the tab lists them. A build that
  bound other keys, or none, is shown as it is.

Click once inside the game window before using the keyboard: noVNC only passes keys
to a focused window.

The controls are part of the contract, not a suggestion. Every keyboard action names
its keys in the prompt, and `input_actions` fails a build that left them unbound:

- racing: W/S or arrows, A/D or arrows, R;
- city: WASD or arrows, Shift, E, Space to brake.

A person must be able to play what the agent built without reading its code.

## Comparing models on a case

When a new model ships, open **Evaluate → Compare models**:

1. Pick the case.
2. Pick two to four models, each with the harness that runs it: `claude` for Claude
   models, `opencode` for local and gateway models.
3. Keep the shared settings: the prompt variant, the repetitions, and the steps
   (one per phase, from the case).

The page creates one experiment with an arm per model, every other setting shared,
and files it in the family `models-<case>`. That family's list holds every earlier
comparison on the case, so the new model is read next to the last one.

The experiment page shows, per model and per run, the checks passed after each
phase, the final grade, the cost and the time. That is the useful reading here:
almost no run passes all 40 checks, so a pass rate would read 0% for every model.

By default runs execute one at a time, and a phased game run can take an hour or
more, so three models take an afternoon. **Two at a time** puts one model on each of
two lanes. A Claude model then finishes, and can be played from its Play tab, while a
local model on opencode is still running. The trade-offs:

- your token quota is used twice as fast;
- two game containers need 8 GB together, and they share the CPU;
- two runs on the same local model take turns on its GPU.

The option is greyed out when the second lane's worker is not running. A local model
reports no cost; the table says so instead of showing US$0.00. [ADR-0082](adr/0082-models-are-compared-on-one-case-by-checks-not-pass-rate.md).

## When a run is cut before it finishes

A phased run takes an hour or more, so it is sometimes cut by something that
is not the model: a worker restart, a gateway that stops answering. A failed or
cancelled run offers two retries on its page (ADR-0083):

- **Continue from step N** keeps the phases that finished, with their time, cost
  and grade. It restores the workspace from the last finished phase's snapshot
  and runs only the phases after it. The tooltip says which phase it would
  restore, or why it cannot: no phase finished, every phase was used, or the
  case works on a cloned repository.
- **Retry from scratch** discards the attempt and runs every phase again under
  the same id.

Continue a run that was cut by infrastructure. Retry from scratch, or leave it
as it is, when the run stopped on its own terms: cut by its time budget, or
finished with checks failing. Those are the result.

## Writing the next case

Every case in the lane follows the same pattern:

1. **State a contract the grader can drive.** Use autoload singletons for state,
   node groups and metadata for what is on screen, and a world-to-screen function so
   the grader can click. Put every name the grader uses in the prompt.
2. **Generate held-out content from the run id:** tracks, positions, map tiles. For
   a real-world map, ship an OpenStreetMap extract and choose the held-out
   neighbourhood by seed.
3. **Judge against an independent reference.** For chess that is python-chess. For
   physics it is a recorded input sequence and tolerances, not a second
   implementation of the agent's game.
4. **Calibrate before trusting the case** (ADR-0057): an empty workspace, a plausible
   weaker build and a reference solution. Keep the reference in
   `data/reference/<case>/`, which is git-ignored.
5. **Keep the frame-time check an observation** (ADR-0073). The CPU renderer is
   consistent between runs and says nothing about a GPU.
