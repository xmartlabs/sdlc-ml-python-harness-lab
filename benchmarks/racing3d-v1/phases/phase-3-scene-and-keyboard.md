## Phase 3 of 5 — the 3D scene and keyboard play

The race from phases 1–2 is in the workspace. Read it before you change it, and do
not change its behaviour through the API.

Build the main scene, exactly as the scene contract in the task states:

- the road, its edges and barriers visibly distinct from the grass, the player's car,
  and a chase `Camera3D` that follows the car from behind and looks down the road;
- the groups `player_car`, `rival_cars` and `track`, and a visible `Label` in the
  group `race_hud` showing `Lap n/N`, the race time and the speed;
- the input actions `accelerate`, `brake`, `steer_left`, `steer_right` and
  `reset_car` in the Input Map, bound to the arrow keys and/or WASD and R, read by the
  scene to drive the player's car, with the race running in real time;
- `godot --path {workspace} -- --track=<path>` races on that track, `--rivals=<n>`
  sets the number of rivals, and no arguments race on `res://tracks/default.json`.

Verify with `godot-render`: take screenshots at the start and while driving, and
inject the actions (`Input.action_press` or `InputEventAction`) to drive, steer,
brake and reset. Confirm the car stays on screen and the HUD changes.

**Stop when phase 3 is done and verified.** Do not build the AI yet.
