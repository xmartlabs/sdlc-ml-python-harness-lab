# The lab's harvester for racing3d-v1 (ADR-0078). It judges nothing: it loads the
# agent's project, drives its Race contract with the tracks and paths the Python
# side generated for this run, and writes every raw answer to a JSON file.
# Verdicts are computed afterwards in Python with the lab's own geometry, so a
# crash here costs answers, never a wrong verdict.
#
# Copied into a scratch copy of the agent's project as
# res://__lab_grader__/harvest.gd and run three times, always with
# `--fixed-fps 60` so one frame is one physics tick:
#   godot --headless ... -- --probe <probe.json> --out <logic.json> --phase logic
#   godot-render ...     -- --probe ... --out <scene.json> --phase scene --shots <dir> --track=<stadium> --rivals=0
#   godot --headless ... -- --probe ... --out <launch.json> --phase launch
#
# Every call into the agent's code goes through `_call`, which records a
# missing method instead of failing, and is awaited: `step()` may be a
# coroutine or return a Signal, and a coroutine called without `await` would
# abort the harvester. A script error inside one of the agent's methods does
# not stop Godot (it logs and returns a default), so one broken method costs
# only the checks that depend on it. Answers are flushed after every section,
# and a watchdog saves and quits if a section hangs (a `step()` that never
# returns), so a hang late in the run keeps everything before it.
#
# The lab drives the car itself in the logic phase: a pure-pursuit follower on
# the lab's own path, reading only the car's position from `car_state()`, so
# the race rules are judged on laps the lab drove.
extends SceneTree

const CTRL := 2  # ticks between two decisions of the lab driver
const WHEELBASE := 2.6
const MAX_STEER := 0.5

var _out := {}
var _probe := {}
var _out_path := ""
var _deadline := 0
var _section := ""
var _tick := 0  # ticks stepped since the last reset() or load_track()


func _initialize() -> void:
	_run.call_deferred()


func _process(_delta: float) -> bool:
	if _deadline > 0 and Time.get_ticks_msec() > _deadline:
		_out["timeout"] = _section
		_save()
		_deadline = 0
		quit(0)
	return false


func _arg(name: String, default: String = "") -> String:
	var args := OS.get_cmdline_user_args()
	var i := args.find(name)
	return args[i + 1] if i >= 0 and i + 1 < args.size() else default


func _save() -> void:
	var file := FileAccess.open(_out_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(_out, "", true, true))
		file.close()


func _race() -> Object:
	return root.get_node_or_null("Race")


func _call(method: String, args: Array = []) -> Variant:
	var race := _race()
	if race == null or not race.has_method(method):
		if not _out["missing"].has(method):
			_out["missing"].append(method)
		return null
	return await race.callv(method, args)


func _step(ticks: int) -> void:
	await _call("step", [ticks])
	_tick += ticks


func _input(throttle: float, brake: float, steer: float) -> void:
	await _call("set_input", [float(throttle), float(brake), float(steer)])


func _reset() -> void:
	await _call("reset")
	_tick = 0


func _load(path: String) -> Variant:
	var ok: Variant = await _call("load_track", [path])
	_tick = 0
	return ok


# --- Plain values ---------------------------------------------------------------


func _num(v: Variant) -> Variant:
	if (v is float or v is int) and is_finite(float(v)):
		return v
	return null


func _plain(v: Variant, depth: int = 0) -> Variant:
	# Anything the agent returns, made JSON-safe without judging it.
	if depth > 6:
		return null
	if v == null or v is bool or v is String:
		return v
	if v is int or v is float:
		return _num(v)
	if v is StringName:
		return str(v)
	if v is Vector3:
		return [_num(v.x), _num(v.y), _num(v.z)]
	if v is Vector2:
		return [_num(v.x), _num(v.y)]
	if v is Array or v is PackedFloat32Array or v is PackedFloat64Array or v is PackedInt32Array or v is PackedInt64Array or v is PackedStringArray or v is PackedVector3Array:
		var items := []
		for item in v:
			items.append(_plain(item, depth + 1))
		return items
	if v is Dictionary:
		var out := {}
		for key in v:
			out[str(key)] = _plain(v[key], depth + 1)
		return out
	return str(v)


func _pos(state: Variant) -> Variant:
	if not state is Dictionary:
		return null
	var p: Variant = _plain(state.get("position"))
	if p is Array and p.size() == 3 and p[0] != null and p[1] != null and p[2] != null:
		return Vector3(p[0], p[1], p[2])
	return null


