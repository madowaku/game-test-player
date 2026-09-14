---
name: game-test-player
description: Play Godot games through the real OS window as a screenshot-driven human-first tester, or explicitly opt into MGOP-backed instrumented diagnostics that attach runtime state, metrics, and errors to the same evidence session.
---

# Game Test Player

Use this skill when the goal is to learn what a real first-time player can see, infer, and do in a Godot game, or when an explicit instrumented diagnostic pass is needed after reproduction. The default path remains black-box. The session/report contract is intended to be reusable by future Browser, Unity, or other adapters.

## Operating contract

- Default to `first_time` and `black_box`. Choose `impatient` when skipped instructions and tempo matter, or `explorer` when trying interesting/secondary affordances is the point. Read the matching file in `personas/` before play.
- In black-box `first_time`, use only the visible game window, ordinary keyboard/mouse/gamepad-equivalent input, and player-facing audio/text. Never inspect source, `project.godot`, SceneTree/Nodes, hidden values, debug overlays, save data, or a known solution route. Do not use an adapter API that exposes any of those things.
- Launch the game as the player would. Prefer a packaged executable; a project directory is accepted only as a launch target passed to Godot, not as something to inspect.
- Treat the screenshot as the source of truth in black-box mode. After every meaningful state change, capture it and reassess. Decide one next action at a time; do not send a precomputed input macro in a first-time session.
- In instrumented mode, keep ordinary player input as the gameplay action path. MGOP may observe declared runtime diagnostics, but must not become a cheat API or force outcomes.
- Keep internal notes sparse but record them at important turns: what seems to be happening, the next goal, the chosen action, why, confidence (0-1), hesitation, expected result, actual result, and any discovered misunderstanding. Use the fields in `scripts/session.py` rather than writing a verbose transcript.
- Stop at success, failure, a credible stuck state, a player-facing end screen, or the session timeout. If the game ends before 20 actions, record that fact rather than inventing actions. Otherwise run at least 20 observe -> one-action -> observe cycles for the v0.1 smoke scenario.

## Godot workflow

The OS-window adapter is [adapters/godot/godot_adapter.py](adapters/godot/godot_adapter.py). It uses the real Windows game window and exposes only:

`launch_game`, `capture_screenshot`, `press_key`, `key_down`, `key_up`, `mouse_move`, `mouse_click`, `wait`, `restart_game`, and `terminate_game`.

For an interactive black-box AI-controlled run, use:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <path-to-game.exe-or-Godot-project-directory> --persona first_time --scenario short-game --out <evidence-directory> --max-steps 60 --max-seconds 300
```

The runner prints one JSON object per line. It takes one JSON action from stdin, executes it, waits for the requested settle interval, captures the next screenshot, and prints the next observation. Inspect the printed screenshot path with the image viewer before choosing the next action. The exact protocol and allowed fields are in [references/runner-protocol.md](references/runner-protocol.md).

Example action (one action only):

```json
{"action":"press_key","key":"space","hold_s":0.08,"settle_s":0.35,"what_happening":"A start prompt is visible","next_goal":"Begin the game","reason":"Space is the only prominent actionable cue","confidence":0.82,"hesitation":false,"expected_result":"The playfield appears"}
```

The runner writes `session.json` and `report.md` in `--out`. Use [scripts/generate_report.py](scripts/generate_report.py) to render Markdown again after adding findings or insights to the JSON. Use [scripts/self_check.py](scripts/self_check.py) for a no-Godot validation of the recorder and report format.

## Instrumented MGOP workflow

MGOP is an explicit diagnostic path and must never silently alter the default black-box behavior. The canonical contract is [references/MGOP_SPEC.md](references/MGOP_SPEC.md).

For Godot 4 development/test builds, install [adapters/godot/runtime/mgop_bridge.gd](adapters/godot/runtime/mgop_bridge.gd) as an Autoload in the target project. The bridge remains dormant until `MADOWAKU_MGOP=1` is present. Game-specific projects register Callables that expose allowed state, metrics, errors, named scenario loading, and reset behavior.

The runner now owns the normal instrumented path. Use either:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <game> --mode instrumented --scenario <scenario> --out <evidence-directory>
```

