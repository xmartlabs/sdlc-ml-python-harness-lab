# Engineering harness (read before you touch code)

You are operating inside a governed repository. Before writing code, ground
yourself in its harness — this is mandatory, not optional:

1. Read the governance docs at the repo root:
   - `.github/architecture.md`
   - `.github/standards.md`
   - `.github/domain-boundaries.md`
2. Read project memory for prior context:
   - `memory/context.md`
   - `memory/learnings.md`
3. Follow the working loop: Ground → Plan → Delegate → Verify → Compound.

Drive the work with the repo's skills, not ad-hoc improvisation:

- Use the **plan_and_execute_feature** skill to turn the task into explicit,
  phased work before you implement it.
- Use the **systematic_debugging** skill when something does not behave as
  expected — reproduce → isolate → hypothesize → fix → verify. No blind edits.
- Use the **verify_changes** skill to confirm the work before you consider it
  done.

Apply the harness to *how* you solve the task. The task itself, its scope and
its acceptance criteria are stated below and are not changed by this preamble.

---

# Task: build an open-world 3D city game from real map data in Godot

Build an open-world 3D city game in **Godot 4.7** (GDScript). The city is generated at
runtime from real map data. Buildings are extruded from their footprints to their
heights and streets follow the road centrelines. A person walks the city in third
person, gets into a car and drives it, ambient traffic follows the roads, and a
minimap shows where you are. It is an original game: no names, assets or maps from
any commercial game.

All files go inside `{workspace}/`. The Godot project lives at the workspace root:
`{workspace}/project.godot`. The `godot` command (4.7.2) is installed; use it to run
and test your work. There is no GPU. `godot --headless` runs logic and physics only.
`godot-render` runs the game on a software renderer and accepts the same arguments as
`godot`. Use it whenever you need a screenshot or want to check what is on screen.

## What is being judged

An automated grader loads your project and calls the API below at a fixed 60 Hz
physics rate. It builds a **map it generates fresh for each run**, a neighbourhood
you have never seen, in the same file format. It checks every answer against that
map file with plain geometry. It counts buildings, reads their heights, casts rays
down onto roofs and streets, and walks and drives the player into walls. It follows
the traffic, renders the city from the game camera and from above, and presses the
input actions. A game that only works on the shipped map, or hard-codes anything
about it, fails most checks. Positions must be right to about a metre and heights to
about half a metre.

### The map data

The map is already in your workspace at **`{workspace}/data/ciudad-vieja.json`**: an
OpenStreetMap extract of Montevideo's Ciudad Vieja, converted to local metres. Copy it
into your project as `res://maps/ciudad-vieja.json`; that is the default map. The
format, which every map the game must load follows:

| Key | Meaning |
|---|---|
| `attribution` | The licence notice. The game must show it (see the scene contract). |
| `units` | Metres; `x` east, `y` north. |
| `levels_to_metres` | `3.0`. |
| `bounds` | `[minx, miny, maxx, maxy]`: the map's extent. Everything is inside it. |
| `buildings` | `[{"id": int, "levels": int or null, "height": float or null, "footprint": [[x, y], …]}]`. A footprint is a closed ring **without** the repeated last point. It may wind either way, may be concave, and may be tiny. |
| `roads` | `[{"id": int, "kind": str, "name": str, "oneway": bool, "line": [[x, y], …]}]`, the road centrelines. Roads meet where they share a point. |

**Axes.** World `X` = map `x` (east). World `Z` = **−**map `y` (north is −Z). `Y` is
up and the ground is at `Y = 0`. A map point `(x, y)` is the world point `(x, 0, −y)`.

**Height rule (exact).** A building's height is `height` if it is a positive number.
Otherwise it is `levels × 3.0` if `levels` is a positive number. Otherwise it is
`9.0` m. A building stands from `Y = 0` to its height, on exactly its footprint.
Footprints with an area under 4 m² may be skipped; every other building must be built.

**Street widths by `kind`** (metres): `primary` and `trunk` 12, `secondary` 10,
`tertiary` 9, `residential` and `unclassified` 7, `pedestrian` and `living_street` 6,
`service` 5, anything else 6. A street is its centreline widened to that width.

## Hard constraints

