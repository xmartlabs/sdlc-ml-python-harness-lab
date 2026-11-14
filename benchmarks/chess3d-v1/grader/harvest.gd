# The lab's harvester for chess3d-v1 (ADR-0078). It judges nothing: it loads the
# agent's project, drives its public contract with the probes the Python side
# generated for this run, and writes every raw answer to a JSON file. Verdicts
# are computed afterwards in Python against an independent rules engine, so a
# crash here costs answers, never a wrong verdict.
#
# Copied into a scratch copy of the agent's project as
# res://__lab_grader__/harvest.gd and run twice:
#   godot --headless --path <copy> --script res://__lab_grader__/harvest.gd \
#       -- --probe <probe.json> --out <logic.json> --phase logic
#   godot-render --path <copy> --script res://__lab_grader__/harvest.gd \
#       -- --probe <probe.json> --out <scene.json> --phase scene --shots <dir>
#
# Every call into the agent's code goes through `_call`, which records a
# missing method instead of failing. A script error inside one of the agent's
# methods does not stop Godot (it logs and returns a default), so one broken
# function costs only the checks that depend on it. Answers are flushed after
# every section, so a hang late in the run keeps everything before it.
extends SceneTree

var _out := {}
var _probe := {}
var _out_path := ""


func _initialize() -> void:
	_run.call_deferred()


func _arg(name: String, default: String = "") -> String:
	var args := OS.get_cmdline_user_args()
	var i := args.find(name)
	return args[i + 1] if i >= 0 and i + 1 < args.size() else default


func _save() -> void:
	var file := FileAccess.open(_out_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(_out, "  "))
		file.close()


func _chess() -> Object:
	return root.get_node_or_null("Chess")


func _call(method: String, args: Array = []) -> Variant:
	var chess := _chess()
	if chess == null or not chess.has_method(method):
		if not _out["missing"].has(method):
			_out["missing"].append(method)
		return null
	return chess.callv(method, args)


func _strings(value: Variant) -> Variant:
	# A move list may come back as PackedStringArray or Array; JSON wants Array.
	if value is PackedStringArray or value is Array:
		var items := []
		for item in value:
			items.append(str(item))
		return items
	return null


func _text(value: Variant) -> Variant:
	return str(value) if value is String or value is StringName else null


func _run() -> void:
	_out_path = _arg("--out")
	var phase := _arg("--phase", "logic")
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(_arg("--probe")))
	_probe = parsed if parsed is Dictionary else {}
	_out = {"phase": phase, "missing": [], "engine": Engine.get_version_info()["string"]}
	_out["has_chess_autoload"] = _chess() != null
	_out["has_position_changed"] = _chess() != null and _chess().has_signal("position_changed")
	_save()
	if _chess() != null:
		if phase == "logic":
			await _logic()
		else:
			await _scene()
	_out["completed"] = true
	_save()
	quit(0)


# --- Rules --------------------------------------------------------------------


