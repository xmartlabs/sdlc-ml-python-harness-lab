# opencity3d-v1

The second case of the 3D game lane (ADR-0078): build an **open-world 3D city game in
Godot 4.7, generated at runtime from real map data**. The data is an OpenStreetMap
extract of Montevideo's Ciudad Vieja. Buildings are extruded from their footprints to
their heights, and streets are laid out from road centrelines. The player walks the
city in third person and drives cars. Ambient traffic follows the roads, and a
minimap tracks the player. It is meant for frontier models. Smaller models are
expected to stop partway up the ladder, and the 44 checks say how far each one got.

## The data and its licence

`data/ciudad-vieja.json` is an OpenStreetMap extract (bbox −56.2110, −34.9085,
−56.2030, −34.9040, fetched 2026-11-13), converted to local metres with `x` east and
`y` north. It holds 271 buildings, 129 of them with `building:levels`, and 66 road
centrelines. `starter/data/ciudad-vieja.json` is the same file. The runner copies the
starter into the empty workspace (ADR-0068), so the agent finds the map at
`{workspace}/data/ciudad-vieja.json`.

The map data is © OpenStreetMap contributors, available under the **Open Database
License (ODbL) 1.0**: <https://www.openstreetmap.org/copyright>. The ODbL requires
attribution wherever the data is used. That is why the scene contract requires a
visible `Label` in the group `attribution` containing "OpenStreetMap", and why the
`attribution` check exists. The held-out maps the grader derives from the extract
keep the same attribution string.

## How it is paced

Five phases, one step each (ADR-0079):

1. The city from data: `load_map` for any map, buildings, streets, ground,
   `building_info`, `ground_height_at`, the height rule.
2. On foot: the player, walking with collisions, teleport, the third-person camera.
3. Vehicles: parked cars, entering and leaving, driving, collisions.
4. The living city: ambient traffic on the roads, the minimap, the attribution.
5. Finish: play it end to end and polish.

Phase 1 is `prompt_default.md`. Phases 2–5 are in `phases/` and are stored with the
prompt's contract appended.

## How it is graded

`grader/grade.py` runs in two parts. The harvest is the first, and it takes no score
of its own:

1. **harvest** builds this run's **held-out map** from a seed derived from the run id.
   It takes a random 400 m window of the extract, rotates it by a random angle, moves
   it, re-tags about 30% of the levels and 10% of the heights, and replaces every id.
   It then forces the height rule's three branches onto the buildings it will probe,
   and adds two degenerate footprints (a 1.2 m square and a collinear sliver). It
   picks probes from that map: roof points at each footprint's pole of
   inaccessibility, street points clear of buildings, points inside a footprint's
   bounding box but outside the footprint, a straight stretch of street, a wall to
   walk into, a wall to drive into, and an open band around the city for driving
   dynamics. It writes three bad maps too. It runs the agent's project twice. The
   first run is the `City` API under `godot --headless --fixed-fps 60`. The second
   is the scene under `godot-render` with `-- --map=<held-out>`, which renders the
   game camera and a top-down orthographic view and presses the input actions. Every
   raw answer goes to `<workspace>/.lab-grade/`.
2. **check `<name>`**, 44 times. Each check decides one behaviour against the
   held-out map file with plain geometry (point in polygon, distance to a polyline,
   the height rule), in code the agent never saw.

A few of the checks guard against shortcuts:

- `footprint_shape` asks `ground_height_at` at points inside a footprint's bounding
  box but outside the footprint: a city of boxes has a roof there.
- `ground_uses_physics` adds a solid box the agent never saw and asks for the height
  on top of it: an answer read from the map file cannot see it.
- `collision_geometry` casts the lab's own rays onto the built world, so collision
  shapes that drift from the meshes fail.
- `roofs_from_above` paints every node in `buildings` magenta, renders a top-down
  orthographic view in a viewport of its own, and compares the covered pixels with
  the footprints (IoU ≥ 0.8).
- `city_visible` measures how much of the game camera's view is building, the same way.
- `keyboard_walk` and `keyboard_drive` press the input actions instead of calling the
  API.

The probes change with every run because an agent can read the lab's git history. The
grader's method is public; the map it is applied to is not.

## Calibration

Measured in the hardened validation lane: `--network none --cap-drop ALL
--security-opt no-new-privileges --pids-limit 512 -m 4g`, image
`harness-lab-runner-godot:latest`, `benchmarks/` mounted read-only, the same commands
as `task.yaml`.

| Workspace | run id | Checks (44) | Notes |
|---|---|---|---|
| empty | TBD | TBD | the harvest fails as the entry-point check |
| weaker build | TBD | TBD | bounding-box buildings, `height` tag ignored, player walks through walls, traffic in straight lines, no minimap |
| reference | TBD | TBD | kept outside the repository, in `data/reference/opencity3d-v1/` (git-ignored) |

Harvest wall time: TBD.

The tamper guard passes only once the grader is committed (ADR-0071).

## Running it

```bash
make images-3d                                   # runner + sandbox images with Godot
make prompts                                     # regenerate prompt_harness.md
make case-import TASK=benchmarks/opencity3d-v1/task.yaml
python benchmarks/opencity3d-v1/grader/grade.py probe <run_id> --map /tmp/heldout.json
```

The last command prints a run's probes and writes its held-out map, which is useful
for looking at what the grader will drive. Open any finished run and use the **Play**
tab: a desktop in your browser with the city running (`play_command` in `task.yaml`).
The desktop renders on the CPU, so the full city plays at a modest frame rate.
