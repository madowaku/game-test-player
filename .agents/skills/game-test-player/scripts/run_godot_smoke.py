"""Run the real Godot MGOP smoke fixture through run_session.py on Windows.

This is an end-to-end infrastructure check, not a first-time UX playtest. It
launches the committed Godot 4 fixture, waits for the instrumented runner,
sends one ordinary SPACE key action, and verifies aligned screenshot/state/
metrics/errors evidence before finishing successfully.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
FIXTURE_DIR = SKILL_ROOT / "fixtures" / "godot-smoke"
CANONICAL_BRIDGE = SKILL_ROOT / "adapters" / "godot" / "runtime" / "mgop_bridge.gd"
FIXTURE_BRIDGE = FIXTURE_DIR / "mgop_bridge.gd"
RUNNER = SKILL_ROOT / "scripts" / "run_session.py"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", help="Path to Godot 4 executable; otherwise GODOT_BIN/PATH is used")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "evidence" / "godot-mgop-smoke"),
        help="Evidence output directory; existing contents are replaced",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Overall runner session timeout")
    return parser


def _reader(stream, target: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            target.put(line)
    finally:
        target.put(None)


def _stderr_reader(stream, target: list[str]) -> None:
    for line in stream:
        target.append(line.rstrip())


def _next_event(
    events: queue.Queue[str | None],
    *,
    deadline: float,
    wanted: str | None = None,
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for runner event {wanted or '<any>'}")
        try:
            line = events.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError(f"timed out waiting for runner event {wanted or '<any>'}") from exc
        if line is None:
            raise RuntimeError("runner stdout closed before smoke validation completed")
        raw = line.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"runner emitted non-JSON stdout: {raw}") from exc
        if event.get("event") == "error":
            raise RuntimeError(f"runner error: {event.get('error', 'unknown error')}")
        if wanted is None or event.get("event") == wanted:
            return event


def _send(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("runner stdin is unavailable")
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _state_from_observation(event: dict[str, Any]) -> dict[str, Any]:
    instrumentation = event.get("instrumentation") or {}
    state = instrumentation.get("state")
    if not isinstance(state, dict):
        raise AssertionError(f"observation step {event.get('step')} has no MGOP state")
    return state


def _assert_path(path_value: Any, label: str) -> Path:
    if not path_value:
        raise AssertionError(f"missing {label} path")
    path = Path(str(path_value))
    if not path.is_file():
        raise AssertionError(f"{label} file does not exist: {path}")
    return path


def _assert_bridge_sync() -> None:
    canonical = CANONICAL_BRIDGE.read_text(encoding="utf-8")
    fixture = FIXTURE_BRIDGE.read_text(encoding="utf-8")
    if canonical != fixture:
        raise AssertionError(
            "fixture mgop_bridge.gd drifted from the canonical bridge; copy the canonical file before smoke testing"
        )


def _validate_initial(event: dict[str, Any]) -> None:
    if event.get("step") != 0:
        raise AssertionError(f"expected initial observation step 0, got {event.get('step')}")
    _assert_path((event.get("screenshot") or {}).get("path"), "initial screenshot")
    state = _state_from_observation(event)
    if state.get("player", {}).get("state") != "idle":
        raise AssertionError(f"expected initial player.state=idle, got {state.get('player')}")
    if state.get("objective", {}).get("state") != "pending":
        raise AssertionError(f"expected initial objective pending, got {state.get('objective')}")
    errors = (event.get("instrumentation") or {}).get("errors") or {}
    if errors.get("count") != 0:
        raise AssertionError(f"fixture reported unexpected initial errors: {errors}")


def _validate_activated(event: dict[str, Any]) -> None:
    if event.get("step") != 1:
        raise AssertionError(f"expected post-action observation step 1, got {event.get('step')}")
    _assert_path((event.get("screenshot") or {}).get("path"), "activated screenshot")
    state = _state_from_observation(event)
    if state.get("player", {}).get("state") != "activated":
        raise AssertionError(f"SPACE did not activate player state: {state.get('player')}")
    if state.get("objective", {}).get("state") != "complete":
        raise AssertionError(f"SPACE did not complete smoke objective: {state.get('objective')}")
    if state.get("input", {}).get("last_action") != "space":
        raise AssertionError(f"MGOP did not observe last_action=space: {state.get('input')}")
    smoke = state.get("extensions", {}).get("smoke_fixture", {})
    if smoke.get("action_count") != 1 or smoke.get("phase") != "activated":
        raise AssertionError(f"unexpected smoke extension state: {smoke}")

    paths = (event.get("instrumentation") or {}).get("paths") or {}
    _assert_path(paths.get("state_path"), "step 1 state")
    _assert_path(paths.get("metrics_path"), "step 1 metrics")
    _assert_path(paths.get("errors_path"), "step 1 errors")


def _validate_reports(event: dict[str, Any]) -> dict[str, Any]:
    session_path = _assert_path(event.get("json"), "session report")
    _assert_path(event.get("markdown"), "markdown report")
    bundle_path = _assert_path(event.get("bundle"), "bundle manifest")

    payload = json.loads(session_path.read_text(encoding="utf-8"))
    if payload.get("mode") != "instrumented" or payload.get("black_box") is not False:
        raise AssertionError("session did not remain explicitly instrumented")
    if payload.get("action_count") != 1 or payload.get("observation_count") != 2:
        raise AssertionError(
            f"expected 1 action and 2 observations, got {payload.get('action_count')} / {payload.get('observation_count')}"
        )
    if payload.get("outcome") != "success":
        raise AssertionError(f"smoke session outcome was not success: {payload.get('outcome')}")
    evidence_bundle = payload.get("evidence_bundle") or {}
    if evidence_bundle.get("entry_count") != 2:
        raise AssertionError(f"expected 2 evidence bundle entries, got {evidence_bundle}")

    observations = payload.get("observations") or []
    if len(observations) != 2:
        raise AssertionError("session observation list did not contain both smoke frames")
    for index, observation in enumerate(observations):
        mgop = observation.get("mgop") or {}
        _assert_path(mgop.get("state_path"), f"observation {index} state")
        _assert_path(mgop.get("metrics_path"), f"observation {index} metrics")
        _assert_path(mgop.get("errors_path"), f"observation {index} errors")

    manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    if len(manifest.get("entries") or []) != 2:
        raise AssertionError("bundle.json did not contain exactly two aligned observations")
    return payload


def run(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("Godot OS-window smoke fixture currently requires Windows")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than 0")

    _assert_bridge_sync()
    out_dir = Path(args.out).expanduser().resolve()
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(RUNNER),
        "--game",
        str(FIXTURE_DIR),
        "--mode",
        "instrumented",
        "--persona",
        "explorer",
        "--scenario",
        "mgop-smoke",
        "--window-title",
        "MGOP Smoke Fixture",
        "--out",
        str(out_dir),
        "--max-steps",
        "3",
        "--max-seconds",
        str(args.timeout),
        "--mgop-connect-timeout",
        "8",
    ]
    if args.godot:
        command.extend(["--godot", str(Path(args.godot).expanduser())])

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("failed to open runner pipes")

    events: queue.Queue[str | None] = queue.Queue()
    stderr_lines: list[str] = []
    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, events), daemon=True)
    stderr_thread = threading.Thread(target=_stderr_reader, args=(process.stderr, stderr_lines), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + args.timeout + 12.0
    try:
        ready = _next_event(events, deadline=deadline, wanted="ready")
        hello = ready.get("instrumentation") or {}
        if ready.get("mode") != "instrumented" or hello.get("mgop_version") != "1.0":
            raise AssertionError(f"instrumented MGOP handshake failed: {ready}")
        capabilities = hello.get("capabilities") or {}
        if not capabilities.get("get_state") or not capabilities.get("get_metrics"):
            raise AssertionError(f"required MGOP capabilities missing: {capabilities}")

        initial = _next_event(events, deadline=deadline, wanted="observation")
        _validate_initial(initial)

        _send(
            process,
            {
                "action": "press_key",
                "key": "space",
                "hold_s": 0.08,
                "settle_s": 0.35,
                "what_happening": "MGOP smoke fixture is visibly idle",
                "next_goal": "Toggle the fixture with ordinary player input",
                "reason": "SPACE is the fixture's documented player action",
                "confidence": 1.0,
                "expected_result": "Visible status and MGOP state both become activated",
            },
        )
        activated = _next_event(events, deadline=deadline, wanted="observation")
        _validate_activated(activated)

        _send(
            process,
            {
                "action": "finish",
                "outcome": "success",
                "reached_point": "MGOP smoke fixture activated",
                "reason": "Visible input and aligned MGOP evidence agreed at step 1",
            },
        )
        _next_event(events, deadline=deadline, wanted="finished")
        reports = _next_event(events, deadline=deadline, wanted="reports")
        payload = _validate_reports(reports)

        remaining = max(0.1, deadline - time.monotonic())
        return_code = process.wait(timeout=remaining)
        if return_code != 0:
            raise RuntimeError(f"runner exited with code {return_code}")

        print(
            json.dumps(
                {
                    "ok": True,
                    "fixture": str(FIXTURE_DIR),
                    "evidence": str(out_dir),
                    "actions": payload["action_count"],
                    "observations": payload["observation_count"],
                    "outcome": payload["outcome"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
        details = "\n".join(stderr_lines[-20:])
        if details:
            print(details, file=sys.stderr)
        print(f"Godot MGOP smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if process.stdin is not None:
            process.stdin.close()
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)


def main() -> int:
    return run(_build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
