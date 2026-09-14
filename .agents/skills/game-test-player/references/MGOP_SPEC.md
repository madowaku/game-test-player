# madowaku Game Observation Protocol (MGOP) v1

Status: Draft v1

MGOP is the engine-neutral observation contract used by instrumented game-test-player sessions. It complements, but never replaces, black-box first-time playtests.

## 1. Goals

MGOP lets an agent observe a running game, load deterministic diagnostic scenarios, collect performance/error signals, and attach machine-readable state to the same evidence session used for screenshots and player actions.

MGOP must:

- stay engine-neutral at the consumer boundary;
- expose only explicitly declared diagnostic state;
- support deterministic named scenarios where practical;
- preserve ordinary player input as the path for gameplay actions;
- produce evidence that can be compared before and after a fix;
- remain disabled unless an instrumented policy/mode explicitly opts in.

## 2. Non-goals

MGOP is not a cheat API, automation macro format, game logic replacement, or source inspection interface. It must not silently alter black-box `first_time` behavior.

A diagnostic bridge may prepare state through `load_scenario(id)`, but should not expose outcome-forcing commands such as `defeat_boss()`, `grant_win()`, or `complete_objective()`.

## 3. Protocol version

Implementations MUST report:

```json
{
  "mgop_version": "1.0"
}
```

Breaking contract changes require a major version change.

## 4. Required operations

An adapter SHOULD expose the following operations when supported:

- `get_state()`
- `get_metrics()`
- `get_errors()`
- `load_scenario(id)`
- `reset()`
- `pause()`
- `resume()`
- `capture()`

`get_state()` is required for MGOP compliance. Unsupported optional operations MUST fail explicitly rather than pretending success.

## 5. State envelope

`get_state()` returns a JSON object using this envelope:

```json
{
  "mgop_version": "1.0",
  "observed_at": "2026-09-14T00:00:00.000Z",
  "scenario": {
    "id": "first-combat",
    "fixture": true
  },
  "game": {
    "scene": "stage_01",
    "state": "playing",
    "paused": false,
    "time_seconds": 12.4
  },
  "player": {
    "position": [0, 0, 0],
    "velocity": [0, 0, 0],
    "state": "controllable",
    "health": 100,
    "alive": true
  },
  "camera": {
    "mode": "follow",
    "position": [0, 0, 0]
  },
  "world": {
    "enemy_count": 0,
    "active_entities": 0
  },
  "objective": {
    "id": "",
    "state": ""
  },
  "input": {
    "last_action": ""
  },
  "performance": {
    "fps": 0,
    "frame_time_ms": 0
  },
  "errors": {
    "count": 0,
    "recent": []
  },
  "extensions": {}
}
```

Fields that cannot be observed MAY be `null` or omitted inside their section. Producers SHOULD keep section names stable.

## 6. Extensions

Game-specific or engine-specific diagnostics belong under `extensions`.

Example:

```json
{
  "extensions": {
    "vehicle": {
      "speed": 83.0,
      "grounded": false
    },
    "terrain": {
      "ready": true,
      "pending_jobs": 2
    }
  }
}
```

Consumers MUST ignore unknown extension keys.

## 7. Metrics

`get_metrics()` SHOULD return a point-in-time sample:

```json
{
  "fps": 60.0,
  "frame_time_ms": 16.67,
  "memory_mb": null,
  "draw_calls": null,
  "triangles": null,
  "active_entities": 18,
  "physics_objects": null,
  "scene_load_ms": null,
  "extensions": {}
}
```

Comparisons are valid only when the scenario, build configuration, renderer/backend, and machine are materially equivalent.

## 8. Errors

`get_errors()` SHOULD return structured recent errors:

```json
{
  "count": 1,
  "recent": [
    {
      "level": "error",
      "message": "Example failure",
      "source": "runtime",
      "observed_at": "2026-09-14T00:00:00.000Z"
    }
  ]
}
```

Do not invent stack traces or source locations that are unavailable.

## 9. Named scenarios

A named scenario is a deterministic diagnostic start point.

Recommended IDs are kebab-case, for example:

- `boot`
- `tutorial-start`
- `first-combat`
- `player-death`
- `boss-phase-2`
- `save-reload`

`load_scenario(id)` MUST report whether the scenario was accepted. A successful load SHOULD make the current scenario visible in subsequent `get_state()` responses.

Fixture scenarios are not substitutes for journey tests. A critical flow should eventually have both a direct fixture test and a real player journey where practical.

## 10. Evidence integration

Instrumented sessions SHOULD attach MGOP state to observation records rather than replacing screenshots.

Recommended observation shape:

```json
{
  "step": 3,
  "screenshot": {"path": "..."},
  "state_summary": "Enemy defeated",
  "mgop": {
    "state_path": "state/step-003.json",
    "metrics_path": "metrics/step-003.json",
    "errors_path": "errors/step-003.json"
  }
}
```

An evidence bundle SHOULD contain:

```text
session.json
report.md
screenshots/
state/
metrics/
errors/
```

The exact file set may vary by adapter capability.

## 11. Session validity

A session that claims gameplay verification MUST NOT be considered successful if it records zero actions and zero observations.

An instrumented session MAY contain zero gameplay actions only for an explicitly diagnostic scenario whose purpose is observation-only; that intent must be recorded in session metadata.

## 12. Modes and policy

Recommended modes:

- `black_box`: visible game window and ordinary input only;
- `instrumented`: ordinary input plus MGOP diagnostics;
- `diagnostic`: MGOP-heavy investigation, including named fixture loading.

`first_time` MUST remain black-box unless a caller explicitly requests a different mode. Hidden diagnostic state must never leak into a black-box persona run.

## 13. Adapter responsibilities

Each engine adapter is responsible for translating engine-native state into the MGOP envelope.

Examples:

- Godot: runtime bridge/autoload -> JSON snapshot -> game-test-player adapter;
- Unity: runtime debug bridge -> JSON snapshot -> game-test-player adapter;
- Web: `window.__MADOWAKU_GAME__` -> Playwright consumer.

The consumer should reason against MGOP field names, not engine internals.

## 14. Security and shipping

Diagnostic bridges SHOULD be disabled in production builds unless explicitly required. Projects should provide a build flag, feature flag, or environment check so MGOP endpoints are not unintentionally exposed to end users.

## 15. Definition of Done for an MGOP adapter

An adapter is v1-ready when it can:

1. return `mgop_version: 1.0`;
2. expose current scene/game state;
3. expose at least one player/world signal relevant to the game;
4. identify the active named scenario when one is loaded;
5. surface basic performance or error information when supported;
6. write MGOP evidence alongside screenshots;
7. fail unsupported operations explicitly;
8. remain opt-in and preserve black-box behavior.