func _state() -> Variant:
	var s: Variant = await _call("car_state")
	return s if s is Dictionary else null


func _sample(state: Variant) -> Array:
	var t: Variant = _num(await _call("race_time"))
	var p: Variant = _pos(state)
	if p == null:
		return [_tick, null, null, null, null, null, null, null, null, null, null, t, null]
	return [
		_tick, p.x, p.y, p.z,
		_plain(state.get("speed")), _plain(state.get("heading_deg")),
		_plain(state.get("lap")), _plain(state.get("checkpoint")),
		_plain(state.get("wrong_way")), _plain(state.get("on_track")), _plain(state.get("upside_down")),
		t, _plain(state.get("lap_times")),
	]


# --- Geometry -------------------------------------------------------------------


func _fwd(heading_deg: float) -> Vector2:
	# The contract's heading: 0 faces -Z, 90 faces -X. Vector2 is (x, z).
	var h := deg_to_rad(heading_deg)
	return Vector2(-sin(h), -cos(h))


func _heading(v: Vector2) -> float:
	return rad_to_deg(atan2(-v.x, -v.y))


func _angle_diff(a: float, b: float) -> float:
	return wrapf(a - b, -180.0, 180.0)


func _xz(p: Vector3) -> Vector2:
	return Vector2(p.x, p.z)


func _pt(path: Array, i: int) -> Vector2:
	return Vector2(path[i][0], path[i][1])


# --- The lab driver -------------------------------------------------------------


func _driver(path: Array, pos: Vector2, heading_deg: float) -> Dictionary:
	var best := 0
	var best_d := INF
	for i in path.size():
		var d := pos.distance_to(_pt(path, i))
		if d < best_d:
			best_d = d
			best = i
	return {"path": path, "idx": best, "progress": 0, "dir": _fwd(heading_deg), "prev": pos, "speed": 0.0}


func _sense(d: Dictionary, pos: Vector2) -> void:
	var motion: Vector2 = pos - d["prev"]
	d["speed"] = motion.length() * 60.0 / CTRL
	if motion.length() > 0.02:
		d["dir"] = motion.normalized()
	d["prev"] = pos


func _steer_to(d: Dictionary, pos: Vector2, target: Vector2, lookahead: float) -> float:
	var alpha := deg_to_rad(_angle_diff(_heading(target - pos), _heading(d["dir"])))
	if absf(alpha) > PI / 2.0:
		# Target behind: full lock toward it (a U-turn).
		return -1.0 if alpha >= 0.0 else 1.0
	var delta := atan(2.0 * WHEELBASE * sin(alpha) / maxf(lookahead, 1.0))
	return clampf(-delta / MAX_STEER, -1.0, 1.0)


func _pedals(speed: float, target: float) -> Array:
	if speed < target - 0.3:
		return [clampf(0.35 + 0.3 * (target - speed), 0.0, 1.0), 0.0]
	if speed > target + 0.8:
		return [0.0, clampf(0.15 * (speed - target), 0.0, 1.0)]
	return [0.25, 0.0]


func _control(d: Dictionary, pos: Vector2) -> Array:
	var path: Array = d["path"]
	var m := path.size()
	var drv: Dictionary = _probe.get("driver", {})
	_sense(d, pos)
	var idx: int = d["idx"]
	var best := idx
	var best_d := pos.distance_to(_pt(path, idx))
	for k in range(1, 25):
		var j := (idx + k) % m
		var dd := pos.distance_to(_pt(path, j))
		if dd < best_d:
			best_d = dd
			best = j
	d["progress"] += (best - idx + m) % m
	d["idx"] = best
	var lookahead: float = float(drv.get("lookahead", 7.0)) + float(drv.get("lookahead_gain", 0.45)) * d["speed"]
	var aim := best
	var steps := 0
	var v := INF
	while steps < 60 and pos.distance_to(_pt(path, aim)) < lookahead:
		v = minf(v, path[aim][2])
		aim = (aim + 1) % m
		steps += 1
	v = minf(v, path[aim][2])
	var steer := _steer_to(d, pos, _pt(path, aim), lookahead)
	if absf(_angle_diff(_heading(_pt(path, aim) - pos), _heading(d["dir"]))) > 60.0:
		v = minf(v, 5.0)
	var pedals := _pedals(d["speed"], v)
	return [pedals[0], pedals[1], steer]


