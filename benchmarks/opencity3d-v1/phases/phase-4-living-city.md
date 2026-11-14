## Phase 4 of 5 — the living city

The city, the player and the cars from phases 1–3 are in the workspace. Read them
before you change them, and keep their behaviour.

Bring the city to life, exactly as the contract in the task states:

- **ambient traffic**: `set_traffic(count)` replaces the traffic with exactly `count`
  cars, nodes in the group `traffic`, spread over the roads; `traffic_states` reports
  them. The cars drive at a steady 5–15 m/s **along the road centrelines**, within
  half the road's width plus 2 m. They follow the roads through their shared points,
  turn around at dead ends instead of jumping, never drive through a building, and do
  not push the player;
- **the minimap**: a visible `Control` in the group `minimap`, at least 96×96 px and
  fully on screen. It draws the whole map north up, with streets, buildings and the
  player's marker. `minimap_player_marker` returns the marker's normalised `[u, v]`;
- **the attribution**: a visible `Label` in the group `attribution`, fully on screen,
  containing `OpenStreetMap` (the map's `attribution` text);
- a HUD that says how to play (on foot or driving, the keys).

Verify headless with `--fixed-fps 60`. Spawn traffic, run 20 simulated seconds, and
check every sampled position against the road centrelines and the footprints in the
map file. Do this on a map of your own as well as the shipped one. Then move the
player and compare the marker with the formula. Look at the minimap and the
attribution under `godot-render`.

**Stop when phase 4 is done and verified.**
