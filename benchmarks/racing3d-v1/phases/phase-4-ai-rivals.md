## Phase 4 of 5 — AI rivals that drive any track

The race and the scene from phases 1–3 are in the workspace. Read them before you
change them.

Add the rivals, exactly as the contract in the task states:

- `add_rivals(count)` puts AI cars on the grid behind the start, on the road and apart
  from each other; `rival_states()` reports each one like `car_state()`; rivals count
  checkpoints and laps by the same rules as the player;
- the AI drives **any** track by itself: it follows the road, slows for corners it
  has never seen, steers around slower or stopped cars (including the player's car
  stopped in the middle of the road) without shoving them aside, and recovers when
  it is stuck;
- cars collide with each other and never pass through one another;
- every rival is visible in the scene, and the scene starts 3 rivals by default.

Verify on tracks you did not tune for: generate several closed tracks of your own with
tighter corners (radius about 20 m) and a narrower road, park the player's car in the
middle of the road, and check that every rival finishes a lap of each without getting
stuck and without moving the parked car.

**Stop when phase 4 is done and verified.**