or the shortcut:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <game> --mgop --scenario <scenario> --out <evidence-directory>
```

In instrumented mode the runner:

1. enables `MADOWAKU_MGOP=1` and the selected MGOP port for the launched child process;
2. requires a successful MGOP v1 startup handshake rather than silently falling back to black-box;
3. captures the visible screenshot at step 0 and after each ordinary player action;
4. automatically queries `get_state`, `get_metrics`, and `get_errors` at the same step;
5. writes those payloads under `state/`, `metrics/`, and `errors/`;
6. attaches their paths to the matching session observation;
7. emits the collected diagnostics in the line-oriented observation event for an AI controller;
8. writes `bundle.json` at session finalization;
9. preserves MGOP across `restart_game`; and
10. restores the caller environment when the runner exits.

Optional diagnostic channels fail independently after the initial handshake. For example, if metrics are unsupported, the screenshot and available state/errors remain valid evidence and the collection failure is reported explicitly.

Use [scripts/mgop_client.py](scripts/mgop_client.py) when a standalone MGOP query is needed and [scripts/evidence_bundle.py](scripts/evidence_bundle.py) when another tool needs to build a compatible evidence set.

A fixture scenario is a diagnostic shortcut, not proof that the real player journey works. For critical gameplay flows, rerun the corresponding ordinary journey after a fix when practical.

## Real Godot MGOP smoke fixture

The committed Godot 4 project at [fixtures/godot-smoke](fixtures/godot-smoke) is the real-engine infrastructure smoke target. It is not a UX scenario. Its only job is to prove that an ordinary player input can produce a visible state change and a matching MGOP state change in the same Evidence Bundle.

On Windows with Godot 4 available, run:

```text
python .agents/skills/game-test-player/scripts/run_godot_smoke.py
```

Or pass the executable explicitly:

```text
python .agents/skills/game-test-player/scripts/run_godot_smoke.py --godot "C:\path\to\Godot_v4.x-stable_win64.exe"
```

The fixture starts in `IDLE`. The smoke runner waits for the MGOP handshake and step-0 observation, sends exactly one ordinary `SPACE` key action, then requires step 1 to be visibly captured and internally reported as `ACTIVATED`. It also requires `objective.state=complete`, `input.last_action=space`, `extensions.smoke_fixture.action_count=1`, two aligned bundle entries, and a successful session report.

The fixture vendors a byte-identical copy of the canonical `mgop_bridge.gd` because Godot resources are scoped to the project `res://` root. `run_godot_smoke.py` refuses to run if the fixture bridge has drifted from the canonical bridge.

Passing this smoke validates infrastructure only. It never replaces a black-box first-time run or a critical player journey.

## Persona behavior

Persona files are deliberately short and behavioral. They must change what the tester notices and tries, not grant hidden knowledge:

- [personas/first_time.md](personas/first_time.md)
- [personas/impatient.md](personas/impatient.md)
- [personas/explorer.md](personas/explorer.md)

Use [scenarios/short-game.md](scenarios/short-game.md) for the v0.1 completion pass. It is a player-facing contract, not a route or a cheat sheet.

## Findings and report

Use `OBSERVATION` for a noteworthy experience with no demonstrated harm. Use `LOW`, `MEDIUM`, `HIGH`, or `BLOCKER` when the evidence supports increasing player impact. A finding should state what the player saw, how to reproduce it, why it matters, and the evidence screenshot path. Keep UX confusion, misread UI, unknown rules, stuck candidates, bug candidates, fun moments, interest drops, and critical UX issues in the corresponding insight buckets as well as in a severity-rated finding when appropriate.

The final report must contain at least Persona, play time, action count, reached point, outcome (`success`, `failure`, `timeout`, or `aborted`), confusion points, misread UI, misunderstood rules, stuck candidates, bug candidates, fun moments, interest drops, critical UX issues, reproduction steps, evidence screenshots, and Severity. The recorder emits all of these keys even when a category is empty; never fill an empty category by guessing.

Read [references/black-box-policy.md](references/black-box-policy.md) when a run might be tempted to use project/debug data, [references/MGOP_SPEC.md](references/MGOP_SPEC.md) for instrumented policy, and [references/session-schema.json](references/session-schema.json) when another tool needs to consume the JSON.

## Adapter boundary

Keep future adapters behind the same session operations and evidence shape. Black-box and instrumented capabilities must remain explicit modes. Do not add Godot-specific hidden-state APIs to the default black-box path, and never expose instrumented data to a black-box run unless the caller explicitly chooses an instrumented mode.