## Follows a closed path until `stop_progress` path points are covered, the
## driver reaches `stop_index`, or `max_ticks` pass. Samples go to `rows`.
func _follow(path: Array, heading_deg: float, stop_progress: int, stop_index: int, max_ticks: int, rows: Variant) -> Variant:
	var s: Variant = await _state()
	var p: Variant = _pos(s)
	if p == null:
		return null
	var d := _driver(path, _xz(p), heading_deg)
	if rows is Array:
		rows.append(await _sample(s))
	var limit := _tick + max_ticks
	while _tick < limit:
		var u := _control(d, _xz(p))
		await _input(u[0], u[1], u[2])
		await _step(CTRL)
		s = await _state()
		p = _pos(s)
		if p == null:
			return d
		if rows is Array:
			rows.append(await _sample(s))
		if stop_progress > 0 and d["progress"] >= stop_progress:
			break
		if stop_index >= 0 and d["progress"] > 10 and (int(d["idx"]) - stop_index + path.size()) % path.size() < 12:
			break
	return d


## Drives at a fixed point at up to `speed` m/s for `max_ticks`, or until
## within `radius` metres of it.
func _seek(target: Vector2, speed: float, max_ticks: int, radius: float, rows: Variant) -> void:
	var s: Variant = await _state()
	var p: Variant = _pos(s)
	if p == null:
		return
	var heading: Variant = _num(s.get("heading_deg"))
	var d := _driver([[target.x, target.y, speed]], _xz(p), heading if heading != null else 0.0)
	var limit := _tick + max_ticks
	while _tick < limit:
		_sense(d, _xz(p))
		var to := target.distance_to(_xz(p))
		if to < radius:
			break
		var steer := _steer_to(d, _xz(p), target, maxf(to, 5.0))
		var pedals := _pedals(d["speed"], speed)
		await _input(pedals[0], pedals[1], steer)
		await _step(CTRL)
		s = await _state()
		p = _pos(s)
		if p == null:
			return
		if rows is Array:
			rows.append(await _sample(s))


## Brakes to a standstill without reversing, then lets go of every pedal.
func _stop() -> void:
	var last: Variant = _pos(await _state())
	for _i in 120:
		await _input(0.0, 1.0, 0.0)
		await _step(CTRL)
		var p: Variant = _pos(await _state())
		if p == null or last == null:
			break
		if _xz(p).distance_to(_xz(last)) * 60.0 / CTRL < 0.4:
			break
		last = p
	await _input(0.0, 0.0, 0.0)
	await _step(20)


# --- Physics queries ------------------------------------------------------------


func _cars() -> Array:
	var out := []
	for group in ["player_car", "rival_cars"]:
		for node in get_nodes_in_group(group):
			if node.is_inside_tree() and not node.is_queued_for_deletion():
				out.append(node)
	return out


func _player() -> Node3D:
	for node in get_nodes_in_group("player_car"):
		if node is Node3D and node.is_inside_tree() and not node.is_queued_for_deletion():
			return node
	return null


func _space() -> PhysicsDirectSpaceState3D:
	var car := _player()
	var world: World3D = car.get_world_3d() if car != null else root.get_world_3d()
	return world.direct_space_state


func _exclude() -> Array[RID]:
	var rids: Array[RID] = []
	for car in _cars():
		var nodes: Array = [car]
		nodes.append_array(car.find_children("*", "CollisionObject3D", true, false))
		for node in nodes:
			if node is CollisionObject3D:
				rids.append(node.get_rid())
	return rids


func _ray(from: Vector3, to: Vector3, exclude: Array[RID]) -> Dictionary:
	var query := PhysicsRayQueryParameters3D.create(from, to)
	query.exclude = exclude
	return _space().intersect_ray(query)