- **Your own code only.** GDScript, scenes, shaders and resources you write. No
  `addons/`, no editor plugins, no GDExtension, no native libraries, no binaries,
  nothing downloaded (no asset packs, models or textures from the internet). Allowed
  file types: `.gd`, `.tscn`, `.tres`, `.gdshader`, `project.godot`, `.cfg`, `.png`,
  `.svg`, `.json`, `.md`, `.txt`, and the `.import`/`.uid` files Godot creates.
  Godot's built-in nodes are all fine, including `CharacterBody3D`, `VehicleBody3D`,
  `StaticBody3D` and `NavigationRegion3D`.
- **Renderer:** `project.godot` must set
  `rendering/renderer/rendering_method="gl_compatibility"`, because the software
  renderer only supports Compatibility. A `run/main_scene` must be set.
- **Sky on the software renderer:** a `Sky` used for ambient light or reflections
  (the defaults when the background is a sky) crashes Godot 4.7 under `godot-render`
  most of the time. Show a sky as the background if you like. Set the environment's
  `ambient_light_source` to `Color` and its `reflected_light_source` to `Disabled`.
  A crash in the rendered game fails every check of the scene.
- **Physics:** keep the default 60 physics ticks per second. The game must behave
  the same when run with `--fixed-fps 60`.
- **Load scripts by path.** Use `preload("res://…")`, not `class_name`, for one
  script to use another. The grader runs your project with `--script`, before the
  editor has indexed any class names.

## The API contract (exact)

An **autoload singleton named `City`** (Project Settings → Autoload, node name `City`)
owns the world: the city, the player, the cars and the traffic. The main scene shows
it; the grader also drives it with no main scene at all. All positions are world
coordinates in metres.

| Member | Contract |
|---|---|
| `load_map(path: String) -> bool` | Build the whole city from a map file (`res://…` or an absolute path): buildings with collision, streets, and a solid ground. Replace any city built before, and spawn the player on foot **on the street point nearest the centre of `bounds`**. Return `false` and change nothing on a missing file, invalid JSON, or a file without `buildings` and `roads` arrays. Build it within 30 s. |
| `building_count() -> int` | The number of buildings built. |
| `building_info(id: int) -> Dictionary` | `{"id": int, "height": float, "centroid": [x, z], "node_path": String}` for a building of the map; `{}` for an unknown id. `centroid` is the world `[X, Z]` of the footprint's area centroid. `node_path` is the absolute path of that building's node. |
| `ground_height_at(x: float, z: float) -> float` | The height of the top surface at world `(x, z)`: its roof inside a building, `0` on a street or open ground. It **must cast a physics ray down** onto the static world (`StaticBody3D` collision), not look the answer up in the map file. Ignore cars, the player and traffic. |
| `player_state() -> Dictionary` | `{"position": [x, y, z], "in_vehicle": bool, "vehicle_id": int, "speed": float}`. `position` is the player's feet on foot and the car's origin at road level when driving. `vehicle_id` is `-1` on foot. `speed` is horizontal speed in m/s. |
| `set_player_input(move_x: float, move_z: float, sprint: bool) -> void` | Walk in the **world** direction `(move_x, move_z)` (each −1…1). This direction ignores the camera. Hold the input until it is changed. Walking is 3–6 m/s and sprinting is at least 1.3× faster. The player stops within a second of `(0, 0)`. |
| `set_vehicle_input(throttle: float, brake: float, steer: float) -> void` | Drive the car the player is in: throttle 0…1, brake 0…1, steer −1 (left) … +1 (right). Hold the input until it is changed. From standstill, full throttle reaches more than 6 m/s within 3 s. Full brake stops the car from 15 m/s within 3 s. Full steer with throttle turns the car through at least 45° in 4 s. |
| `step(ticks: int)` | Advance the game exactly `ticks` physics ticks. It may be a coroutine (`await get_tree().physics_frame` per tick); the grader awaits it. |
| `teleport_player(x: float, z: float) -> void` | Put the player on foot, standing on the ground at `(x, z)`. Leave any car first. |
| `spawn_parked_car(x: float, z: float, heading_deg: float) -> int` | Place a drivable car on the ground at `(x, z)`, facing compass `heading_deg` (0 = north = −Z, 90 = east = +X). Return its id, an int ≥ 0. |
| `enter_nearest_vehicle() -> bool` | Enter the nearest parked car within **5 m** of the player; `false` if there is none. |
| `exit_vehicle() -> bool` | Leave the car. The player stands on the ground beside it, within 6 m and not inside anything. `false` when on foot. |
| `set_traffic(count: int) -> void` | Replace the ambient traffic with exactly `count` cars, spread over the roads. |
| `traffic_states() -> Array` | `[{"id": int, "position": [x, y, z], "speed": float}]`, one entry per traffic car. |
| `minimap_player_marker() -> Array` | `[u, v]`: where the player's marker is drawn on the minimap, normalised 0…1 with `(0, 0)` at the top-left. The minimap shows the whole of `bounds` with north up. So `u = (x − minx) / (maxx − minx)` and `v = (maxy − y) / (maxy − miny)` for the player's map point `(x, y)`. |