func _logic() -> void:
	var t0 := Time.get_ticks_msec()
	_call("load_fen", [_probe.get("start_fen", "")])
	_out["start_fen"] = _text(_call("fen"))

	# Legal move sets and status, for fixed trap positions and per-run ones.
	var legal := []
	for fen in _probe.get("legal_positions", []):
		_call("load_fen", [fen])
		legal.append({"fen": fen, "moves": _strings(_call("legal_moves")), "status": _text(_call("status"))})
	_out["legal"] = legal
	_save()

	# Perft, cheapest first, flushed after each so a slow engine is still
	# measured on whatever depths it reached.
	var perft := []
	_out["perft"] = perft
	for item in _probe.get("perft", []):
		_call("load_fen", [item["fen"]])
		var started := Time.get_ticks_msec()
		var nodes: Variant = _call("perft", [int(item["depth"])])
		perft.append({"fen": item["fen"], "depth": item["depth"], "nodes": nodes, "ms": Time.get_ticks_msec() - started})
		_save()

	# Whole games replayed: SAN out, FEN after each move, end state.
	var games := []
	for game in _probe.get("games", []):
		_call("load_fen", [game["fen"]])
		var sans := []
		var fens := []
		for uci in game["uci"]:
			sans.append(_text(_call("play", [uci])))
			fens.append(_text(_call("fen")))
		games.append({"sans": sans, "fens": fens, "status": _text(_call("status")), "result": _text(_call("result")), "history": _strings(_call("history_san"))})
	_out["games"] = games
	_save()

	# Every legal move of a crowded position, each from the same start: the
	# SAN disambiguation cases a replayed game rarely reaches.
	var san_all := []
	for item in _probe.get("san_positions", []):
		var sans := []
		for uci in item["uci"]:
			_call("load_fen", [item["fen"]])
			sans.append(_text(_call("play", [uci])))
		san_all.append({"fen": item["fen"], "sans": sans})
	_out["san_all"] = san_all

	# SAN as input.
	var san_input := []
	for item in _probe.get("san_input", []):
		_call("load_fen", [item["fen"]])
		var played: Variant = _call("play", [item["san"]])
		san_input.append({"played": _text(played), "fen": _text(_call("fen"))})
	_out["san_input"] = san_input

	# Illegal input must be refused and leave the position untouched.
	var illegal := []
	for item in _probe.get("illegal", []):
		_call("load_fen", [item["fen"]])
		var played: Variant = _call("play", [item["move"]])
		illegal.append({"played": _text(played), "fen": _text(_call("fen"))})
	_out["illegal"] = illegal

	# Undo restores the exact prior position, castling and en passant included.
	var undo := []
	for item in _probe.get("undo", []):
		_call("load_fen", [item["fen"]])
		for uci in item["uci"]:
			_call("play", [uci])
		for _i in item["uci"].size():
			_call("undo")
		undo.append({"fen": _text(_call("fen"))})
	_out["undo"] = undo
	_save()

	# The AI: a legal reply that leaves the position alone, and forced mates.
	var budget := int(_probe.get("ai_budget_ms", 5000))
	var ai := []
	_out["ai"] = ai
	for item in _probe.get("ai", []):
		_call("load_fen", [item["fen"]])
		var started := Time.get_ticks_msec()
		var move: Variant = _call("best_move", [budget])
		ai.append({"kind": item["kind"], "fen": item["fen"], "move": _text(move), "ms": Time.get_ticks_msec() - started, "fen_after": _text(_call("fen"))})
		_save()
	_out["logic_ms"] = Time.get_ticks_msec() - t0


# --- The 3D scene -------------------------------------------------------------


func _frames(count: int) -> void:
	for _i in count:
		await process_frame


func _camera() -> Camera3D:
	return root.get_viewport().get_camera_3d()


func _board() -> Node:
	return get_first_node_in_group("chess_board")


func _screen(world: Vector3) -> Variant:
	var camera := _camera()
	if camera == null or camera.is_position_behind(world):
		return null
	var point := camera.unproject_position(world)
	return [point.x, point.y]


func _square_world(square: String) -> Variant:
	var board := _board()
	if board == null or not board.has_method("square_to_world"):
		return null
	var world: Variant = board.call("square_to_world", square)
	return world if world is Vector3 else null


func _square_screen(square: String) -> Variant:
	var world: Variant = _square_world(square)
	return _screen(world) if world is Vector3 else null


func _pieces() -> Array:
	var rows := []
	for node in get_nodes_in_group("chess_pieces"):
		if node is Node3D and node.is_visible_in_tree():
			var at: Vector3 = node.global_position
			rows.append({"square": str(node.get_meta("square", "")), "piece": str(node.get_meta("piece", "")), "world": [at.x, at.y, at.z]})
	return rows


func _state() -> Dictionary:
	return {"fen": _text(_call("fen")), "pieces": _pieces()}


func _click_at(at: Vector2) -> void:
	var motion := InputEventMouseMotion.new()
	motion.position = at
	motion.global_position = at
	root.push_input(motion)
	await _frames(2)
	for pressed in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.pressed = pressed
		event.position = at
		event.global_position = at
		root.push_input(event)
		await _frames(3)


func _click_move(from_square: String, to_square: String) -> bool:
	var from: Variant = _square_screen(from_square)
	var to: Variant = _square_screen(to_square)
	if not (from is Array and to is Array):
		return false
	await _click_at(Vector2(from[0], from[1]))
	await _click_at(Vector2(to[0], to[1]))
	# Room for a move animation.
	await _frames(45)
	return true


func _load_position(fen: String) -> void:
	_call("load_fen", [fen])
	await _frames(20)


func _image() -> Image:
	await RenderingServer.frame_post_draw
	return root.get_texture().get_image()


