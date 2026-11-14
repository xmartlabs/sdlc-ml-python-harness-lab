## Phase 2 of 5 — the race rules

The car and the track from phase 1 are in the workspace. Read them before you change
them, and do not change how the car drives.

Add the race rules to the `Race` autoload, exactly as the contract in the task states:

- **Checkpoints in order:** a gate counts only when the car crosses its segment across
  the road, in the racing direction, and it is the next gate expected. A gate skipped
  by driving around it off the road is never counted, and neither is anything after it
  until the car goes back for it.
- **Laps:** a lap completes when, after every checkpoint in order, the car crosses
  gate 0 again; `lap_times` gets one entry per lap in simulated seconds, to the tick.
- `wrong_way`, `on_track` and `upside_down` in `car_state()`.
- `respawn()`: upright, at rest, on the centreline at the last checkpoint passed (the
  start if none), facing the racing direction, with the race progress kept.

Verify with scripts that drive the car through `set_input`/`step` with a simple path
follower of your own: a full lap on a track with `"walls": true` (one lap, one lap
time equal to `race_time()` at the crossing), a lap on the same track with
`"walls": false` that drives around one checkpoint on the grass (no lap counted), a
U-turn and a stretch driven backwards (`wrong_way`), and a respawn from the grass and
from a car put on its roof.

**Stop when phase 2 is done and verified.** Do not build the scene or the AI yet.
