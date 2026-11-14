# The lab's harvester for opencity3d-v1 (ADR-0078). It judges nothing: it loads
# the agent's project, drives the `City` contract with the probes and the
# held-out map the Python side generated for this run, and writes every raw
# answer to a JSON file. Verdicts are computed afterwards in Python against the
# map file itself, so a crash here costs answers, never a wrong verdict.
#
# Copied into a scratch copy of the agent's project as
# res://__lab_grader__/harvest.gd and run twice:
#   godot --headless --fixed-fps 60 --path <copy> --script res://__lab_grader__/harvest.gd \
#       -- --probe <probe.json> --out <logic.json> --phase logic
#   godot-render --fixed-fps 60 --path <copy> --script res://__lab_grader__/harvest.gd \
#       -- --probe <probe.json> --out <scene.json> --phase scene --shots <dir> --map=<held-out>
#
# Every call into the agent's code goes through `_call`, which records a
# missing method instead of failing and gives up on a call that never returns.
# A script error inside one of the agent's methods does not stop Godot (it logs
# and returns a default), so one broken function costs only the checks that
# depend on it. Answers are flushed after every section.
extends SceneTree

const METHODS := [
	"load_map",
	"building_count",
	"building_info",
	"ground_height_at",
	"player_state",
	"set_player_input",
	"set_vehicle_input",
	"step",
	"teleport_player",
	"enter_nearest_vehicle",
	"exit_vehicle",
	"spawn_parked_car",
	"traffic_states",
	"set_traffic",
	"minimap_player_marker",
]
const ACTIONS := [
	"move_forward",
	"move_back",
	"move_left",
	"move_right",
	"sprint",
	"interact",
	"accelerate",
	"brake",
	"steer_left",
	"steer_right",
]
# How long one contract call may take before the harvest gives up on it.
const CALL_TIMEOUT_MS := 20000
const LOAD_TIMEOUT_MS := 60000
const GRID := 8

var _out := {}
var _probe := {}
var _out_path := ""
var _ticks := 0
var _hung := {}
var _magenta: StandardMaterial3D


func _initialize() -> void:
	physics_frame.connect(_on_physics_frame)
	_run.call_deferred()


func _on_physics_frame() -> void:
	_ticks += 1


func _arg(name: String, default: String = "") -> String:
	var args := OS.get_cmdline_user_args()
	var i := args.find(name)
	return args[i + 1] if i >= 0 and i + 1 < args.size() else default


func _save() -> void:
	var file := FileAccess.open(_out_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(_out, "", true, true))
		file.close()


func _city() -> Object:
	return root.get_node_or_null("City")


# JSON-friendly copy of an answer: vectors become arrays, integral floats
# become ints (map ids arrive as floats from Godot's JSON parser), NaN is null.
func _plain(value: Variant) -> Variant:
	var kind := typeof(value)
	if kind == TYPE_FLOAT:
		if is_nan(value) or is_inf(value):
			return null
		if value == floor(value) and absf(value) < 9.0e15:
			return int(value)
		return value
	if kind == TYPE_VECTOR2 or kind == TYPE_VECTOR2I:
		return [_plain(float(value.x)), _plain(float(value.y))]
	if kind == TYPE_VECTOR3 or kind == TYPE_VECTOR3I:
		return [_plain(float(value.x)), _plain(float(value.y)), _plain(float(value.z))]
	if kind == TYPE_STRING_NAME or kind == TYPE_NODE_PATH:
		return str(value)
	if kind == TYPE_DICTIONARY:
		var out := {}
		for key in value:
			out[str(key)] = _plain(value[key])
		return out
	if kind == TYPE_ARRAY or (kind >= TYPE_PACKED_BYTE_ARRAY and kind <= TYPE_PACKED_VECTOR4_ARRAY):
		var items := []
		for item in value:
			items.append(_plain(item))
		return items
	if kind == TYPE_OBJECT:
		return null
	return value


func _invoke(target: Object, method: String, args: Array, box: Dictionary) -> void:
	box["value"] = await target.callv(method, args)
	box["done"] = true