func _luma(image: Image, at: Variant) -> Variant:
	if not at is Array:
		return null
	var cx := int(at[0])
	var cy := int(at[1])
	var total := 0.0
	var count := 0
	for dx in range(-3, 4):
		for dy in range(-3, 4):
			var x := cx + dx
			var y := cy + dy
			if x >= 0 and y >= 0 and x < image.get_width() and y < image.get_height():
				var c := image.get_pixel(x, y)
				total += 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
				count += 1
	return total / count if count > 0 else null


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
	var path := str(ProjectSettings.get_setting("application/run/main_scene", ""))
	_out["main_scene"] = path
	_out["renderer"] = RenderingServer.get_video_adapter_name()
	if path == "" or not ResourceLoader.exists(path):
		return
	var packed: Variant = load(path)
	if not packed is PackedScene:
		return
	var scene: Node = packed.instantiate()
	root.add_child(scene)
	current_scene = scene
	# From here a crash is the game's: the lab's display stack came up.
	_out["instanced"] = true
	_save()
	await _frames(15)
	var size := root.get_visible_rect().size
	_out["viewport"] = [size.x, size.y]
	_out["has_camera"] = _camera() != null
	_out["has_board"] = _board() != null
	_out["has_square_to_world"] = _board() != null and _board().has_method("square_to_world")

	var squares := {}
	for file in "abcdefgh":
		for rank in range(1, 9):
			var square := "%s%d" % [file, rank]
			var world: Variant = _square_world(square)
			squares[square] = {"world": [world.x, world.y, world.z] if world is Vector3 else null, "screen": _square_screen(square)}
	_out["squares"] = squares
	_out["start"] = _state()
	var image := await _image()
	_out["image_start"] = _image_stats(image)
	var luma := {}
	for square in _probe.get("color_squares", []):
		luma[square] = _luma(image, _square_screen(square))
	_out["square_luma"] = luma
	_out["shot_start"] = _shot(image, "start")
	_save()

	# A line played with the mouse: ordinary moves, en passant, both castles.
	var clicks := []
	_out["clicks"] = clicks
	for pair in _probe.get("click_line", []):
		var clicked := await _click_move(pair[0], pair[1])
		clicks.append({"move": pair, "clicked": clicked, "state": _state()})
		_save()
	_out["shot_line"] = _shot(await _image(), "after_line")

	# An illegal pair changes nothing.
	var bad: Array = _probe.get("illegal_click", [])
	if bad.size() == 2:
		var before: Variant = _text(_call("fen"))
		var clicked := await _click_move(bad[0], bad[1])
		_out["illegal_click"] = {"clicked": clicked, "fen_before": before, "fen_after": _text(_call("fen"))}
		# Click empty space far from the board to drop any selection.
		await _click_at(Vector2(4.0, 4.0))

	# The board follows the engine: a position loaded through the API appears.
	var follow: Dictionary = _probe.get("follow", {})
	if follow.has("fen"):
		await _load_position(follow["fen"])
		_out["follow"] = _state()
		var promotion: Array = follow.get("promotion_click", [])
		if promotion.size() == 2:
			var clicked := await _click_move(promotion[0], promotion[1])
			_out["promotion"] = {"clicked": clicked, "state": _state()}
	_save()

	# The status a person reads.
	var labels := {}
	for item in _probe.get("status_positions", []):
		await _load_position(item["fen"])
		var texts := []
		for node in get_nodes_in_group("status_label"):
			if node.is_visible_in_tree() and "text" in node:
				texts.append(str(node.text))
		labels[item["kind"]] = texts
	_out["status_labels"] = labels
	_save()

	# The AI answers a move made with the mouse.
	var chess := _chess()
	if chess != null and "ai_side" in chess:
		await _load_position(_probe.get("start_fen", ""))
		chess.set("ai_side", "black")
		var opening: Array = _probe.get("ai_click", ["e2", "e4"])
		var clicked := await _click_move(opening[0], opening[1])
		var waited := 0
		var deadline := Time.get_ticks_msec() + int(_probe.get("ai_scene_wait_ms", 15000))
		while Time.get_ticks_msec() < deadline:
			var fen: Variant = _text(_call("fen"))
			if fen is String and fen.split(" ").size() > 1 and fen.split(" ")[1] == "w" and not fen.begins_with(_probe.get("start_fen", "").split(" ")[0]):
				break
			await _frames(5)
			waited += 5
		await _frames(45)
		_out["ai_scene"] = {"clicked": clicked, "state": _state()}
		chess.set("ai_side", "")
	_save()

	# Frame time over a short window, as an observation (ADR-0073): software
	# rendering on a CPU, comparable between runs, not a statement about a GPU.
	await _load_position(_probe.get("start_fen", ""))
	var times := []
	for _i in 40:
		var started := Time.get_ticks_usec()
		await process_frame
		times.append((Time.get_ticks_usec() - started) / 1000.0)
	_out["frame_ms"] = times
	_out["shot_final"] = _shot(await _image(), "final")
