## Phase 5 of 5 — finish: play it end to end

Everything is in the workspace. This phase is about the whole, not new features.

- Re-read the full contract in the task and check every item against what you built:
  API methods, the scene groups and metadata, the input actions, the renderer
  setting, `--map=`, and the constraints on your own code only.
- Load maps you have not tried yet: rotate the shipped one, cut a piece out of it,
  re-tag its heights. Confirm the city, the player, the cars and the traffic all
  still work. Nothing may depend on the shipped map.
- Play it under `godot-render`: walk, enter a car, drive across the city, crash into
  a wall, get out, follow the traffic, watch the minimap. Fix whatever breaks.
- Keep it smooth on a software renderer with the whole shipped city and traffic on
  screen. Make it pleasant: readable HUD, a camera that does not clip into walls,
  buildings and streets that read as a city.
- Remove any scratch files your own tests no longer need.

State what you checked, what you fixed, and anything still not working.
