## Phase 3 of 5 — vehicles

The city and the player from phases 1–2 are in the workspace. Read them before you
change them, and keep their behaviour.

Add cars the player can drive, exactly as the contract in the task states:

- `spawn_parked_car(x, z, heading_deg)` places a drivable car, a node in the group
  `vehicles`, on the ground, facing that compass heading (0 = north = −Z,
  90 = east = +X);
- `enter_nearest_vehicle` enters the nearest car within 5 m (and refuses when there is
  none); `exit_vehicle` puts the player back on foot beside the car;
- while driving, `player_state` reports the car (`in_vehicle`, `vehicle_id`,
  position, speed);
- `set_vehicle_input(throttle, brake, steer)`: from standstill, full throttle passes
  6 m/s within 3 s; full brake stops the car from 15 m/s within 3 s; steer +1 turns
  right (clockwise seen from above), at least 45° in 4 s with throttle;
- a car **cannot drive through a building**;
- the keyboard: `interact` enters and leaves, `accelerate`, `brake`, `steer_left` and
  `steer_right` drive, and the camera follows the car.

Verify headless with `--fixed-fps 60`. Drive in a straight line and measure the speed
and the distance, brake to a stop, and turn in a circle. Then drive straight into a
building wall and confirm the car never ends inside the footprint. Do the same with
the keys under `godot-render`.

**Stop when phase 3 is done and verified.** Do not build traffic or the minimap yet.
