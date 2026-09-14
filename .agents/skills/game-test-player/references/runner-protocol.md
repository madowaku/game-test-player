# Interactive runner protocol

`run_session.py` is a line-oriented controller boundary. It emits JSON events on stdout and consumes one JSON object per stdin line. Blank lines are ignored. A controller should inspect the latest observation before sending the next action.

The runner has two explicit modes:

- `black_box` (default): screenshot-driven player-perspective testing only.
- `instrumented`: the same ordinary player input plus MGOP state, metrics, and error evidence collected at every observation.

`--mgop` is a shortcut for `--mode instrumented`. It cannot be combined with `--mode black_box`.

## Launch examples

Black-box first-time playtest:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <game> --persona first_time --scenario short-game --out <evidence>
```

Instrumented diagnostic playtest:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <game> --mode instrumented --scenario short-game --out <evidence>
```

Instrumented mode sets `MADOWAKU_MGOP=1` and `MADOWAKU_MGOP_PORT=<port>` for the launched game process. A compatible MGOP bridge must answer the startup handshake. The default endpoint is `127.0.0.1:49561`; override it with `--mgop-host`, `--mgop-port`, `--mgop-timeout`, and `--mgop-connect-timeout` when needed.

The runner keeps the MGOP environment active across `restart_game` and restores the caller environment when the session exits.

## Events

- `ready`: launch metadata, resolved `mode`, `black_box`, and MGOP handshake metadata when instrumented.
- `observation`: the initial frame or the frame after one action. `screenshot` is `null` only when capture failed; `capture_error` explains why. In instrumented mode, `instrumentation` also contains the collected `state`, `metrics`, `errors`, any per-channel `collection_errors`, and durable evidence `paths`.
- `recorded`: a report-only command was accepted.
- `command_error`: the line was invalid; the session remains alive.
- `finished`: the chosen outcome.
- `reports`: absolute paths to `session.json`, `report.md`, and `bundle.json` when instrumented.
- `error`: launch/runtime/MGOP startup failure; reports are still written.

MGOP collection after startup is channel-independent. If, for example, metrics are unsupported, the screenshot and any available state/errors are still recorded and the failure appears under `instrumentation.collection_errors.metrics`. An instrumented session never silently falls back to black-box if the initial MGOP handshake fails.

## Player actions

Exactly one of these operations may appear in an action command:

```json
{"action":"press_key","key":"space","hold_s":0.08,"settle_s":0.35}
{"action":"key_down","key":"left","settle_s":0.1}
{"action":"key_up","key":"left","settle_s":0.1}
{"action":"mouse_move","x":320,"y":180,"relative":true,"settle_s":0.1}
{"action":"mouse_click","x":320,"y":180,"button":"left","clicks":1,"interval_s":0.08,"relative":true,"settle_s":0.35}
{"action":"wait","seconds":0.6}
{"action":"restart_game","settle_s":0.5}
{"action":"terminate_game"}
```

Coordinates are relative to the captured window unless `relative` is `false`. `settle_s` is a player-visible transition wait and defaults to 0.25 for non-`wait` actions. The operation is recorded even when it fails.

Append decision fields to an action when they matter:

```json
{"action":"mouse_click","x":320,"y":180,"what_happening":"A large button is visible","next_goal":"Open the level","reason":"It is the only labeled control","confidence":0.74,"hesitation":false,"expected_result":"A level view appears","actual_result":"The menu stayed open","misunderstanding":"The panel looked enabled but did not respond"}
```

The runner stores these as a compact decision memo. Empty fields are allowed; do not write a stream-of-consciousness transcript.

After inspecting the resulting frame, complete the previous memo on the next command when useful:

```json
{"previous_actual_result":"The menu opened","previous_misunderstanding":"The first click was on a decorative panel","action":"press_key","key":"escape","what_happening":"The menu is now open","next_goal":"Return to play","reason":"Escape is the visible back convention","confidence":0.9,"expected_result":"The playfield returns"}
```

In `black_box`, `state_summary` and `player_facing_audio` must describe only what the player can perceive. In `instrumented`, hidden diagnostic state belongs in MGOP, not in those human-perspective fields.

## Instrumented evidence shape

At each observation step, the runner keeps screenshot and MGOP evidence aligned by step number:

```text
evidence/
  0000-initial.png
  0001-observation.png
  session.json
  report.md
  bundle.json
  state/
    state-step-0000.json
    state-step-0001.json
  metrics/
    metrics-step-0000.json
    metrics-step-0001.json
  errors/
    errors-step-0000.json
    errors-step-0001.json
```

The matching observation in `session.json` contains MGOP file references. `bundle.json` is the machine-readable manifest for the instrumented evidence set.

## Report-only commands

These do not count as player actions and do not call the game:

```json
{"record":"insight","bucket":"confusion_points","text":"The next objective was not apparent","severity":"MEDIUM","step":2,"evidence":"C:/evidence/0002-observation.png"}
{"record":"finding","title":"Start affordance is ambiguous","description":"The first actionable frame contains two equally prominent controls.","severity":"HIGH","category":"UX","impact":"A first-time player can choose the wrong route.","reproduction":["Launch the game","Observe the first actionable frame"],"evidence":["C:/evidence/0000-initial.png"],"step":0}
{"record":"reached_point","value":"First level loaded"}
{"record":"reproduction_steps","steps":["Launch the game","Press Space once","Observe the result"]}
```

`bucket` must be one of `confusion_points`, `misread_ui`, `misunderstood_rules`, `stuck_candidates`, `bug_candidates`, `fun_moments`, `interest_drops`, or `critical_ux_issues`. Severity must be `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, or `OBSERVATION`.

## Finish

```json
{"action":"finish","outcome":"success","reached_point":"Goal screen shown","reason":"The short scenario visibly completed"}
```

Allowed outcomes are `success`, `failure`, `timeout`, and `aborted`. If the game ends early, finish with the visible outcome and explain the early stop; do not pad the action count with invented input.