# Calls a contract method, sync or coroutine, and waits for it at most
# CALL_TIMEOUT_MS. A method that hung once is not called again.
func _call(method: String, args: Array = []) -> Variant:
	var city := _city()
	if city == null or not city.has_method(method):
		if not _out["missing"].has(method):
			_out["missing"].append(method)
		return null
	if _hung.has(method):
		return null
	var box := {"done": false, "value": null}
	_invoke(city, method, args, box)
	var deadline := Time.get_ticks_msec() + (LOAD_TIMEOUT_MS if method == "load_map" else CALL_TIMEOUT_MS)
	while not box["done"] and Time.get_ticks_msec() < deadline:
		await process_frame
	if not box["done"]:
		_hung[method] = true
		_out["hung"].append(method)
		return null
	return box["value"]


func _advance(ticks: int) -> void:
	var target := _ticks + ticks
	var guard := 0
	while _ticks < target and guard < ticks * 4 + 60:
		await physics_frame
		guard += 1


func _frames(count: int) -> void:
	for _i in count:
		await process_frame


func _state() -> Variant:
	return _plain(await _call("player_state"))


func _sample(count: int, every: int) -> Array:
	var rows := [await _state()]
	for _i in count - 1:
		await _advance(every)
		rows.append(await _state())
	return rows


func _run() -> void:
	_out_path = _arg("--out")
	var phase := _arg("--phase", "logic")
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(_arg("--probe")))
	_probe = parsed if parsed is Dictionary else {}
	_out = {"phase": phase, "missing": [], "hung": [], "engine": Engine.get_version_info()["string"]}
	_out["has_city_autoload"] = _city() != null
	var methods := []
	if _city() != null:
		for method in METHODS:
			if _city().has_method(method):
				methods.append(method)
	_out["methods"] = methods
	var actions := {}
	for action in ACTIONS:
		actions[action] = InputMap.action_get_events(action).size() if InputMap.has_action(action) else 0
	_out["actions"] = actions
	_out["physics_hz"] = Engine.physics_ticks_per_second
	_save()
	_magenta = StandardMaterial3D.new()
	_magenta.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_magenta.albedo_color = Color(1, 0, 1)
	_magenta.cull_mode = BaseMaterial3D.CULL_DISABLED
	if phase == "logic":
		await _logic()
	else:
		await _scene()
	_out["completed"] = true
	_save()
	quit(0)


func _start_main_scene() -> bool:
	var path := str(ProjectSettings.get_setting("application/run/main_scene", ""))
	_out["main_scene"] = path
	if path == "" or not ResourceLoader.exists(path):
		return false
	var packed: Variant = load(path)
	if not packed is PackedScene:
		return false
	var scene: Node = packed.instantiate()
	root.add_child(scene)
	current_scene = scene
	_out["instanced"] = true
	return true


# building_count() once it stops changing: a loader may spread its work.
func _settled_count() -> Variant:
	var last: Variant = null
	var stable := 0
	for _i in 60:
		await _frames(10)
		var count: Variant = _plain(await _call("building_count"))
		if count is int and count > 0 and count == last:
			stable += 1
			if stable >= 2:
				break
		else:
			stable = 0
		last = count
		if _city() == null or not _city().has_method("building_count"):
			break
	return last


func _ray(at: Array) -> Variant:
	var from := Vector3(float(at[0]), 1000.0, float(at[1]))
	var query := PhysicsRayQueryParameters3D.create(from, from - Vector3(0.0, 1100.0, 0.0))
	var hit := root.get_world_3d().direct_space_state.intersect_ray(query)
	return _plain(hit["position"].y) if hit.has("position") else null


func _w(at: Array) -> Vector3:
	return Vector3(float(at[0]), 0.0, float(at[1]))


func _geometry(node: Node) -> Array:
	var out := []
	if node is MeshInstance3D or node is MultiMeshInstance3D or node is CSGShape3D:
		out.append(node)
	for child in node.get_children():
		out.append_array(_geometry(child))
	return out


func _aabb(node: Node) -> Variant:
	var total := AABB()
	var any := false
	for g in _geometry(node):
		var box: AABB = g.global_transform * g.get_aabb()
		if box.size == Vector3.ZERO:
			continue
		total = box if not any else total.merge(box)
		any = true
	if not any:
		return null
	var end := total.end
	return [total.position.x, total.position.y, total.position.z, end.x, end.y, end.z]


