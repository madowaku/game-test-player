"""Run dependency-free checks for the recorder and report contract."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapters.godot.godot_adapter import GodotAdapter, encode_bgra_png, normalize_key  # noqa: E402
from scripts.session import INSIGHT_BUCKETS, SessionRecorder, render_markdown  # noqa: E402


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="game-test-player-self-check-"))
    try:
        png_path = workspace / "step-0000-initial.png"
        # 2x1 BGRA pixels: red, then green.  This checks the same PNG encoder
        # used by the Windows capture backend without launching a game.
        png_path.write_bytes(encode_bgra_png(bytes((0, 0, 255, 0, 0, 255, 0, 0)), 2, 1))
        assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert normalize_key("space") == 0x20
        assert normalize_key("KEY_A") == 0x41

        project_dir = workspace / "project-target"
        project_dir.mkdir()
        project_adapter = GodotAdapter(
            project_dir,
            godot_binary=sys.executable,
            evidence_dir=workspace / "project-evidence",
        )
        assert project_adapter.command == [sys.executable, "--path", str(project_dir)]

        recorder = SessionRecorder(
            persona="first_time",
            scenario="short-game",
            evidence_dir=workspace,
            adapter="godot",
            black_box=True,
        )
        recorder.record_observation({"path": png_path, "width": 2, "height": 1}, step=0)
        for step in range(1, 21):
            recorder.record_action("wait", {"seconds": 0.0}, duration_s=0.0, result="ok")
            recorder.record_memo(
                step=step,
                what_happening="A visible game state is on screen",
                next_goal="Understand the next cue",
                action="wait",
                reason="No clear input cue yet",
                confidence=0.5,
                hesitation=step == 1,
                expected_result="The screen remains readable",
                actual_result="The screen remained readable",
            )
            recorder.record_observation({"path": png_path, "width": 2, "height": 1}, step=step)
        recorder.add_insight(
            "confusion_points",
            "The first prompt did not identify the next interaction.",
            step=1,
            evidence=[png_path],
            severity="MEDIUM",
        )
        recorder.add_insight("misread_ui", "A decorative panel looked interactive.", step=2)
        recorder.add_insight("misunderstood_rules", "The scoring rule was not inferable.", step=3)
        recorder.add_insight("stuck_candidates", "The player could not find a way forward.", step=4)
        recorder.add_insight("bug_candidates", "A button appeared to ignore a click.", step=5)
        recorder.add_insight("fun_moments", "A responsive animation was satisfying.", step=6)
        recorder.add_insight("interest_drops", "A long silent wait reduced interest.", step=7)
        recorder.add_insight("critical_ux_issues", "The end state was not announced.", step=8, severity="HIGH")
        recorder.add_finding(
            title="Example finding",
            description="This synthetic finding only verifies rendering.",
            severity="OBSERVATION",
            reproduction=["Launch", "Observe the initial state"],
            impact="No product impact; self-check fixture.",
            evidence=[png_path],
        )
        recorder.set_reached_point("synthetic 20-step checkpoint")
        recorder.set_reproduction_steps(["Launch the target", "Observe one state", "Choose one action"])
        recorder.finish("success", reason="recorder self-check")
        json_path = recorder.save_json()
        markdown_path = recorder.save_markdown()
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")
        assert payload["black_box"] is True
        assert payload["action_count"] == 20
        assert set(payload["insights"]) == set(INSIGHT_BUCKETS)
        for heading in (
            "Persona",
            "Play time",
            "Actions",
            "Reached point",
            "Outcome",
            "Severity",
            "Confusion points",
            "Misread UI",
            "Misunderstood rules",
            "Progress-blocking candidates",
            "Bug candidates",
            "Fun moments",
            "Interest drops",
            "Critical UX issues",
            "Evidence screenshots",
            "`OBSERVATION`",
        ):
            assert heading in markdown, heading
        assert "No TODO" not in markdown
        print(json.dumps({"ok": True, "json": str(json_path), "markdown": str(markdown_path)}))
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
