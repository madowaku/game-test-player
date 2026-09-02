"""Run an interactive, one-action-at-a-time Godot playtest session.

The process is intentionally a thin control loop.  It never reads the game
project and never asks the adapter for hidden state.  An AI (or a human
operator) supplies one JSON command after inspecting the latest screenshot.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapters.godot.godot_adapter import GodotAdapter, GodotAdapterError  # noqa: E402
from scripts.session import SessionRecorder  # noqa: E402


PERSONAS = ("first_time", "impatient", "explorer")
ADAPTER_ACTIONS = {
    "press_key",
    "key_down",
    "key_up",
    "mouse_move",
    "mouse_click",
    "wait",
    "restart_game",
    "terminate_game",
}
RECORD_COMMANDS = {"insight", "finding", "reached_point", "reproduction_steps"}
def emit(event: str, **payload: Any) -> None:
    """Write one machine-readable event for an AI controller."""

    message = {"event": event, **payload}
    print(json.dumps(message, ensure_ascii=False, default=str), flush=True)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _command_parameters(command: Mapping[str, Any], action: str) -> dict[str, Any]:
    allowed = {
        "press_key": ("key", "hold_s"),
        "key_down": ("key",),
        "key_up": ("key",),
        "mouse_move": ("x", "y", "relative"),
        "mouse_click": ("x", "y", "button", "clicks", "interval_s", "relative"),
        "wait": ("seconds",),
        "restart_game": (),
        "terminate_game": (),
    }
    return {key: command[key] for key in allowed.get(action, ()) if key in command}


def dispatch_adapter_action(adapter: GodotAdapter, command: Mapping[str, Any]) -> Any:
    """Dispatch exactly one allowed adapter operation."""

    action = command.get("action")
    if not isinstance(action, str) or action not in ADAPTER_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(sorted(ADAPTER_ACTIONS))}")
    if action == "press_key":
        return adapter.press_key(command["key"], hold_s=float(command.get("hold_s", 0.05)))
    if action == "key_down":
        return adapter.key_down(command["key"])
    if action == "key_up":
        return adapter.key_up(command["key"])
    if action == "mouse_move":
        return adapter.mouse_move(
            int(command["x"]), int(command["y"]), relative=bool(command.get("relative", True))
        )
    if action == "mouse_click":
        return adapter.mouse_click(
            int(command["x"]),
            int(command["y"]),
            button=str(command.get("button", "left")),
            clicks=int(command.get("clicks", 1)),
            interval_s=float(command.get("interval_s", 0.08)),
            relative=bool(command.get("relative", True)),
        )
    if action == "wait":
        return adapter.wait(float(command.get("seconds", 0)))
    if action == "restart_game":
        return adapter.restart_game()
    return adapter.terminate_game()


def _memo_from_command(command: Mapping[str, Any], action: str) -> dict[str, Any]:
    return {
        "step": command.get("step"),
        "what_happening": command.get("what_happening", ""),
        "next_goal": command.get("next_goal", ""),
        "action": action,
        "reason": command.get("reason", ""),
        "confidence": command.get("confidence"),
        "hesitation": command.get("hesitation", False),
        "expected_result": command.get("expected_result", ""),
        "actual_result": command.get("actual_result", ""),
        "misunderstanding": command.get("misunderstanding", ""),
    }


def _capture(adapter: GodotAdapter, recorder: SessionRecorder, *, label: str) -> tuple[dict[str, Any] | None, str]:
    try:
        screenshot = adapter.capture_screenshot(step=recorder.action_count, label=label)
        if is_dataclass(screenshot):
            return asdict(screenshot), ""
        return dict(screenshot), ""
    except Exception as exc:  # A missing frame is evidence too; keep the session alive.
        return None, str(exc)


def _stdin_reader(target: queue.Queue[str | None]) -> None:
    try:
        for line in sys.stdin:
            target.put(line)
    finally:
        target.put(None)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True, help="Packaged .exe or Godot project directory")
    parser.add_argument("--godot", help="Godot executable used when --game is a project directory")
    parser.add_argument("--game-arg", action="append", default=[], help="Argument forwarded to the game")
    parser.add_argument("--window-title", help="Optional visible-window title substring")
    parser.add_argument("--persona", choices=PERSONAS, default="first_time")
    parser.add_argument("--scenario", default="short-game")
    parser.add_argument("--out", default="game-test-evidence")
    parser.add_argument("--startup-wait", type=float, default=1.0)
    parser.add_argument("--window-timeout", type=float, default=15.0)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the game process running after the runner exits (normally avoid this)",
    )
    return parser


def _save_reports(recorder: SessionRecorder) -> tuple[Path, Path]:
    return recorder.save_json(), recorder.save_markdown()


def _handle_record_command(recorder: SessionRecorder, command: Mapping[str, Any]) -> None:
    record = command.get("record")
    if record not in RECORD_COMMANDS:
        raise ValueError(f"record must be one of {', '.join(sorted(RECORD_COMMANDS))}")
    if record == "insight":
        recorder.add_insight(
            str(command["bucket"]),
            str(command["text"]),
            step=int(command["step"]) if command.get("step") is not None else None,
            evidence=[str(item) for item in _as_list(command.get("evidence"))],
            severity=str(command.get("severity", "OBSERVATION")),
        )
    elif record == "finding":
        recorder.add_finding(
            title=str(command["title"]),
            description=str(command["description"]),
            severity=str(command.get("severity", "OBSERVATION")),
            reproduction=[str(item) for item in _as_list(command.get("reproduction"))],
            impact=str(command.get("impact", "")),
            evidence=[str(item) for item in _as_list(command.get("evidence"))],
            category=str(command.get("category", "UX")),
            step=int(command["step"]) if command.get("step") is not None else None,
        )
    elif record == "reached_point":
        recorder.set_reached_point(str(command.get("value", "")))
    else:
        recorder.set_reproduction_steps(str(item) for item in _as_list(command.get("steps")))


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).expanduser().resolve()
    recorder = SessionRecorder(
        persona=args.persona,
        scenario=args.scenario,
        evidence_dir=out_dir,
        adapter="godot",
        black_box=True,
    )
    adapter: GodotAdapter | None = None
    finished_reason = ""
    return_code = 0
    try:
        adapter = GodotAdapter(
            args.game,
            godot_binary=args.godot,
            game_args=args.game_arg,
            window_title=args.window_title,
            evidence_dir=out_dir,
            startup_wait=args.startup_wait,
            window_timeout=args.window_timeout,
        )
        launch_info = adapter.launch_game()
        emit("ready", black_box=True, persona=args.persona, scenario=args.scenario, launch=launch_info)
        screenshot, capture_error = _capture(adapter, recorder, label="initial")
        recorder.record_observation(screenshot, step=0, state_summary="")
        emit("observation", step=0, screenshot=screenshot, capture_error=capture_error)
        _save_reports(recorder)

        incoming: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_stdin_reader, args=(incoming,), daemon=True)
        reader.start()

        while not recorder.finished:
            elapsed = recorder.summary()["play_time_s"]
            if elapsed >= args.max_seconds:
                finished_reason = f"session limit of {args.max_seconds:g} seconds reached"
                recorder.finish("timeout", reason=finished_reason)
                emit("finished", outcome="timeout", reason=finished_reason)
                break
            if recorder.action_count >= args.max_steps:
                finished_reason = f"session limit of {args.max_steps} actions reached"
                recorder.finish("timeout", reason=finished_reason)
                emit("finished", outcome="timeout", reason=finished_reason)
                break
            remaining = max(0.01, float(args.max_seconds) - float(elapsed))
            try:
                raw_line = incoming.get(timeout=remaining)
            except queue.Empty:
                finished_reason = f"session limit of {args.max_seconds:g} seconds reached while waiting for a decision"
                recorder.finish("timeout", reason=finished_reason)
                emit("finished", outcome="timeout", reason=finished_reason)
                break
            if raw_line is None:
                finished_reason = "controller input closed before a finish command"
                recorder.finish("aborted", reason=finished_reason)
                emit("finished", outcome="aborted", reason=finished_reason)
                break
            line = raw_line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError as exc:
                emit("command_error", error=f"invalid JSON: {exc}")
                continue
            if not isinstance(command, dict):
                emit("command_error", error="each line must be a JSON object")
                continue

            if command.get("record"):
                try:
                    _handle_record_command(recorder, command)
                    _save_reports(recorder)
                    emit("recorded", record=command.get("record"), summary=recorder.summary())
                except Exception as exc:
                    emit("command_error", error=str(exc))
                continue

            if command.get("previous_actual_result") is not None or command.get("previous_misunderstanding") is not None:
                recorder.update_last_memo(
                    actual_result=command.get("previous_actual_result"),
                    misunderstanding=command.get("previous_misunderstanding"),
                )

            if command.get("action") == "finish":
                try:
                    outcome = str(command.get("outcome", command.get("status", "aborted"))).lower()
                    recorder.finish(
                        outcome,
                        reached_point=command.get("reached_point"),
                        reason=str(command.get("reason", "")),
                    )
                    emit("finished", outcome=outcome, reason=command.get("reason", ""))
                except Exception as exc:
                    emit("command_error", error=str(exc))
                continue

            action = command.get("action")
            if not isinstance(action, str) or action not in ADAPTER_ACTIONS:
                emit(
                    "command_error",
                    error=f"action must be one of {', '.join(sorted(ADAPTER_ACTIONS))}, or use action=finish",
                )
                continue

            action_step = recorder.action_count + 1
            started = time.monotonic()
            result_text = "ok"
            error_text = ""
            try:
                result = dispatch_adapter_action(adapter, command)
                if result is not None:
                    result_text = json.dumps(result, ensure_ascii=False, default=str)
            except Exception as exc:
                error_text = str(exc)
                result_text = "error"
            duration = time.monotonic() - started
            recorder.record_action(
                action,
                _command_parameters(command, action),
                duration_s=duration,
                result=result_text,
                error=error_text,
            )
            memo = _memo_from_command(command, action)
            memo["step"] = action_step
            recorder.record_memo(**memo)

            try:
                settle_s = float(command.get("settle_s", 0.25))
            except (TypeError, ValueError):
                settle_s = 0.0
                error_text = error_text or "settle_s must be a number"
            if not math.isfinite(settle_s) or settle_s < 0:
                error_text = error_text or "settle_s must be finite and non-negative"
                settle_s = 0.0
            if not error_text and settle_s > 0 and action != "wait":
                try:
                    adapter.wait(settle_s)
                except Exception as exc:
                    error_text = str(exc)
            screenshot, capture_error = _capture(adapter, recorder, label="observation")
            recorder.record_observation(
                screenshot,
                step=action_step,
                state_summary=str(command.get("state_summary", "")),
                player_facing_audio=str(command.get("player_facing_audio", "")),
            )
            _save_reports(recorder)
            emit(
                "observation",
                step=action_step,
                action=action,
                operation_error=error_text,
                screenshot=screenshot,
                capture_error=capture_error,
                game_running=adapter.is_running,
            )
            if action == "terminate_game":
                finished_reason = "terminate_game was selected"
                recorder.finish("aborted", reason=finished_reason)
                emit("finished", outcome="aborted", reason=finished_reason)

    except (GodotAdapterError, OSError, ValueError) as exc:
        finished_reason = str(exc)
        if not recorder.finished:
            recorder.finish("aborted", reason=finished_reason)
        emit("error", error=finished_reason)
        return_code = 1
    finally:
        if adapter is not None and not args.keep_open:
            adapter.terminate_game()
        if not recorder.finished:
            recorder.finish("aborted", reason=finished_reason or "runner exited before finish")
        json_path, markdown_path = _save_reports(recorder)
        emit("reports", json=str(json_path), markdown=str(markdown_path), summary=recorder.summary())
    return return_code


def main() -> int:
    args = _build_parser().parse_args()
    if args.max_steps < 1:
        raise SystemExit("--max-steps must be at least 1")
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        raise SystemExit("--max-seconds must be greater than 0")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