func _rays(data: Dictionary, open: bool) -> Array:
	var pts: Array = data["centerline"]
	var w: float = data["width"]
	var n := pts.size()
	var exclude := _exclude()
	var rows := []
	for i in n:
		var a := Vector2(pts[i][0], pts[i][1])
		var b := Vector2(pts[(i + 1) % n][0], pts[(i + 1) % n][1])
		var mid := (a + b) / 2.0
		var t := (b - a).normalized()
		var left := Vector2(t.y, -t.x)
		var row := {}
		var down := _ray(Vector3(mid.x, 3.0, mid.y), Vector3(mid.x, -3.0, mid.y), exclude)
		row["down"] = down["position"].y if down else null
		for side in [["left", 1.0], ["right", -1.0]]:
			var sign: float = side[1]
			var end: Vector2 = mid + left * sign * (w / 2.0 + 4.0)
			var hit := _ray(Vector3(mid.x, 0.5, mid.y), Vector3(end.x, 0.5, end.y), exclude)
			row[side[0]] = _xz(hit["position"]).distance_to(mid) if hit else null
		if open:
			var o := mid + left * (1.0 if i % 2 == 0 else -1.0) * (w / 2.0 + 8.0)
			var ground := _ray(Vector3(o.x, 3.0, o.y), Vector3(o.x, -3.0, o.y), exclude)
			row["outside"] = ground["position"].y if ground else null
		rows.append(row)
	return rows


# --- Run ------------------------------------------------------------------------


func _run() -> void:
	_out_path = _arg("--out")
	var phase := _arg("--phase", "logic")
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(_arg("--probe")))
	_probe = parsed if parsed is Dictionary else {}
	_out = {"phase": phase, "missing": [], "engine": Engine.get_version_info()["string"]}
	_out["has_race_autoload"] = _race() != null
	_save()
	var budget := {"logic": 300, "scene": 180, "launch": 75}
	_deadline = Time.get_ticks_msec() + int(budget.get(phase, 60)) * 1000
	if phase == "logic":
		_out["actions"] = _actions()
		if _race() != null:
			await _logic()
	elif phase == "scene":
		await _scene()
	else:
		await _launch()
	_out["completed"] = true
	_save()
	quit(0)


func _actions() -> Dictionary:
	var out := {}
	for action in InputMap.get_actions():
		var name := str(action)
		if name.begins_with("ui_"):
			continue
		var keys := []
		for event in InputMap.action_get_events(action):
			var key := event as InputEventKey
			if key != null:
				var code: int = key.keycode if key.keycode != 0 else key.physical_keycode
				if code == 0:
					code = key.key_label
				keys.append(OS.get_keycode_string(code))
		out[name] = keys
	return out


# --- The race API, headless -----------------------------------------------------


