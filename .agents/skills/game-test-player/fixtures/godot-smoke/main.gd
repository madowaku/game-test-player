extends Control
## Tiny Godot fixture used to prove the runner + MGOP + Evidence Bundle path.
##
## SPACE toggles idle <-> activated through ordinary player input.
## R resets to idle. MGOP observes those changes through registered providers.

var phase := "idle"
var action_count := 0
var last_action := ""

@onready var status_panel: ColorRect = $StatusPanel
@onready var status_label: Label = $StatusPanel/Status
@onready var details_label: Label = $Details


func _ready() -> void:
    MGOPBridge.register_state_provider(Callable(self, "_mgop_state"))
    MGOPBridge.register_metrics_provider(Callable(self, "_mgop_metrics"))
    MGOPBridge.register_errors_provider(Callable(self, "_mgop_errors"))
    MGOPBridge.register_scenario_loader(Callable(self, "_mgop_load_scenario"))
    MGOPBridge.register_reset_handler(Callable(self, "_mgop_reset"))
    _render()


func _input(event: InputEvent) -> void:
    if not event is InputEventKey:
        return
    var key_event := event as InputEventKey
    if not key_event.pressed or key_event.echo:
        return

    if key_event.keycode == KEY_SPACE or key_event.physical_keycode == KEY_SPACE:
        action_count += 1
        phase = "activated" if phase == "idle" else "idle"
        last_action = "space"
        _render()
        get_viewport().set_input_as_handled()
    elif key_event.keycode == KEY_R or key_event.physical_keycode == KEY_R:
        action_count += 1
        _reset_fixture("r")
        get_viewport().set_input_as_handled()


func _render() -> void:
    if phase == "activated":
        status_panel.color = Color(0.16, 0.52, 0.31, 1.0)
        status_label.text = "ACTIVATED"
    else:
        status_panel.color = Color(0.24, 0.29, 0.39, 1.0)
        status_label.text = "IDLE"

    details_label.text = "SPACE toggles state  •  R resets\nphase=%s  action_count=%d  last_action=%s" % [
        phase,
        action_count,
        last_action if not last_action.is_empty() else "none",
    ]


func _reset_fixture(action_name: String) -> void:
    phase = "idle"
    last_action = action_name
    _render()


func _mgop_state() -> Dictionary:
    return {
        "player": {
            "state": phase,
            "health": 100,
            "alive": true,
        },
        "world": {
            "enemy_count": 0,
            "active_entities": 1,
        },
        "objective": {
            "id": "toggle-smoke-state",
            "state": "complete" if phase == "activated" else "pending",
        },
        "input": {
            "last_action": last_action,
        },
        "extensions": {
            "smoke_fixture": {
                "phase": phase,
                "action_count": action_count,
            },
        },
    }


func _mgop_metrics() -> Dictionary:
    return {
        "extensions": {
            "smoke_fixture": {
                "phase": phase,
                "action_count": action_count,
            },
        },
    }


func _mgop_errors() -> Dictionary:
    return {
        "count": 0,
        "recent": [],
    }


func _mgop_load_scenario(scenario_id: String) -> Dictionary:
    match scenario_id:
        "smoke-idle":
            phase = "idle"
        "smoke-active":
            phase = "activated"
        _:
            return {
                "accepted": false,
                "reason": "unknown smoke scenario",
            }

    action_count = 0
    last_action = "load_scenario:%s" % scenario_id
    _render()
    return {
        "accepted": true,
        "phase": phase,
    }


func _mgop_reset() -> bool:
    action_count = 0
    _reset_fixture("mgop-reset")
    return true
