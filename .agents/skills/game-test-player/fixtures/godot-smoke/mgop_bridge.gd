extends Node
## MGOP v1 runtime bridge for Godot 4.x.
##
## Install as an Autoload named `MGOPBridge` in development/test builds only.
## The bridge is disabled unless MADOWAKU_MGOP=1 is present in the environment.
## It serves newline-delimited JSON over localhost TCP and never injects gameplay input.

const MGOP_VERSION := "1.0"
const DEFAULT_HOST := "127.0.0.1"
const DEFAULT_PORT := 49561
const MAX_REQUEST_BYTES := 64 * 1024

var _server := TCPServer.new()
var _clients: Array[StreamPeerTCP] = []
var _buffers: Dictionary = {}
var _enabled := false
var _port := DEFAULT_PORT
var _started_at_msec := 0
var _active_scenario := ""

var _state_provider: Callable
var _metrics_provider: Callable
var _errors_provider: Callable
var _scenario_loader: Callable
var _reset_handler: Callable


func _ready() -> void:
    _enabled = OS.get_environment("MADOWAKU_MGOP") == "1"
    if not _enabled:
        return

    var requested_port := OS.get_environment("MADOWAKU_MGOP_PORT")
    if requested_port.is_valid_int():
        _port = int(requested_port)

    _started_at_msec = Time.get_ticks_msec()
    var error := _server.listen(_port, DEFAULT_HOST)
    if error != OK:
        push_error("MGOP bridge failed to listen on %s:%d (error %s)" % [DEFAULT_HOST, _port, error])
        _enabled = false
        return

    print("MGOP bridge listening on %s:%d" % [DEFAULT_HOST, _port])


func _process(_delta: float) -> void:
    if not _enabled:
        return
    _accept_pending_clients()
    _poll_clients()


func register_state_provider(provider: Callable) -> void:
    _state_provider = provider


func register_metrics_provider(provider: Callable) -> void:
    _metrics_provider = provider


func register_errors_provider(provider: Callable) -> void:
    _errors_provider = provider


func register_scenario_loader(loader: Callable) -> void:
    _scenario_loader = loader


func register_reset_handler(handler: Callable) -> void:
    _reset_handler = handler


func is_enabled() -> bool:
    return _enabled


func port() -> int:
    return _port


func _accept_pending_clients() -> void:
    while _server.is_connection_available():
        var peer := _server.take_connection()
        if peer == null:
            return
        peer.set_no_delay(true)
        _clients.append(peer)
        _buffers[peer.get_instance_id()] = PackedByteArray()


func _poll_clients() -> void:
    for index in range(_clients.size() - 1, -1, -1):
        var peer := _clients[index]
        peer.poll()
        var status := peer.get_status()
        if status == StreamPeerTCP.STATUS_NONE or status == StreamPeerTCP.STATUS_ERROR:
            _buffers.erase(peer.get_instance_id())
            _clients.remove_at(index)
            continue

        var available := peer.get_available_bytes()
        if available <= 0:
            continue

        var result := peer.get_data(available)
        if result[0] != OK:
            _send_error(peer, null, "read_failed", "Unable to read MGOP request")
            continue

        var buffer: PackedByteArray = _buffers.get(peer.get_instance_id(), PackedByteArray())
        buffer.append_array(result[1])
        if buffer.size() > MAX_REQUEST_BYTES:
            _send_error(peer, null, "request_too_large", "MGOP request exceeded byte limit")
            peer.disconnect_from_host()
            continue

        _buffers[peer.get_instance_id()] = _consume_lines(peer, buffer)


func _consume_lines(peer: StreamPeerTCP, buffer: PackedByteArray) -> PackedByteArray:
    while true:
        var newline := buffer.find(10)
        if newline < 0:
            return buffer

        var line_bytes := buffer.slice(0, newline)
        buffer = buffer.slice(newline + 1)
        var line := line_bytes.get_string_from_utf8().strip_edges()
        if line.is_empty():
            continue
        _handle_request(peer, line)

    return buffer


func _handle_request(peer: StreamPeerTCP, line: String) -> void:
    var parsed = JSON.parse_string(line)
    if not parsed is Dictionary:
        _send_error(peer, null, "invalid_json", "Request must be a JSON object")
        return

    var request: Dictionary = parsed
    var request_id = request.get("id")
    var op := str(request.get("op", ""))
    var args = request.get("args", {})
    if not args is Dictionary:
        _send_error(peer, request_id, "invalid_args", "args must be a JSON object")
        return

    match op:
        "hello":
            _send_ok(peer, request_id, {
                "mgop_version": MGOP_VERSION,
                "engine": "godot",
                "engine_version": Engine.get_version_info(),
                "capabilities": _capabilities(),
            })
        "get_state":
            _send_ok(peer, request_id, _get_state())
        "get_metrics":
            _send_ok(peer, request_id, _get_metrics())
        "get_errors":
            _send_ok(peer, request_id, _get_errors())
        "load_scenario":
            _handle_load_scenario(peer, request_id, args)
        "reset":
            _handle_reset(peer, request_id)
        "pause":
            get_tree().paused = true
            _send_ok(peer, request_id, {"paused": true})
        "resume":
            get_tree().paused = false
            _send_ok(peer, request_id, {"paused": false})
        _:
            _send_error(peer, request_id, "unsupported_operation", "Unsupported MGOP operation: %s" % op)


