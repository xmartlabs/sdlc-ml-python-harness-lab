## Phase 2 of 5 — on foot

The city from phase 1 is in the workspace. Read it before you change it, and keep its
behaviour: any map still loads exactly as the contract states.

Add a person who walks the city, exactly as the contract in the task states:

- the player, a node in the group `player`, spawned on foot by `load_map` **on the
  street point nearest the centre of the map's `bounds`**;
- `player_state`, `set_player_input` (a world direction; walking 3–6 m/s, sprinting
  at least 1.3× faster, stopping within a second of `(0, 0)`), `teleport_player`, and
  `step(ticks)` advancing exactly that many physics ticks;
- collisions: the player stands on the ground and on nothing else, and **cannot walk
  through a building**, whatever its shape;
- in the main scene, a third-person `Camera3D` that follows the player, 2–40 m from
  them, with the player on screen, also right after a teleport;
- the movement actions (`move_forward`, `move_back`, `move_left`, `move_right`,
  `sprint`) walk the player relative to the camera.

Verify headless with `--fixed-fps 60`. Walk along a street and measure the distance.
Walk straight at a wall of a concave building and confirm the player never ends
inside its footprint. Teleport to several places and check `player_state`. Then hold
the keys under `godot-render` and look at the camera.

**Stop when phase 2 is done and verified.** Do not build cars or traffic yet.