# --- The API, headless ----------------------------------------------------------


func _logic() -> void:
	var t0 := Time.get_ticks_msec()
	await _start_main_scene()
	# The game as launched with no --map argument.
	_out["default_scene"] = {"count": await _settled_count()}
	_save()
	if _city() == null:
		return

	var started := Time.get_ticks_msec()
	var ok: Variant = await _call("load_map", [_probe["default_map"]])
	_out["default"] = {"ok": _plain(ok), "count": _plain(await _call("building_count")), "ms": Time.get_ticks_msec() - started}
	var bad := []
	for path in _probe["bad_maps"]:
		bad.append({"ok": _plain(await _call("load_map", [path]))})
	_out["bad"] = bad
	started = Time.get_ticks_msec()
	ok = await _call("load_map", [_probe["heldout_path"]])
	_out["heldout"] = {"ok": _plain(ok), "ms": Time.get_ticks_msec() - started}
	_out["heldout"]["count"] = _plain(await _call("building_count"))
	_save()
	await _call("set_traffic", [0])
	await _advance(10)
	_out["spawn"] = await _state()

	# What was built, read from the API and from the scene tree.
	var info := []
	var nodes := {}
	for id in _probe["info_ids"]:
		var row: Variant = _plain(await _call("building_info", [int(id)]))
		info.append({"id": int(id), "info": row})
		var found: Node = null
		if row is Dictionary and row.get("node_path") is String and row["node_path"] != "":
			found = root.get_node_or_null(NodePath(row["node_path"]))
		nodes[str(int(id))] = {
			"found": found != null,
			"building_id": _plain(found.get_meta("building_id", null)) if found != null else null,
			"in_group": found != null and found.is_in_group("buildings"),
		}
	_out["info"] = info
	_out["info_nodes"] = nodes
	_out["info_unknown"] = _plain(await _call("building_info", [int(_probe["unknown_id"])]))
	var buildings := []
	for node in get_nodes_in_group("buildings"):
		buildings.append({"id": _plain(node.get_meta("building_id", null)), "aabb": _aabb(node)})
	_out["buildings"] = buildings
	_save()

	# The lab's own rays, straight down onto the built world.
	await _advance(2)
	var rays := {"roofs": [], "streets": [], "band": []}
	for item in _probe["roof_points"]:
		rays["roofs"].append(_ray(item["at"]))
	for at in _probe["street_points"]:
		rays["streets"].append(_ray(at))
	for at in _probe["band_points"]:
		rays["band"].append(_ray(at))
	_out["rays"] = rays

	# ground_height_at() on roofs, streets, and inside footprint boxes.
	for key in ["roof", "notch"]:
		var heights := []
		for item in _probe[key + "_points"]:
			heights.append(_plain(await _call("ground_height_at", [float(item["at"][0]), float(item["at"][1])])))
		_out[key + "_heights"] = heights
	var streets := []
	for at in _probe["street_points"]:
		streets.append(_plain(await _call("ground_height_at", [float(at[0]), float(at[1])])))
	_out["street_heights"] = streets
	# A solid box only the lab knows about: a height read from the map file
	# cannot see it, a ray against the built world does.
	var spec: Dictionary = _probe["box"]
	var body := StaticBody3D.new()
	body.collision_layer = 0xFFFFFFFF
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(6.0, float(spec["height"]), 6.0)
	shape.shape = box
	body.add_child(shape)
	body.position = _w(spec["at"]) + Vector3(0.0, float(spec["height"]) / 2.0, 0.0)
	root.add_child(body)
	await _advance(2)
	_out["box_height"] = _plain(await _call("ground_height_at", [float(spec["at"][0]), float(spec["at"][1])]))
	body.queue_free()
	await _advance(2)
	_save()

	# step(n) advances exactly n physics ticks.
	var ticks := []
	for asked in [1, 7, 30]:
		var before := _ticks
		await _call("step", [asked])
		ticks.append({"asked": asked, "counted": _ticks - before})
	_out["ticks"] = ticks

	# Teleports, and the minimap marker wherever the player stands.
	var teleports := []
	var markers := []
	var places: Array = _probe["teleports"].duplicate()
	if _probe["walk"] is Dictionary:
		places.append(_probe["walk"]["start"])
	for at in places:
		await _call("teleport_player", [float(at[0]), float(at[1])])
		await _advance(10)
		var state: Variant = await _state()
		if teleports.size() < _probe["teleports"].size():
			teleports.append({"state": state})
		markers.append({"state": state, "uv": _plain(await _call("minimap_player_marker"))})
	_out["teleports"] = teleports
	_out["markers"] = markers
	_save()

	# Ambient traffic, with the player out of the city.
	var traffic := {}
	await _call("set_traffic", [int(_probe["traffic"]["first"])])
	await _advance(5)
	traffic["first"] = _plain(await _call("traffic_states"))
	traffic["group_first"] = get_nodes_in_group("traffic").size()
	var samples := [traffic["first"]]
	for _i in int(_probe["traffic"]["samples"]) - 1:
		await _advance(int(_probe["traffic"]["every"]))
		samples.append(_plain(await _call("traffic_states")))
	traffic["samples"] = samples
	await _call("set_traffic", [int(_probe["traffic"]["second"])])
	await _advance(5)
	traffic["second"] = _plain(await _call("traffic_states"))
	_out["traffic"] = traffic
	await _call("set_traffic", [0])
	await _advance(5)
	_save()

	# On foot: walk, sprint, and walk into a wall.
	var walk: Dictionary = _probe["walk"] if _probe["walk"] is Dictionary else {}
	for key in ([] if walk.is_empty() else ["walk", "sprint"]):
		await _call("teleport_player", [float(walk["start"][0]), float(walk["start"][1])])
		await _call("set_player_input", [0.0, 0.0, false])
		await _advance(10)
		await _call("set_player_input", [float(walk["dir"][0]), float(walk["dir"][1]), key == "sprint"])
		var row := {"samples": await _sample(7, 30)}
		await _call("set_player_input", [0.0, 0.0, false])
		await _advance(60)
		row["stopped"] = await _state()
		_out[key] = row
	var wall: Dictionary = _probe["wall"] if _probe["wall"] is Dictionary else {}
	if not wall.is_empty():
		await _call("teleport_player", [float(wall["start"][0]), float(wall["start"][1])])
		await _advance(10)
		await _call("set_player_input", [float(wall["dir"][0]), float(wall["dir"][1]), false])
		_out["wall"] = {"samples": await _sample(25, 20)}
		await _call("set_player_input", [0.0, 0.0, false])
	_save()

	# Vehicles: refuse a far car, enter a near one, drive, brake, get out.
	var run: Dictionary = _probe["run"]
	await _call("teleport_player", [float(run["player"][0]), float(run["player"][1])])
	await _advance(10)
	var enter := {"far": _plain(await _call("enter_nearest_vehicle"))}
	enter["vehicle_id"] = _plain(await _call("spawn_parked_car", [float(run["car"][0]), float(run["car"][1]), float(run["heading"])]))
	await _advance(20)
	var vehicles := []
	for node in get_nodes_in_group("vehicles"):
		if node is Node3D:
			vehicles.append(_plain(node.global_position))
	enter["vehicles"] = vehicles
	enter["entered"] = _plain(await _call("enter_nearest_vehicle"))
	await _advance(5)
	enter["state"] = await _state()
	_out["enter"] = enter
	await _call("set_vehicle_input", [1.0, 0.0, 0.0])
	_out["drive"] = {"samples": await _sample(7, 30)}
	await _call("set_vehicle_input", [0.0, 1.0, 0.0])
	var braking := await _sample(7, 30)
	_out["brake"] = {"samples": braking}
	var leave := {"exited": _plain(await _call("exit_vehicle"))}
	await _advance(10)
	leave["state"] = await _state()
	leave["car_state"] = braking[-1]
	_out["exit"] = leave
	await _call("set_vehicle_input", [0.0, 0.0, 0.0])
	_save()

	for key in ["steer", "car_wall"]:
		var item: Variant = _probe[key]
		if not item is Dictionary:
			continue
		await _call("teleport_player", [float(item["player"][0]), float(item["player"][1])])
		await _advance(10)
		var row := {"vehicle_id": _plain(await _call("spawn_parked_car", [float(item["car"][0]), float(item["car"][1]), float(item["heading"])]))}
		await _advance(20)
		row["entered"] = _plain(await _call("enter_nearest_vehicle"))
		await _advance(5)
		row["state"] = await _state()
		_out[key + "_enter"] = row
		if key == "steer":
			await _call("set_vehicle_input", [1.0, 0.0, 1.0])
			_out[key] = {"samples": await _sample(25, 10)}
		else:
			await _call("set_vehicle_input", [1.0, 0.0, 0.0])
			_out[key] = {"samples": await _sample(21, 15)}
		await _call("set_vehicle_input", [0.0, 1.0, 0.0])
		await _advance(90)
		await _call("exit_vehicle")
		await _call("set_vehicle_input", [0.0, 0.0, 0.0])
		_save()
	_out["logic_ms"] = Time.get_ticks_msec() - t0


