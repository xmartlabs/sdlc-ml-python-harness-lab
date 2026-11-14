# racing3d-v1 scoring

The grade is the shipped grader: `test_pass_rate` over 39 independent checks, all
objective. The lab drives the agent's car itself, with its own path follower on its
own centreline, and judges every lap, checkpoint, crossing and distance with its own
geometry on tracks generated for the run. The rubric exists so the LLM judge and a
human reviewer land on the same ladder; every level is tagged with what can establish
it — `[check]` an automated check, `[diff]` the code itself (quoted), `[manual]` only
a human playing the game.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No Godot project, or nothing that loads | [check] |
| 1 | A project that loads, but the `Race` autoload or its contract is missing (`contract` fails) | [check] |
| 2 | The contract exists, with only own code on the Compatibility renderer (`contract`, `own_code`, `renderer`) | [check] |
| 4 | A track loads from data and the car drives on it: it starts at rest on the start, the road and barriers are built, it accelerates, brakes, steers both ways, never creeps forward on the brake, and reports its heading (`load_track`, `track_built`, `accelerates`, `brakes`, `steers_left`, `steers_right`, `reverse_or_stop`, `heading`) | [check] |
| 5 | The physics is sound: the car stays on its wheels for a full lap, the barriers hold, the same inputs replay to the centimetre, and malformed track files are refused (`stays_on_ground`, `wall_blocks`, `determinism`, `bad_track`) | [check] |
| 7 | The race rules are exact on a lap the lab drove: checkpoints count in order at their gates, a lap counts once with its lap time to the tick, a cut around a gate counts nothing, and driving backwards is flagged (`checkpoint_order`, `lap_counts`, `lap_time_consistent`, `cut_not_counted`, `wrong_way`) | [check] |
| 8 | The car can be recovered and read: `on_track`, `respawn()` from the grass and from the roof, and the input map and default track ship (`on_track`, `respawn`, `upside_down`, `input_actions`, `default_track`) | [check] |
| 9 | A 3D scene a person can play: it loads on the given track and by default, a chase camera keeps the car on screen, the HUD updates, the keyboard actions drive, steer, brake and reset the car, and it renders within the frame budget (`scene_loads`, `default_launch`, `camera_follows`, `car_visible`, `hud`, `keyboard_drive`, `keyboard_reset`, `render`, `frame_time`) | [check] |
| 10 | AI rivals race: they spawn on the grid, drive the run's track on the road, never pass through another car, steer around a car stopped in the road without shoving it, and finish a lap of an unseen track with tighter corners (`rivals_spawn`, `rivals_drive`, `rivals_no_overlap`, `rivals_avoid_stopped`, `rivals_finish_heldout`), with no third-party code anywhere | [check] + [diff] |

Level 10 needs the diff as well as the checks: `own_code` refuses addons, native
libraries and binaries by file type, and the judge must confirm from the code that the
vehicle model, the race rules and the AI were written, not pasted from elsewhere.

Calibrated in the hardened validation lane (no network, all capabilities dropped,
4 GB): see the case README for the numbers. The reference is kept outside the
repository.

Not graded here, and left to a person in the **Play** sandbox: whether the game is
pleasant to play — how the car feels, how the camera moves, how good the track looks,
how well the rivals race.
