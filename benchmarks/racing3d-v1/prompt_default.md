# Task: build a playable 3D racing game in Godot

Build a complete racing game in **Godot 4.7** (GDScript): a car with real physics a
person drives with the keyboard, tracks loaded from data, lap timing with checkpoints,
and AI rivals that can drive any track.

All files go inside `{workspace}/`. The Godot project lives at the workspace root:
`{workspace}/project.godot`. The `godot` command (4.7.2) is installed; use it to run
and test your work. There is no GPU: `godot --headless` runs logic and physics only,
and `godot-render` runs the game on a software renderer (it accepts the same arguments
as `godot`), which is what you need to take a screenshot or check what is on screen.

## What is being judged

An automated grader loads your project and drives your game through the API below on
tracks it generates **fresh for each run** (you cannot know them in advance). It steers
your car itself, with its own path follower that reads only `car_state()` and calls
`set_input()` and `step()`, and it measures every lap, checkpoint and crossing with its
own geometry. It then plays the 3D scene through the input actions and reads back the
camera, the cars and the HUD. A second, unseen track with tighter corners is used only
for the AI rivals: an AI that only works on the tracks you tested fails there.

Each behaviour is its own check, for example: the car accelerates, brakes and steers
the right way (`steers_left`, `steers_right`); the barriers hold (`wall_blocks`); the
same inputs give the same race (`determinism`); checkpoints count only in order
(`checkpoint_order`); a full lap counts once (`lap_counts`); a corner cut past a
checkpoint is not counted (`cut_not_counted`); driving backwards is flagged
(`wrong_way`); rivals finish a lap of the unseen track (`rivals_finish_heldout`),
drive around a car stopped in the road instead of shoving it aside
(`rivals_avoid_stopped`), and never pass through another car (`rivals_no_overlap`);
the keyboard drives the car in the scene (`keyboard_drive`).

## Hard constraints

- **Your own code only.** GDScript, scenes and resources you write. No `addons/`, no
  editor plugins, no GDExtension, no native libraries, no binaries, nothing downloaded
  (no asset packs, no vehicle controllers from elsewhere). Godot's built-in nodes are
  fine, including `VehicleBody3D` and `VehicleWheel3D`. Allowed file types: `.gd`,
  `.tscn`, `.tres`, `.gdshader`, `project.godot`, `.cfg`, `.png`, `.svg`, `.json`,
  `.md`, `.txt`, and the `.import`/`.uid` files Godot creates.
- **Renderer:** `project.godot` must set
  `rendering/renderer/rendering_method="gl_compatibility"` (the software renderer only
  supports Compatibility). A `run/main_scene` must be set.
- **Physics at 60 ticks per second** (`physics/common/physics_ticks_per_second=60`,
  the default). The grader runs Godot with `--fixed-fps 60`.

## The track format

A track is a JSON file:

```json
{
  "name": "Lakeside Loop",
  "width": 12.0,
  "closed": true,
  "centerline": [[x, z], [x, z], ...],
  "checkpoints": [0, 8, 16, 24, 32, 40],
  "start": {"position": [x, y, z], "heading_deg": 37.5},
  "walls": true
}
```

- Coordinates are metres in Godot's world: `x` and `z` on the ground, the road
  surface at `y = 0`. The ground continues flat at `y = 0` beyond the road.
- `centerline` is a closed loop of at least 24 points (the last joins the first),
  counter-clockwise seen from above; the race runs in the order of the points. The road
  is `width` metres wide (10–14), centred on the line.
- `checkpoints` are indices into `centerline`, increasing, at least 6; the first is
  always `0`, the start/finish line. A checkpoint is a **gate**: the segment across the
  full width of the road through that centreline point, perpendicular to the line there
  (the direction from the previous point to the next one).
- `start` is where the player's car starts: a point on the road and its heading.
- **Heading:** degrees about the vertical axis; `0` faces `-Z`, `90` faces `-X` (a left
  turn increases it), `180`/`-180` faces `+Z`. A heading of `h` is the direction
  `(-sin h, 0, -cos h)`.
- `walls`: when `true`, barriers run along both edges of the road over the whole loop;
  when `false` there are none and a car can drive onto the grass.

Ship your own default track at **`res://tracks/default.json`** in this format.

## The API contract (exact)

An **autoload singleton named `Race`** (Project Settings → Autoload, node name `Race`)
holds the whole race — the single source of truth the scene renders. It must provide:

| Method | Contract |
|---|---|
| `load_track(path: String) -> bool` | Load a track file (an absolute path or `res://`), build it in 3D, remove every rival, and put the player's car at rest on `start`. Return `false`, and keep running, on a missing or malformed file. |
| `reset() -> void` | Back to the start state of the current track: every car on its grid spot at rest, lap 0, checkpoint -1, no lap times, `race_time()` 0. |
| `set_input(throttle: float, brake: float, steer: float) -> void` | The player's controls, held until changed: `throttle` and `brake` 0..1, `steer` -1 (full left) .. 1 (full right). |
| `step(ticks: int) -> void` | Advance the simulation exactly `ticks` physics ticks (1/60 s each), for every car. It may return when done or be a coroutine (`await Race.step(n)`); the grader awaits it either way. Between calls nothing moves, and a car keeps its velocity across calls: `step(10)` and ten `step(1)` give the same result. |
| `car_state() -> Dictionary` | The player's car, see below. |
| `respawn() -> void` | Put the player's car upright, at rest, on the centreline at the last checkpoint passed (the start if none), facing the racing direction. Race progress (lap, checkpoint, lap times) is kept. |
| `add_rivals(count: int) -> void` | Add `count` AI cars on the grid behind the start, on the road, apart from each other and from the player. They drive the current track by themselves whenever the race advances, steer around slower or stopped cars (never shoving them aside), and get themselves unstuck. |
| `rival_states() -> Array` | One `car_state()`-shaped dictionary per rival, in the order added. |
| `race_time() -> float` | Seconds of simulated time since `reset()` or `load_track()`: ticks / 60, never wall-clock time. |