# --- The scene, rendered ---------------------------------------------------------


func _camera() -> Camera3D:
	return root.get_viewport().get_camera_3d()


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


func _is_magenta(c: Color) -> bool:
	return c.r > 0.75 and c.g < 0.3 and c.b > 0.75


func _plain_environment() -> Environment:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0, 0, 0)
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.fog_enabled = false
	env.glow_enabled = false
	return env


# Paints every building magenta (unshaded, both faces) until restored.
func _paint_buildings() -> Dictionary:
	var saved := {}
	for building in get_nodes_in_group("buildings"):
		for g in _geometry(building):
			saved[g] = g.material_override
			g.material_override = _magenta
	return saved


func _restore(saved: Dictionary) -> void:
	for g in saved:
		if is_instance_valid(g):
			g.material_override = saved[g]


func _labels(group: String) -> Array:
	var rows := []
	for node in get_nodes_in_group(group):
		if node is Control:
			var rect: Rect2 = node.get_global_rect()
			rows.append({
				"text": str(node.get("text")) if "text" in node else "",
				"visible": node.is_visible_in_tree(),
				"rect": [rect.position.x, rect.position.y, rect.size.x, rect.size.y],
			})
	return rows


func _controls(group: String, image: Image) -> Array:
	var rows := []
	for node in get_nodes_in_group(group):
		if node is Control:
			var rect: Rect2 = node.get_global_rect()
			var colors := {}
			for gx in 12:
				for gy in 12:
					var x := int(rect.position.x + (gx + 0.5) * rect.size.x / 12.0)
					var y := int(rect.position.y + (gy + 0.5) * rect.size.y / 12.0)
					if x >= 0 and y >= 0 and x < image.get_width() and y < image.get_height():
						colors[image.get_pixel(x, y).to_html(false).substr(0, 5)] = true
			rows.append({
				"visible": node.is_visible_in_tree(),
				"rect": [rect.position.x, rect.position.y, rect.size.x, rect.size.y],
				"distinct": colors.size(),
			})
	return rows


