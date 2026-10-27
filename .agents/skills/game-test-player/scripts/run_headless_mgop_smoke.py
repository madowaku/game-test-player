"""Validate the real Godot MGOP bridge in headless diagnostic mode.

This checks the engine and protocol, not OS screenshots or ordinary player input.
The separate Windows run_godot_smoke.py remains the full end-to-end gate.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.mgop_client import MGOPClient, MGOPError  # noqa: E402

FIXTURE = SKILL_ROOT / "fixtures" / "godot-smoke"
BRIDGE = SKILL_ROOT / "adapters" / "godot" / "runtime" / "mgop_bridge.gd"
FIXTURE_BRIDGE = FIXTURE / "mgop_bridge.gd"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def verify(client: MGOPClient) -> None:
    hello = client.hello()
    require(hello.get("mgop_version") == "1.0", f"Wrong protocol version: {hello}")
    require(hello.get("engine") == "godot", f"Wrong engine: {hello}")
    capabilities = hello.get("capabilities") or {}
    for name in ("get_state", "get_metrics", "get_errors", "load_scenario", "pause", "resume"):
        require(capabilities.get(name) is True, f"Missing capability: {name}")

    initial = client.get_state()
    require(initial.get("player", {}).get("state") == "idle", f"Initial state: {initial}")
    require(initial.get("objective", {}).get("state") == "pending", "Initial objective not pending")
    require(client.get_errors().get("count") == 0, "Fixture reports errors")
    require(isinstance(client.get_metrics().get("fps"), (int, float)), "No FPS metric")

    loaded = client.load_scenario("smoke-active")
    require(loaded.get("accepted") is True, "Scenario rejected")
    active = client.get_state()
    require(active.get("player", {}).get("state") == "activated", "Scenario did not activate")
    require(active.get("scenario", {}).get("id") == "smoke-active", "Scenario ID missing")
    require(active.get("objective", {}).get("state") == "complete", "Objective incomplete")

    require(client.pause().get("paused") is True, "Pause not acknowledged")
    require(client.get_state().get("game", {}).get("paused") is True, "Paused state not observable")
    require(client.resume().get("paused") is False, "Resume not acknowledged")
    require(client.get_state().get("game", {}).get("paused") is False, "Game remained paused")

    client.reset()
    reset = client.get_state()
    require(reset.get("player", {}).get("state") == "idle", "Reset did not restore idle")
    require(reset.get("extensions", {}).get("smoke_fixture", {}).get("action_count") == 0,
            "Reset did not clear action count")
    try:
        client.load_scenario("unknown-scenario")
    except MGOPError:
        pass
    else:
        raise AssertionError("Unknown scenario unexpectedly succeeded")


def run(godot: str, timeout: float) -> int:
    require(BRIDGE.read_bytes() == FIXTURE_BRIDGE.read_bytes(), "Fixture bridge drift")
    require(timeout > 0, "Timeout must be positive")
    port = free_port()
    env = os.environ.copy()
    env.update(MADOWAKU_MGOP="1", MADOWAKU_MGOP_PORT=str(port))
    proc = subprocess.Popen([godot, "--headless", "--path", str(FIXTURE)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    failure = None
    try:
        deadline = time.monotonic() + timeout
        client = MGOPClient(port=port, timeout_s=1.0)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Godot exited before handshake: {proc.returncode}")
            try:
                client.hello()
                break
            except (MGOPError, ConnectionError, OSError, TimeoutError):
                time.sleep(0.2)
        else:
            raise TimeoutError("Godot MGOP handshake timed out")
        verify(client)
        print("PASS: real Godot headless MGOP handshake, state, scenario, pause/resume, reset, errors")
        print("NOTE: Screenshots and ordinary player input are NOT tested; Windows smoke still required")
        return 0
    except Exception as exc:
        failure = exc
        return 1
    finally:
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
        if failure is not None:
            print(f"FAIL: {failure}", file=sys.stderr)
            print(stdout[-6000:], file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", default=os.getenv("GODOT_BIN") or shutil.which("godot") or shutil.which("godot4"),
                        help="Godot 4 binary (or use GODOT_BIN/PATH)")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if not args.godot:
        parser.error("Godot 4 not found; pass --godot or set GODOT_BIN")
    return run(args.godot, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
