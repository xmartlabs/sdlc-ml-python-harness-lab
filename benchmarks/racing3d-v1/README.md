# racing3d-v1

The second case of the 3D game lane (ADR-0078): build a playable 3D racing game in
**Godot 4.7** — a car with real physics on a track loaded from data, lap timing with
checkpoints in order, AI rivals that drive any track, and a 3D scene played with the
keyboard. It is meant for frontier models. Smaller models are expected to stop partway
up the ladder, and the 39 checks say how far each one got.

## How it is paced

Five phases, one step each (ADR-0079):

1. The car and the track.
2. The race rules: checkpoints, laps, wrong way, respawn.
3. The 3D scene and keyboard play.
4. AI rivals that drive any track.
5. Finish: play full races end to end.

Phase 1 is `prompt_default.md`. Phases 2–5 are in `phases/` and are stored with the
prompt's contract appended. The run cannot end itself before phase 5, so per-step
reward shows which milestone a model reached.

## How it is graded

`grader/grade.py` runs in two parts. The first part takes no score of its own:

1. **harvest** generates this run's tracks from a seed derived from the run id: a
   gently curving loop for driving, a **held-out** loop of another family (corners
   of 20–30 m radius, a narrower road) used only for the rivals, and a stadium loop
   with a long straight for the keyboard. It loads the agent's project in Godot three
   times — the race API headless, the scene on a software renderer (`godot-render`),
   and a plain launch headless — and records every raw answer in
   `<workspace>/.lab-grade/`.
2. **check `<name>`**, 39 times. Each check decides one behaviour with the lab's own
   geometry: the centreline, signed distance to it, and each checkpoint gate as a
   segment across the road.

The lab drives the car itself. Its path follower (pure pursuit on the lab's
centreline, with a speed plan from the curvature) reads only the car's position from
`car_state()` and calls `set_input()` and `step()`. It drives:

- a full lap, to judge checkpoints in order, the lap count and the lap time;
- the same lap with the barriers off, bending 15 m around one gate on the grass, to
  judge that a cut counts nothing;
- a U-turn and a stretch the wrong way;
- straight into each barrier;
- onto the grass and back with `respawn()`.

It also puts the car on its roof. A rule is judged on a lap the lab drove and
measured, never on one the game says it drove.

The rivals race twice:

- 60 s on the run's track, with the player parked at the inside edge;
- up to 150 s on the held-out track, with the player stopped in the middle of the
  road half a lap from the start, so every rival has to get past it.

Overlap is measured on every sample of both races. On the held-out track the stopped
car must also stay where it was: a rival that shoves it aside has driven through it by
other means.

The scene checks drive the game through its input actions (`InputEventAction`), not
the API. They read the camera, the player's node, the HUD label and a rendered frame.
The plain launch confirms that the game starts on its own default track with three
rivals that move.

`step()` may return when done or be a coroutine; the harvester awaits every call
into the agent's code either way. A coroutine called without `await` would abort the
harvester, so this matters. A watchdog saves the answers so far and quits if a call
hangs.

## Calibration

To be measured in the hardened validation lane (no network, every capability
dropped, 4 GB of memory):

| Workspace | Checks (39) | Notes |
|---|---|---|
| empty | TBD | the harvest fails as the entry-point check |
| weaker build | TBD | drives and counts laps; no gate ordering or cut detection, no wrong way, no respawn, rivals that follow the centreline blindly |
| reference | TBD | kept outside the repository, in `data/reference/racing3d-v1/` (git-ignored) |

The tamper guard passes only once the grader is committed (ADR-0071).

## Running it

```bash
make images-3d                                   # runner + sandbox images with Godot
make prompts                                     # regenerate prompt_harness.md
make case-import TASK=benchmarks/racing3d-v1/task.yaml
```

Open any finished run and use the **Play** tab: a desktop in your browser with the
race already running on the game's default track (`play_command` in `task.yaml`).