func _follow() -> Dictionary:
	var camera := _camera()
	var player := get_first_node_in_group("player")
	if camera == null or not player is Node3D:
		return {}
	var at: Vector3 = player.global_position
	var size := root.get_visible_rect().size
	var screen: Variant = null
	if not camera.is_position_behind(at):
		var point := camera.unproject_position(at)
		screen = [point.x, point.y]
	return {"camera": _plain(camera.global_position), "player": _plain(at), "screen": screen, "viewport": [size.x, size.y]}


# The share of the game camera's view that is building.
func _building_share() -> Variant:
	var camera := _camera()
	if camera == null:
		return null
	var saved_env := camera.environment
	camera.environment = _plain_environment()
	var saved := _paint_buildings()
	await _frames(3)
	var image := await _image()
	_restore(saved)
	camera.environment = saved_env
	_shot(image, "buildings_painted")
	var hits := 0
	for gx in 48:
		for gy in 27:
			if _is_magenta(image.get_pixel(int((gx + 0.5) * image.get_width() / 48.0), int((gy + 0.5) * image.get_height() / 27.0))):
				hits += 1
	return hits / float(48 * 27)


# A top-down orthographic view in a viewport of its own, so the game's camera
# and HUD are untouched: which pixels are roofs.
func _top_down() -> Dictionary:
	var spec: Dictionary = _probe["ortho"]
	var size := root.get_visible_rect().size
	var view := SubViewport.new()
	view.size = Vector2i(int(size.x), int(size.y))
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = float(spec["size"])
	camera.near = 1.0
	camera.far = 2000.0
	camera.environment = _plain_environment()
	camera.transform = Transform3D(Basis(Vector3.RIGHT, -PI / 2.0), Vector3(float(spec["center"][0]), 600.0, float(spec["center"][1])))
	view.add_child(camera)
	root.add_child(view)
	camera.current = true
	var saved := _paint_buildings()
	await _frames(4)
	await RenderingServer.frame_post_draw
	var image := view.get_texture().get_image()
	_restore(saved)
	view.queue_free()
	_shot(image, "top_down")
	var cols := image.get_width() / GRID
	var rows := image.get_height() / GRID
	var mask := PackedStringArray()
	for r in rows:
		for c in cols:
			mask.append("1" if _is_magenta(image.get_pixel(c * GRID + GRID / 2, r * GRID + GRID / 2)) else "0")
	return {"width": image.get_width(), "height": image.get_height(), "cols": cols, "rows": rows, "mask": "".join(mask)}


