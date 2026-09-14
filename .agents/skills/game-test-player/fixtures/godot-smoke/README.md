# Godot MGOP smoke fixture

This Godot 4 project is the real-engine smoke target for the instrumented game-test-player path. It is intentionally tiny: the player presses `SPACE`, the visible state changes from `IDLE` to `ACTIVATED`, and MGOP must report the same transition at the same session step.

## What it proves

A passing smoke run proves the following path on the Windows host:

```text
run_godot_smoke.py
  -> run_session.py --mode instrumented
  -> Godot OS window
  -> ordinary SPACE key input
  -> MGOPBridge JSONL
  -> state / metrics / errors
  -> screenshot + aligned Evidence Bundle
  -> session.json + bundle.json
```

The fixture registers providers for:

- `player.state`
- `objective.state`
- `input.last_action`
- `extensions.smoke_fixture.phase`
- `extensions.smoke_fixture.action_count`
- metrics extensions
- an empty structured error channel
- named scenarios `smoke-idle` and `smoke-active`
- reset behavior

## Run

From the repository root on Windows with Godot 4 on `PATH` or `GODOT_BIN`:

```text
python .agents/skills/game-test-player/scripts/run_godot_smoke.py
```

Or provide Godot explicitly:

```text
python .agents/skills/game-test-player/scripts/run_godot_smoke.py --godot "C:\path\to\Godot_v4.x-stable_win64.exe"
```

By default evidence is written under `evidence/godot-mgop-smoke/`.

## Pass conditions

The smoke runner fails unless all of these are true:

1. MGOP v1 handshake succeeds.
2. Step 0 has a real screenshot and `player.state == idle`.
3. One ordinary `SPACE` action is recorded.
4. Step 1 has a real screenshot and `player.state == activated`.
5. `objective.state == complete` and `input.last_action == space`.
6. `extensions.smoke_fixture.action_count == 1`.
7. State, metrics, and errors JSON files exist for both observations.
8. `session.json` reports exactly 1 action, 2 observations, instrumented mode, and success.
9. `bundle.json` contains exactly 2 aligned entries.

## Canonical bridge sync

`mgop_bridge.gd` is vendored into this fixture because a Godot project cannot safely depend on a resource outside its `res://` root. `run_godot_smoke.py` compares the fixture copy byte-for-byte with the canonical bridge at `adapters/godot/runtime/mgop_bridge.gd` before launching. If the canonical bridge changes, update the fixture copy in the same change.

This fixture is diagnostic infrastructure. It is not a replacement for a real player journey test.