func _logic() -> void:
	var t0 := Time.get_ticks_msec()
	var tracks: Dictionary = _probe["tracks"]
	var data: Dictionary = _probe["track_data"]
	var paths: Dictionary = _probe["paths"]
	var main_heading: float = data["main"]["start"]["heading_deg"]
	var methods := {}
	for m in ["load_track", "reset", "set_input", "step", "car_state", "respawn", "add_rivals", "rival_states", "race_time"]:
		methods[m] = _race().has_method(m)
	_out["methods"] = methods
	_save()

	_section = "load"
	var ok: Variant = await _load(tracks["main"])
	var state: Variant = await _state()
	await _input(0.0, 0.0, 0.0)
	await _step(60)
	_out["load"] = {"result": _plain(ok), "state": _plain(state), "settled": _plain(await _state())}
	_save()

	_section = "bad_tracks"
	var bad := []
	_out["bad_tracks"] = bad
	for path in _probe["bad_tracks"]:
		bad.append({"result": _plain(await _load(path))})
		_save()
	_out["reload_after_bad"] = {"result": _plain(await _load(tracks["main"])), "state": _plain(await _state())}
	_save()

	_section = "rays"
	await _step(2)
	var rays := {"walls": _rays(data["main"], false)}
	await _load(tracks["cut"])
	await _step(2)
	rays["open"] = _rays(data["main"], true)
	_out["rays"] = rays
	_save()

	_section = "dynamics"
	await _load(tracks["main"])
	var dyn := {}
	await _reset()
	var rows := []
	await _input(1.0, 0.0, 0.0)
	rows.append(await _sample(await _state()))
	for _i in 30:
		await _step(4)
		rows.append(await _sample(await _state()))
	dyn["accelerate"] = rows
	rows = []
	await _input(0.0, 1.0, 0.0)
	for _i in 30:
		await _step(4)
		rows.append(await _sample(await _state()))
	dyn["brake"] = rows
	await _reset()
	rows = [await _sample(await _state())]
	await _input(0.0, 1.0, 0.0)
	for _i in 22:
		await _step(4)
		rows.append(await _sample(await _state()))
	dyn["brake_from_rest"] = rows
	for side in [["left", -1.0], ["right", 1.0]]:
		await _reset()
		rows = [await _sample(await _state())]
		await _input(0.6, 0.0, 0.0)
		for _i in 12:
			await _step(4)
			rows.append(await _sample(await _state()))
		await _input(0.6, 0.0, side[1])
		for _i in 9:
			await _step(4)
			rows.append(await _sample(await _state()))
		dyn[side[0]] = rows
	_out["dynamics"] = dyn
	_save()

	_section = "determinism"
	var runs := []
	_out["determinism"] = runs
	for r in 3:
		await _reset()
		var started: Variant = _num(await _call("race_time"))
		for row in _probe["program"]:
			await _input(row[1], row[2], row[3])
			if r < 2:
				await _step(int(row[0]))
			else:
				for _k in int(row[0]):
					await _step(1)
		runs.append({"time_after_reset": started, "time": _num(await _call("race_time")), "state": _plain(await _state())})
		_save()

	_section = "walls"
	var walls := []
	_out["walls"] = walls
	for target in _probe["wall_targets"]:
		await _reset()
		rows = [await _sample(await _state())]
		await _seek(Vector2(target[0], target[1]), 30.0, 240, 0.0, rows)
		walls.append(rows)
		_save()

	_section = "lap"
	var main_path: Array = paths["main"]
	await _reset()
	rows = []
	_out["lap"] = rows
	await _follow(main_path, main_heading, main_path.size() + 15, -1, int(_probe["lap_limit_ticks"]), rows)
	_save()

	_section = "cut"
	var cut_path: Array = paths["cut"]
	await _load(tracks["cut"])
	await _reset()
	rows = []
	_out["cut"] = rows
	await _follow(cut_path, main_heading, cut_path.size() + 15, -1, int(_probe["lap_limit_ticks"]), rows)
	_save()

	_section = "respawn"
	# On the open track: past checkpoint 1, then off the road onto the grass.
	var gates: Array = _probe["gate_index"]
	await _load(tracks["cut"])
	await _reset()
	await _follow(main_path, main_heading, 0, int(gates[1]) + 4, 3600, null)
	rows = []
	_out["respawn_drive"] = rows
	var exit: Array = _probe["respawn_exit"]
	await _seek(Vector2(exit[0], exit[1]), 8.0, 300, 3.0, rows)
	await _stop()
	var respawn := {"before": _plain(await _state())}
	await _call("respawn")
	respawn["after"] = _plain(await _state())
	await _input(0.0, 0.0, 0.0)
	await _step(60)
	respawn["settled"] = _plain(await _state())
	await _input(0.8, 0.0, 0.0)
	await _step(90)
	respawn["driven"] = _plain(await _state())
	_out["respawn"] = respawn
	_save()

	_section = "flip"
	await _load(tracks["main"])
	await _reset()
	await _input(0.0, 0.0, 0.0)
	await _step(30)
	var flip := {"flipped_up": null}
	var car := _player()
	if car != null:
		var xf := car.global_transform
		xf.basis = Basis(xf.basis.z.normalized(), PI) * xf.basis
		xf.origin += Vector3(0.0, 1.5, 0.0)
		car.global_transform = xf
		var body := car as RigidBody3D
		if body != null:
			body.linear_velocity = Vector3.ZERO
			body.angular_velocity = Vector3.ZERO
		await _step(90)
		flip["flipped"] = _plain(await _state())
		car = _player()
		flip["flipped_up"] = car.global_basis.y.y if car != null else null
		await _call("respawn")
		flip["after"] = _plain(await _state())
		await _step(60)
		flip["settled"] = _plain(await _state())
		car = _player()
		flip["settled_up"] = car.global_basis.y.y if car != null else null
	_out["flip"] = flip
	_save()

	# Turned around on the open track (a U-turn over the grass), then driven
	# against the racing direction.
	_section = "reverse"
	await _load(tracks["cut"])
	await _reset()
	rows = []
	_out["reverse"] = rows
	await _follow(paths["reverse"], main_heading, 0, -1, 900, rows)
	_save()

	# The rivals race while the player's car is parked: at the inside edge of
	# the run's track, and in the middle of the road on the held-out track,
	# where every rival has to get around it.
	_section = "rivals_main"
	await _load(tracks["main"])
	await _reset()
	await _follow(paths["park_main"], main_heading, 0, int(_probe["park_main_stop"]), 3600, null)
	await _stop()
	await _input(0.0, 1.0, 0.0)
	_out["rivals_main"] = await _rival_race(int(_probe["rival_drive_ticks"]), 6)
	_save()

	_section = "rivals_heldout"
	var loaded: Variant = await _load(tracks["heldout"])
	await _reset()
	var held_heading: float = data["heldout"]["start"]["heading_deg"]
	await _follow(paths["park_heldout"], held_heading, 0, int(_probe["park_heldout_stop"]), 3600, null)
	await _stop()
	await _input(0.0, 1.0, 0.0)
	var race: Dictionary = await _rival_race(int(_probe["heldout_limit_ticks"]), 15)
	race["loaded"] = _plain(loaded)
	_out["rivals_heldout"] = race
	_section = ""
	_out["logic_ms"] = Time.get_ticks_msec() - t0