func _get_state() -> Dictionary:
    var tree := get_tree()
    var current_scene := tree.current_scene
    var custom := _call_dictionary(_state_provider)

    var base := {
        "mgop_version": MGOP_VERSION,
        "observed_at": Time.get_datetime_string_from_system(true, true),
        "scenario": {
            "id": _active_scenario,
            "fixture": not _active_scenario.is_empty(),
        },
        "game": {
            "scene": current_scene.scene_file_path if current_scene != null else "",
            "state": "playing" if not tree.paused else "paused",
            "paused": tree.paused,
            "time_seconds": maxf(0.0, float(Time.get_ticks_msec() - _started_at_msec) / 1000.0),
        },
        "player": {},
        "camera": {},
        "world": {},
        "objective": {},
        "input": {},
        "performance": _get_metrics(),
        "errors": _get_errors(),
        "extensions": {},
    }
    return _deep_merge(base, custom)


func _get_metrics() -> Dictionary:
    var base := {
        "fps": Engine.get_frames_per_second(),
        "frame_time_ms": 0.0 if Engine.get_frames_per_second() <= 0 else 1000.0 / Engine.get_frames_per_second(),
        "memory_mb": null,
        "draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
        "triangles": Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME),
        "active_entities": Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
        "physics_objects": Performance.get_monitor(Performance.PHYSICS_3D_ACTIVE_OBJECTS),
        "scene_load_ms": null,
        "extensions": {},
    }
    return _deep_merge(base, _call_dictionary(_metrics_provider))


func _get_errors() -> Dictionary:
    var custom := _call_dictionary(_errors_provider)
    if custom.is_empty():
        return {"count": 0, "recent": []}
    if not custom.has("count"):
        custom["count"] = (custom.get("recent", []) as Array).size()
    if not custom.has("recent"):
        custom["recent"] = []
    return custom


func _handle_load_scenario(peer: StreamPeerTCP, request_id, args: Dictionary) -> void:
    var scenario_id := str(args.get("id", "")).strip_edges()
    if scenario_id.is_empty():
        _send_error(peer, request_id, "invalid_scenario", "Scenario id is required")
        return
    if not _scenario_loader.is_valid():
        _send_error(peer, request_id, "unsupported_operation", "No scenario loader registered")
        return

    var result = _scenario_loader.call(scenario_id)
    var accepted := false
    var details: Dictionary = {}
    if result is bool:
        accepted = result
    elif result is Dictionary:
        details = result
        accepted = bool(details.get("accepted", false))

    if not accepted:
        _send_error(peer, request_id, "scenario_rejected", "Scenario was not accepted", details)
        return

    _active_scenario = scenario_id
    details["accepted"] = true
    details["id"] = scenario_id
    _send_ok(peer, request_id, details)


func _handle_reset(peer: StreamPeerTCP, request_id) -> void:
    if _reset_handler.is_valid():
        var result = _reset_handler.call()
        if result is bool and not result:
            _send_error(peer, request_id, "reset_failed", "Registered reset handler rejected reset")
            return
        _send_ok(peer, request_id, {"reset": true})
        return

    if get_tree().current_scene == null:
        _send_error(peer, request_id, "reset_failed", "No current scene to reload")
        return

    var error := get_tree().reload_current_scene()
    if error != OK:
        _send_error(peer, request_id, "reset_failed", "reload_current_scene failed", {"error": error})
        return
    _send_ok(peer, request_id, {"reset": true})


func _capabilities() -> Dictionary:
    return {
        "get_state": true,
        "get_metrics": true,
        "get_errors": true,
        "load_scenario": _scenario_loader.is_valid(),
        "reset": true,
        "pause": true,
        "resume": true,
        "capture": false,
    }


func _call_dictionary(callable: Callable) -> Dictionary:
    if not callable.is_valid():
        return {}
    var value = callable.call()
    if value is Dictionary:
        return value
    return {}


func _deep_merge(base: Dictionary, overlay: Dictionary) -> Dictionary:
    var result := base.duplicate(true)
    for key in overlay:
        if result.has(key) and result[key] is Dictionary and overlay[key] is Dictionary:
            result[key] = _deep_merge(result[key], overlay[key])
        else:
            result[key] = overlay[key]
    return result


func _send_ok(peer: StreamPeerTCP, request_id, result) -> void:
    _send_json(peer, {
        "id": request_id,
        "ok": true,
        "result": result,
    })


func _send_error(peer: StreamPeerTCP, request_id, code: String, message: String, details: Dictionary = {}) -> void:
    _send_json(peer, {
        "id": request_id,
        "ok": false,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    })


func _send_json(peer: StreamPeerTCP, value: Dictionary) -> void:
    var payload := JSON.stringify(value) + "\n"
    peer.put_data(payload.to_utf8_buffer())
