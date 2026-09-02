# Godot adapter v0.1

`godot_adapter.py` is a small Windows OS-window backend. It launches a packaged `.exe` directly, or launches a project directory with `godot --path <directory>`. The target path is passed through; the adapter does not read `project.godot`, scenes, scripts, save data, or engine state.

The backend finds a visible top-level window belonging to the launched process (or matching `window_title`), captures the window with Win32 `PrintWindow`/`BitBlt`, encodes a PNG using the Python standard library, and sends ordinary virtual-key/mouse events. Coordinates are relative to the captured window by default.

```python
from adapters.godot import GodotAdapter

with GodotAdapter("C:/games/short-game.exe", evidence_dir="evidence") as game:
    game.launch_game()
    before = game.capture_screenshot(step=0, label="initial")
    game.press_key("space")
    game.wait(0.35)
    after = game.capture_screenshot(step=1)
```

For AI-controlled sessions, prefer `scripts/run_session.py`, which adds the one-action JSON Lines protocol and report recording. v0.1 intentionally does not provide an instrumented Godot driver; add a separate, explicit mode before exposing any internal state.