`car_state()` returns:

```
{"position": [x, y, z], "velocity": [x, y, z], "speed": float (m/s),
 "heading_deg": float, "lap": int, "checkpoint": int, "lap_times": [float, ...],
 "wrong_way": bool, "upside_down": bool, "on_track": bool}
```

- `position` is the car's origin, within half a metre of the ground under it; `speed`
  is the length of `velocity`; `heading_deg` is the direction the car's nose points.
- `checkpoint` is the index (into `checkpoints`) of the last gate passed **in order**;
  `-1` at the start. Passing gate `k` counts only when the car crosses its segment
  moving in the racing direction and `k` is the next gate expected. A gate missed by
  driving around it off the road is not passed, so every later gate stays uncounted
  until the car comes back for it.
- `lap` is the number of completed laps: a lap is completed when the car, having
  passed every checkpoint in order, crosses gate 0 again in the racing direction.
  `lap_times` holds one entry per completed lap, in seconds of simulated time,
  measured to the tick of the crossing; the first lap is timed from `reset()` (or
  `load_track()`), each later one from the end of the lap before.
- `wrong_way` is `true` while the car is driving against the racing direction, `false`
  while it drives with it.
- `upside_down` is `true` while the car lies on its roof or side.
- `on_track` is `true` while the car is on the road and `false` on the grass.

The race rules: barriers stop or deflect a car and are never driven through; cars
collide with each other and never pass through one another; braking slows the car
and, once stopped, keeps it there (it never moves it forward). The simulation is
deterministic: the same track and the same input sequence after `reset()` give the
same positions.

## The scene contract (exact)

- The main scene shows the race in 3D: the road, its edges and barriers visibly
  distinct from the grass, the player's car, every rival, and a `Camera3D` that
  follows the player's car from behind and looks down the road ahead.
- The player's car node — a `Node3D` whose global position is the car's position — is
  in the group **`player_car`**; every rival's node is in **`rival_cars`**; the road
  surface node(s) are in **`track`**. Cars have visible meshes.
- A visible `Label` in the group **`race_hud`** shows the lap (`Lap 1/3`), the race
  time and the speed, updated as the race runs.
- Input actions in `project.godot`'s Input Map, named exactly **`accelerate`**,
  **`brake`**, **`steer_left`**, **`steer_right`** (arrow keys and/or WASD) and
  **`reset_car`** (R, which calls the same behaviour as `respawn()`). The grader
  presses these actions; the scene must read them, not raw key codes.
- `godot --path {workspace} -- --track=<path>` starts a race on that track;
  `--rivals=<n>` sets the number of rivals (default 3). With no arguments it races on
  `res://tracks/default.json` against 3 rivals. In the scene the race runs in real
  time on its own, driven by the keyboard.

## How the work is paced: five phases

The whole contract above is the destination. You build it in **five phases, one per
step**; each step ends when its phase is done and verified, and the next phase is
given to you in the next step. Everything is graded at every step, so a phase done
well is visible on its own.

1. **The car and the track** — this step.
2. The race rules: checkpoints, laps, wrong way, respawn.
3. The 3D scene and keyboard play.
4. AI rivals that drive any track.
5. Finish: play full races end to end and polish.

Test your own work as you go: write your own GDScript test scripts and run them with
`godot --headless --script`, and check what is on screen with `godot-render`.

## Phase 1 of 5 — the car and the track (do only this now)

Create the Godot project (`project.godot` with the Compatibility renderer, physics at
60 ticks per second, and the `Race` autoload) and implement:

- `load_track` (including refusing malformed files), `reset`, `set_input`, `step`,
  `car_state` (position, velocity, speed, heading) and `race_time`;
- the track built in 3D from the file: the ground at `y = 0`, the road, and barriers
  with collision along both edges when `walls` is true;
- a drivable car with real physics (`VehicleBody3D` or your own rigid body): it
  accelerates to well over 8 m/s in 2 s of full throttle, brakes hard, steers both
  ways, stays on its wheels in normal driving, and is stopped by the barriers;
- exact determinism: the same inputs after `reset()` give the same positions, whether
  stepped in one call or one tick at a time;
- `res://tracks/default.json`, a valid track of your own.

Verify with a script that loads a track, drives a scripted sequence of inputs through
`set_input`/`step`, prints `car_state()`, and replays it after `reset()` to compare.
Drive into a barrier on purpose and confirm the car stays on the road side.

**Stop when phase 1 is done and verified.** Do not build the race rules, the scene or
the AI yet; state what you built and how you verified it.
