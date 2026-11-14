# opencity3d-v1 scoring

The grade is the shipped grader: `test_pass_rate` over 44 independent checks, all
objective, every one decided against the map file of a neighbourhood generated for
the run. The grader uses plain geometry: point in polygon, distance to a centreline,
and the height rule. The rubric exists so the LLM judge and a human reviewer land on
the same ladder. Every level is tagged with what can establish it: `[check]` an
automated check, `[diff]` the code itself (quoted), `[manual]` only a human playing
the game.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No Godot project, or nothing that loads | [check] |
| 1 | A project that loads, but the `City` autoload or its contract is missing (`contract` fails) | [check] |
| 2 | The contract exists, with only own code on the Compatibility renderer and the ten input actions (`contract`, `own_code`, `renderer`, `input_actions`) | [check] |
| 4 | Any map loads: the shipped one by default, a held-out one through the API, bad files refused; buildings counted, tagged and placed with heights by the rule (`default_map`, `heldout_loads`, `bad_map`, `building_count`, `building_heights`, `building_info`, `buildings_group`, `footprints_placed`) | [check] |
| 5 | The built city is solid where the map says: roofs and streets by physics ray, true footprint shapes rather than boxes, and rendered roofs that match the footprints from above (`roof_raycast`, `street_raycast`, `footprint_shape`, `collision_geometry`, `ground_uses_physics`, `map_argument`, `roofs_from_above`, `city_visible`) | [check] |
| 6 | On foot: exact physics steps, a spawn on the central street, teleports, walking and sprinting at the stated speeds, and walls that stop the player (`step_ticks`, `spawn`, `teleport`, `walk`, `sprint`, `walls_block`) | [check] |
| 7 | The game scene: a third-person camera that keeps the player in view, and the keyboard walks (`scene_loads`, `camera_follows`, `keyboard_walk`) | [check] |
| 8 | Vehicles: entered within 5 m only, driven, braked, steered, stopped by walls, left beside the car, and the same by keyboard (`enter_vehicle`, `drive`, `brake`, `steering`, `drive_blocked`, `exit_vehicle`, `keyboard_drive`) | [check] |
| 9 | The living city: exactly the traffic asked for, driving the roads and never through buildings; a minimap whose marker tracks the player (`traffic_spawn`, `traffic_moves`, `traffic_on_roads`, `traffic_not_in_buildings`, `minimap_marker`, `minimap`) | [check] |
| 10 | The whole game: the OpenStreetMap attribution on screen and the frame budget kept on the full city with traffic (`attribution`, `frame_time`), with no third-party code anywhere | [check] + [diff] |

Level 10 needs the diff as well as the checks. `own_code` refuses addons, native
libraries and binaries by file type, and the judge must confirm from the code that
the city builder, the controllers and the traffic were written, not pasted from a
known project.

Calibrated in the hardened validation lane (no network, all capabilities dropped,
4 GB): see `README.md` for the numbers per workspace and run id. The reference is
kept outside the repository.

Not graded here, and left to a person in the **Play** sandbox: whether the city is
pleasant to explore. That covers building and street looks, how the car handles, the
camera, and how the HUD reads.