func _rival_race(ticks: int, every: int) -> Dictionary:
	await _call("add_rivals", [int(_probe["rivals"])])
	var race := {"spawn": _plain(await _call("rival_states")), "player": _plain(await _state())}
	var samples := []
	race["samples"] = samples
	var elapsed := 0
	while elapsed < ticks:
		await _step(every)
		elapsed += every
		var player: Variant = _pos(await _state())
		var cars := []
		var states: Variant = await _call("rival_states")
		if states is Array:
			for st in states:
				var p: Variant = _pos(st)
				cars.append([p.x, p.y, p.z] if p != null else null)
		samples.append([elapsed, [player.x, player.y, player.z] if player != null else null, cars])
	return race


# --- The 3D scene ---------------------------------------------------------------


func _frames(count: int) -> void:
	for _i in count:
		await process_frame


func _camera() -> Camera3D:
	return root.get_viewport().get_camera_3d()


func _screen(world: Vector3) -> Variant:
	var camera := _camera()
	if camera == null or camera.is_position_behind(world):
		return null
	var point := camera.unproject_position(world)
	return [point.x, point.y]


func _has_mesh(node: Node) -> bool:
	if node is VisualInstance3D:
		return true
	return not node.find_children("*", "VisualInstance3D", true, false).is_empty()


func _visible_cars(group: String) -> int:
	var count := 0
	for node in get_nodes_in_group(group):
		if node is Node3D and node.is_inside_tree() and not node.is_queued_for_deletion() and node.is_visible_in_tree() and _has_mesh(node):
			count += 1
	return count


func _instance_main() -> Node:
	var path := str(ProjectSettings.get_setting("application/run/main_scene", ""))
	_out["main_scene"] = path
	if path == "" or not ResourceLoader.exists(path):
		return null
	var packed: Variant = load(path)
	if not packed is PackedScene:
		return null
	var scene: Node = packed.instantiate()
	root.add_child(scene)
	current_scene = scene
	_out["instanced"] = true
	return scene


func _hud() -> Variant:
	var texts := []
	for node in get_nodes_in_group("race_hud"):
		if node is CanvasItem and node.is_visible_in_tree() and "text" in node:
			texts.append(str(node.text))
	return " | ".join(texts) if not texts.is_empty() else null


func _ahead(at: Vector3) -> Vector3:
	var pts: Array = _probe["track_data"]["scene"]["centerline"]
	var best := 0
	var best_d := INF
	for i in pts.size():
		var d := Vector2(pts[i][0], pts[i][1]).distance_to(_xz(at))
		if d < best_d:
			best_d = d
			best = i
	var p: Array = pts[(best + 2) % pts.size()]
	return Vector3(p[0], 0.5, p[1])


func _scene_sample() -> Variant:
	var car := _player()
	var camera := _camera()
	if car == null or camera == null:
		return {}
	var at := car.global_position
	var cam := camera.global_position
	return _plain({
		"car": at,
		"camera": cam,
		"car_screen": _screen(at),
		"ahead_screen": _screen(_ahead(at)),
		"hud": _hud(),
	})


func _press(action: String, pressed: bool) -> void:
	var event := InputEventAction.new()
	event.action = action
	event.pressed = pressed
	event.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(event)


func _motion_heading(frames: int) -> Variant:
	var car := _player()
	if car == null:
		return null
	var a := car.global_position
	await _frames(frames)
	car = _player()
	if car == null:
		return null
	var d := _xz(car.global_position - a)
	return _heading(d) if d.length() > 0.05 else null


