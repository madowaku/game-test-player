"""MGOP evidence bundle helpers for instrumented game-test-player sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.session import SessionRecorder, utc_now


class EvidenceBundle:
    """Write MGOP state/metrics/errors beside existing screenshot evidence."""

    def __init__(self, root: str | Path, *, protocol_version: str = "1.0") -> None:
        self.root = Path(root).expanduser().resolve()
        self.protocol_version = protocol_version
        self.state_dir = self.root / "state"
        self.metrics_dir = self.root / "metrics"
        self.errors_dir = self.root / "errors"
        for directory in (self.root, self.state_dir, self.metrics_dir, self.errors_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, Any]] = []

    def record_instrumented_observation(
        self,
        recorder: SessionRecorder,
        screenshot: str | Path | Mapping[str, Any] | None,
        *,
        state: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        errors: Mapping[str, Any] | None = None,
        state_summary: str = "",
        player_facing_audio: str = "",
        step: int | None = None,
    ) -> dict[str, Any]:
        observation = recorder.record_observation(
            screenshot,
            state_summary=state_summary,
            player_facing_audio=player_facing_audio,
            step=step,
        )
        resolved_step = int(observation["step"])
        mgop: dict[str, str] = {}
        if state is not None:
            mgop["state_path"] = str(self._write_json(self.state_dir, "state", resolved_step, state))
        if metrics is not None:
            mgop["metrics_path"] = str(self._write_json(self.metrics_dir, "metrics", resolved_step, metrics))
        if errors is not None:
            mgop["errors_path"] = str(self._write_json(self.errors_dir, "errors", resolved_step, errors))
        observation["mgop"] = mgop
        self._entries.append({
            "step": resolved_step,
            "observed_at": observation["observed_at"],
            **mgop,
        })
        return observation

    def finalize(self, recorder: SessionRecorder) -> Path:
        """Attach bundle metadata to the session and write bundle.json."""

        manifest = {
            "schema_version": "1.0",
            "protocol": "MGOP",
            "protocol_version": self.protocol_version,
            "created_at": utc_now(),
            "root": str(self.root),
            "entries": list(self._entries),
        }
        manifest_path = self.root / "bundle.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        recorder.data["instrumentation"] = {
            "enabled": True,
            "protocol": "MGOP",
            "protocol_version": self.protocol_version,
        }
        recorder.data["evidence_bundle"] = {
            "manifest": str(manifest_path),
            "state_dir": str(self.state_dir),
            "metrics_dir": str(self.metrics_dir),
            "errors_dir": str(self.errors_dir),
            "entry_count": len(self._entries),
        }
        return manifest_path

    def _write_json(
        self,
        directory: Path,
        prefix: str,
        step: int,
        payload: Mapping[str, Any],
    ) -> Path:
        path = directory / f"{prefix}-step-{step:04d}.json"
        path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return path


__all__ = ["EvidenceBundle"]
