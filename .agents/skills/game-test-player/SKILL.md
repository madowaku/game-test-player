---
name: game-test-player
description: Play Godot games as a screenshot-driven human-first tester through the OS game window, then produce evidence-backed UX and bug findings. Use for black-box playtests, not source-level or instrumented QA.
---

# Game Test Player

Use this skill when the goal is to learn what a real first-time player can see, infer, and do in a Godot game. v0.1 provides one OS-window adapter; the session/report contract is intentionally reusable by future Browser, Unity, or other adapters.

## Operating contract

- Default to `first_time`. Choose `impatient` when skipped instructions and tempo matter, or `explorer` when trying interesting/secondary affordances is the point. Read the matching file in `personas/` before play.
- In `first_time`, stay black-box. Use only the visible game window, ordinary keyboard/mouse/gamepad-equivalent input, and player-facing audio/text. Never inspect source, `project.godot`, SceneTree/Nodes, hidden values, debug overlays, save data, or a known solution route. Do not use an adapter API that exposes any of those things.
- Launch the game as the player would. Prefer a packaged executable; a project directory is accepted only as a launch target passed to Godot, not as something to inspect.
- Treat the screenshot as the source of truth. After every meaningful state change, capture it and reassess. Decide one next action at a time; do not send a precomputed input macro in a first-time session.
- Keep internal notes sparse but record them at important turns: what seems to be happening, the next goal, the chosen action, why, confidence (0-1), hesitation, expected result, actual result, and any discovered misunderstanding. Use the fields in `scripts/session.py` rather than writing a verbose transcript.
- Stop at success, failure, a credible stuck state, a player-facing end screen, or the session timeout. If the game ends before 20 actions, record that fact rather than inventing actions. Otherwise run at least 20 observe -> one-action -> observe cycles for the v0.1 smoke scenario.

## Godot v0.1 workflow

The adapter is [adapters/godot/godot_adapter.py](adapters/godot/godot_adapter.py). It uses the real Windows game window and exposes only:

`launch_game`, `capture_screenshot`, `press_key`, `key_down`, `key_up`, `mouse_move`, `mouse_click`, `wait`, `restart_game`, and `terminate_game`.

For an interactive AI-controlled run, use:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <path-to-game.exe-or-Godot-project-directory> --persona first_time --scenario short-game --out <evidence-directory> --max-steps 60 --max-seconds 300
```

The runner prints one JSON object per line. It takes one JSON action from stdin, executes it, waits for the requested settle interval, captures the next screenshot, and prints the next observation. Inspect the printed screenshot path with the image viewer before choosing the next action. The exact protocol and allowed fields are in [references/runner-protocol.md](references/runner-protocol.md).

Example action (one action only):

```json
{"action":"press_key","key":"space","hold_s":0.08,"settle_s":0.35,"what_happening":"A start prompt is visible","next_goal":"Begin the game","reason":"Space is the only prominent actionable cue","confidence":0.82,"hesitation":false,"expected_result":"The playfield appears"}
```

The runner writes `session.json` and `report.md` in `--out`. Use [scripts/generate_report.py](scripts/generate_report.py) to render Markdown again after adding findings or insights to the JSON. Use [scripts/self_check.py](scripts/self_check.py) for a no-Godot validation of the recorder and report format.

## Persona behavior

Persona files are deliberately short and behavioral. They must change what the tester notices and tries, not grant hidden knowledge:

- [personas/first_time.md](personas/first_time.md)
- [personas/impatient.md](personas/impatient.md)
- [personas/explorer.md](personas/explorer.md)

Use [scenarios/short-game.md](scenarios/short-game.md) for the v0.1 completion pass. It is a player-facing contract, not a route or a cheat sheet.

## Findings and report

Use `OBSERVATION` for a noteworthy experience with no demonstrated harm. Use `LOW`, `MEDIUM`, `HIGH`, or `BLOCKER` when the evidence supports increasing player impact. A finding should state what the player saw, how to reproduce it, why it matters, and the evidence screenshot path. Keep UX confusion, misread UI, unknown rules, stuck candidates, bug candidates, fun moments, interest drops, and critical UX issues in the corresponding insight buckets as well as in a severity-rated finding when appropriate.

The final report must contain at least Persona, play time, action count, reached point, outcome (`success`, `failure`, `timeout`, or `aborted`), confusion points, misread UI, misunderstood rules, stuck candidates, bug candidates, fun moments, interest drops, critical UX issues, reproduction steps, evidence screenshots, and Severity. The recorder emits all of these keys even when a category is empty; never fill an empty category by guessing.

Read [references/black-box-policy.md](references/black-box-policy.md) when a run might be tempted to use project/debug data, and [references/session-schema.json](references/session-schema.json) when another tool needs to consume the JSON.

## Adapter boundary

Keep future adapters behind the same session operations and evidence shape. Do not add a Godot-specific internal-driver API to v0.1. An instrumented mode can be added later as an explicitly separate mode with an explicit policy flag; it must never silently run in `first_time`.