Rules the grader holds the game to:

- **Solid city.** The player cannot walk through a building, and a car cannot drive
  through one. Buildings and the ground are static physics bodies with collision.
- **Traffic stays on the roads.** Every traffic car stays within half its road's
  width plus 2 m of a road centreline and never drives through a building. Cars move
  at a steady 5–15 m/s and follow the roads through their shared points. At a dead
  end a car turns around; it does not jump.
- **Traffic is not in the way of the API.** The grader moves the player and its own
  cars wherever it needs to. Traffic must not push the player, and `ground_height_at`
  ignores it.

## The scene contract (exact)

- **The main scene** shows the city in 3D through a current `Camera3D` that follows
  the player in **third person**: 2–40 m from the player, with the player on screen.
  It also shows a sky or background, lighting, and a HUD.
- `godot --path {workspace} -- --map=<path>` loads that map; with no `--map` argument
  the game loads `res://maps/ciudad-vieja.json`. The main scene calls `City.load_map`.
- **Groups and metadata:**
  - the player node is in the group **`player`**;
  - each building's node is in the group **`buildings`** and has metadata
    **`building_id`** (the map id, an int); its meshes are under it;
  - each ambient traffic car is in the group **`traffic`**;
  - each drivable car is in the group **`vehicles`**.
- **A minimap:** a visible `Control` in the group **`minimap`**, at least 96×96 px,
  fully on screen. It draws the streets and buildings of the whole map, north up,
  with the player's marker at `minimap_player_marker()`.
- **The attribution:** a visible `Label` in the group **`attribution`**, fully on
  screen, whose text contains `OpenStreetMap`, for example the map's `attribution`
  string. The map data is licensed under the ODbL, which requires it.
- **Input actions, exactly these names**, each bound to at least one key:
  `move_forward`, `move_back`, `move_left`, `move_right`, `sprint`, `interact`
  (enter the nearest car within 5 m, or leave the car), `accelerate`, `brake`,
  `steer_left`, `steer_right`. On foot, the movement actions walk relative to the
  camera. In a car, the driving actions drive. The keyboard and the API drive the
  same game.

## How the work is paced: five phases

The whole contract above is the destination. You build it in **five phases, one per
step**. Each step ends when its phase is done and verified, and the next phase is
given to you in the next step. Everything is graded at every step, so a phase done
well is visible on its own.

1. **The city from data** — this step.
2. On foot: the player, walking, collisions, the third-person camera.
3. Vehicles: parked cars, entering and leaving, driving.
4. The living city: ambient traffic, the minimap, the attribution and the HUD.
5. Finish: play it end to end and polish.

Test your own work as you go. Write your own GDScript test scripts and run them with
`godot --headless --fixed-fps 60 --script`. Make test maps of your own, rotated,
concave and tiny, and check what is on screen with `godot-render`.

## Phase 1 of 5 — the city from data (do only this now)

Create the Godot project (`project.godot` with the Compatibility renderer, the `City`
autoload, the ten input actions and a main scene). Copy the map into
`res://maps/ciudad-vieja.json`. Then implement in the `City` autoload:

- `load_map` for **any** map in the format. Extrude each building from its footprint,
  concave or not and wound either way, to the height the rule gives. Give it a
  roof, walls and collision that matches. Lay out the streets from the centrelines at
  their widths, and add a solid ground under the whole map. Refuse bad files with
  `false`.
- `building_count`, `building_info` and `ground_height_at` (a physics ray, not a
  lookup);
- each building's node in the group `buildings` with its `building_id` metadata;
- a main scene that loads the default map, or the `--map=` argument, and shows the
  city through a `Camera3D`.

Verify with your own maps. Use one with a rotated, concave, L-shaped building, one
with every branch of the height rule, and one with a footprint under 4 m². Compare
`ground_height_at` inside and just outside each footprint with what the file says.
Take a screenshot of the city with `godot-render`.

**Stop when phase 1 is done and verified.** Do not build the player, cars or traffic
yet; state what you built and how you verified it.
