"""Dependency-free self-check for the MGOP client, runner mode, and evidence bundle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.evidence_bundle import EvidenceBundle  # noqa: E402
from scripts.mgop_client import MGOPClient  # noqa: E402
from scripts.run_session import (  # noqa: E402
    _build_parser,
    _enable_mgop_environment,
    _resolve_mode,
    _restore_environment,
)
from scripts.session import SessionRecorder  # noqa: E402


def _serve(listener: socket.socket, stop: threading.Event) -> None:
    listener.settimeout(0.1)
    while not stop.is_set():
        try:
            conn, _address = listener.accept()
        except socket.timeout:
            continue
        with conn:
            conn.settimeout(1.0)
            raw = b""
            while b"\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            if not raw:
                continue
            request = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
            op = request["op"]
            if op == "hello":
                result = {"mgop_version": "1.0", "engine": "self-check"}
            elif op == "get_state":
                result = {
                    "mgop_version": "1.0",
                    "game": {"scene": "synthetic", "state": "playing", "paused": False},
                    "player": {"health": 100, "alive": True},
                    "extensions": {},
                }
            elif op == "get_metrics":
                result = {"fps": 60.0, "frame_time_ms": 16.67, "extensions": {}}
            elif op == "get_errors":
                result = {"count": 0, "recent": []}
            else:
                response = {
                    "id": request["id"],
                    "ok": False,
                    "error": {"code": "unsupported", "message": op},
                }
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                continue
            response = {"id": request["id"], "ok": True, "result": result}
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="game-test-player-mgop-self-check-"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    stop = threading.Event()
    server = threading.Thread(target=_serve, args=(listener, stop), daemon=True)
    server.start()
    previous_env = None
    try:
        parser = _build_parser()
        explicit = parser.parse_args(["--game", "synthetic", "--mode", "instrumented"])
        assert _resolve_mode(explicit) == "instrumented"
        shortcut = parser.parse_args(["--game", "synthetic", "--mgop"])
        assert _resolve_mode(shortcut) == "instrumented"
        conflict = parser.parse_args(["--game", "synthetic", "--mode", "black_box", "--mgop"])
        try:
            _resolve_mode(conflict)
        except ValueError:
            pass
        else:
            raise AssertionError("--mgop must conflict with --mode black_box")

        old_enabled = os.environ.get("MADOWAKU_MGOP")
        old_port = os.environ.get("MADOWAKU_MGOP_PORT")
        previous_env = _enable_mgop_environment(port)
        assert os.environ["MADOWAKU_MGOP"] == "1"
        assert os.environ["MADOWAKU_MGOP_PORT"] == str(port)
        _restore_environment(previous_env)
        previous_env = None
        assert os.environ.get("MADOWAKU_MGOP") == old_enabled
        assert os.environ.get("MADOWAKU_MGOP_PORT") == old_port

        client = MGOPClient(host=host, port=port, timeout_s=1.0)
        hello = client.hello()
        state = client.get_state()
        metrics = client.get_metrics()
        errors = client.get_errors()
        assert hello["mgop_version"] == "1.0"
        assert state["game"]["scene"] == "synthetic"
        assert metrics["fps"] == 60.0
        assert errors["count"] == 0

        recorder = SessionRecorder(
            persona="explorer",
            scenario="synthetic",
            evidence_dir=workspace,
            adapter="godot",
            black_box=False,
        )
        recorder.data["mode"] = "instrumented"
        recorder.data["instrumentation"] = {
            "enabled": True,
            "protocol": "MGOP",
            "protocol_version": "1.0",
            "status": "ready",
            "hello": hello,
        }
        bundle = EvidenceBundle(workspace)
        observation = bundle.record_instrumented_observation(
            recorder,
            None,
            state=state,
            metrics=metrics,
            errors=errors,
            step=0,
            state_summary="synthetic self-check",
        )
        recorder.finish("success", reached_point="synthetic", reason="MGOP self-check")
        manifest_path = bundle.finalize(recorder)
        session_path = recorder.save_json()

        assert manifest_path.exists()
        assert Path(observation["mgop"]["state_path"]).exists()
        assert Path(observation["mgop"]["metrics_path"]).exists()
        assert Path(observation["mgop"]["errors_path"]).exists()
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        assert payload["mode"] == "instrumented"
        assert payload["black_box"] is False
        assert payload["instrumentation"]["hello"]["mgop_version"] == "1.0"
        assert payload["evidence_bundle"]["entry_count"] == 1
        assert payload["observations"][0]["mgop"]["state_path"]

        print(
            json.dumps(
                {
                    "ok": True,
                    "mgop": hello,
                    "session": str(session_path),
                    "bundle": str(manifest_path),
                }
            )
        )
        return 0
    finally:
        if previous_env is not None:
            _restore_environment(previous_env)
        stop.set()
        listener.close()
        server.join(timeout=1.0)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