func _action(name: String, pressed: bool) -> void:
	var event := InputEventAction.new()
	event.action = name
	event.pressed = pressed
	event.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(event)


func _tap(name: String) -> void:
	_action(name, true)
	await _frames(4)
	_action(name, false)
	await _frames(12)


func _scene() -> void:
	if not await _start_main_scene():
		return
	_out["building_count"] = await _settled_count()
	await _frames(10)
	var size := root.get_visible_rect().size
	_out["viewport"] = [size.x, size.y]
	_out["has_camera"] = _camera() != null
	_out["players"] = get_nodes_in_group("player").size()
	await _call("set_traffic", [0])
	await _frames(10)
	_out["follow_start"] = _follow()
	var image := await _image()
	_out["image_start"] = _image_stats(image)
	_out["shot_start"] = _shot(image, "start")
	_out["attribution"] = _labels("attribution")
	_out["minimap"] = _controls("minimap", image)
	_save()
	_out["buildings_share"] = await _building_share()
	_out["ortho"] = await _top_down()
	_save()

	# The camera follows the player to a new place.
	var at: Array = _probe["teleports"][0] if not _probe["teleports"].is_empty() else _probe["keys"]["player"]
	await _call("teleport_player", [float(at[0]), float(at[1])])
	await _frames(60)
	_out["follow_moved"] = _follow()
	_save()

	# The keyboard, through the input actions.
	var keys: Dictionary = _probe["keys"]
	await _call("teleport_player", [float(keys["player"][0]), float(keys["player"][1])])
	await _frames(20)
	var walk := {"before": await _state()}
	_action("move_forward", true)
	await _frames(120)
	_action("move_forward", false)
	await _frames(10)
	walk["after"] = await _state()
	_out["keys_walk"] = walk
	await _call("teleport_player", [float(keys["player"][0]), float(keys["player"][1])])
	await _frames(10)
	await _call("spawn_parked_car", [float(keys["car"][0]), float(keys["car"][1]), float(keys["heading"])])
	await _frames(20)
	var drive := {}
	await _tap("interact")
	drive["entered"] = await _state()
	_action("accelerate", true)
	await _frames(120)
	_action("accelerate", false)
	drive["driven"] = await _state()
	_action("brake", true)
	await _frames(120)
	_action("brake", false)
	await _tap("interact")
	await _frames(10)
	drive["left"] = await _state()
	_out["keys_drive"] = drive
	_save()

	# Frame time on the full shipped city with traffic: an observation (ADR-0073).
	await _call("load_map", [_probe["default_map"]])
	await _frames(10)
	await _call("set_traffic", [8])
	await _frames(30)
	var times := []
	for _i in 60:
		var started := Time.get_ticks_usec()
		await process_frame
		times.append((Time.get_ticks_usec() - started) / 1000.0)
	_out["frame_ms"] = times
	_out["shot_final"] = _shot(await _image(), "final")