func _image() -> Image:
	await RenderingServer.frame_post_draw
	return root.get_texture().get_image()


func _image_stats(image: Image) -> Dictionary:
	var values := []
	var colors := {}
	for gx in 48:
		for gy in 27:
			var c := image.get_pixel(int((gx + 0.5) * image.get_width() / 48.0), int((gy + 0.5) * image.get_height() / 27.0))
			values.append(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b)
			colors[c.to_html(false).substr(0, 5)] = true
	var mean := 0.0
	for v in values:
		mean += v
	mean /= values.size()
	var variance := 0.0
	for v in values:
		variance += (v - mean) * (v - mean)
	return {"mean": mean, "variance": variance / values.size(), "distinct": colors.size()}


func _shot(image: Image, name: String) -> String:
	var path := _arg("--shots") + "/" + name + ".png"
	image.save_png(path)
	return path


func _scene() -> void:
	_out["renderer"] = RenderingServer.get_video_adapter_name()
	_section = "scene_load"
	if _instance_main() == null:
		return
	await _frames(30)
	var size := root.get_visible_rect().size
	_out["viewport"] = [size.x, size.y]
	_out["has_camera"] = _camera() != null
	_out["player_cars"] = _visible_cars("player_car")
	_out["track_nodes"] = get_nodes_in_group("track").size()
	var car := _player()
	_out["player_node"] = _plain(car.global_position) if car != null else null
	_out["start_state"] = _plain(await _state())
	var image := await _image()
	_out["image"] = _image_stats(image)
	_out["shot_start"] = _shot(image, "start")
	_save()

	_section = "keyboard"
	var kb := {}
	_out["keyboard"] = kb
	var drive := []
	_out["drive"] = drive
	_press("accelerate", true)
	for _i in 12:
		await _frames(10)
		drive.append(_scene_sample())
	kb["accelerated"] = _plain(await _state())
	_save()
	# Short pulses each way, so a car on a 12 m road turns and comes back
	# without reaching a barrier. Each turn is measured from straight-line
	# motion just before its own pulse to the motion just after it, so a car
	# whose wheels take a moment to turn is judged fairly.
	var before_left: Variant = await _motion_heading(6)
	_press("steer_left", true)
	await _frames(14)
	_press("steer_left", false)
	await _frames(6)
	var after_left: Variant = await _motion_heading(6)
	await _frames(10)
	var before_right: Variant = await _motion_heading(6)
	_press("steer_right", true)
	await _frames(14)
	_press("steer_right", false)
	await _frames(6)
	var after_right: Variant = await _motion_heading(6)
	if before_left != null and after_left != null:
		kb["turn_left"] = _plain(_angle_diff(after_left, before_left))
	if before_right != null and after_right != null:
		kb["turn_right"] = _plain(_angle_diff(after_right, before_right))
	await _frames(12)
	_press("accelerate", false)
	kb["before_brake"] = _plain(await _state())
	_press("brake", true)
	await _frames(90)
	kb["braked"] = _plain(await _state())
	_press("brake", false)
	_press("accelerate", true)
	await _frames(40)
	kb["before_reset"] = _plain(await _state())
	_press("accelerate", false)
	_press("reset_car", true)
	await _frames(3)
	_press("reset_car", false)
	await _frames(15)
	kb["after_reset"] = _plain(await _state())
	_out["shot_driven"] = _shot(await _image(), "driven")
	_save()

	# Frame time over a short window, as an observation (ADR-0073): software
	# rendering on a CPU, comparable between runs, not a statement about a GPU.
	_section = "frame_time"
	var times := []
	for _i in 40:
		var started := Time.get_ticks_usec()
		await process_frame
		times.append((Time.get_ticks_usec() - started) / 1000.0)
	_out["frame_ms"] = times
	_out["shot_final"] = _shot(await _image(), "final")
	_section = ""


# --- A plain launch -------------------------------------------------------------


func _launch() -> void:
	_section = "launch"
	if _instance_main() == null:
		return
	await _frames(10)
	_out["state"] = _plain(await _state())
	_out["rivals"] = _plain(await _call("rival_states"))
	_out["rival_nodes"] = _visible_cars("rival_cars")
	_save()
	await _frames(300)
	_out["rivals_later"] = _plain(await _call("rival_states"))
	_out["state_later"] = _plain(await _state())
	_section = ""
