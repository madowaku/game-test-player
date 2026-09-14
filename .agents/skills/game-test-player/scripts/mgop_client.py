"""Minimal dependency-free MGOP v1 JSONL client."""

from __future__ import annotations

import json
import socket
from typing import Any, Mapping


class MGOPError(RuntimeError):
    pass


class MGOPClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 49561, timeout_s: float = 2.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self._next_id = 1

    def request(self, op: str, args: Mapping[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "id": request_id,
            "op": str(op),
            "args": dict(args or {}),
        }
        raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendall(raw)
            response = self._read_line(sock)
        try:
            decoded = json.loads(response)
        except json.JSONDecodeError as exc:
            raise MGOPError("MGOP bridge returned invalid JSON") from exc
        if decoded.get("id") != request_id:
            raise MGOPError("MGOP response id did not match request")
        if not decoded.get("ok"):
            error = decoded.get("error") or {}
            code = error.get("code", "mgop_error")
            message = error.get("message", "MGOP request failed")
            raise MGOPError(f"{code}: {message}")
        return decoded.get("result")

    def hello(self) -> dict[str, Any]:
        return dict(self.request("hello") or {})

    def get_state(self) -> dict[str, Any]:
        return dict(self.request("get_state") or {})

    def get_metrics(self) -> dict[str, Any]:
        return dict(self.request("get_metrics") or {})

    def get_errors(self) -> dict[str, Any]:
        return dict(self.request("get_errors") or {})

    def load_scenario(self, scenario_id: str) -> dict[str, Any]:
        return dict(self.request("load_scenario", {"id": scenario_id}) or {})

    def reset(self) -> dict[str, Any]:
        return dict(self.request("reset") or {})

    def pause(self) -> dict[str, Any]:
        return dict(self.request("pause") or {})

    def resume(self) -> dict[str, Any]:
        return dict(self.request("resume") or {})

    def _read_line(self, sock: socket.socket) -> str:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            newline = chunk.find(b"\n")
            if newline >= 0:
                chunks.append(chunk[:newline])
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 1024 * 1024:
                raise MGOPError("MGOP response exceeded 1 MiB")
        if not chunks:
            raise MGOPError("MGOP bridge closed without a response")
        return b"".join(chunks).decode("utf-8")


__all__ = ["MGOPClient", "MGOPError"]
