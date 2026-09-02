# Interactive runner protocol

`run_session.py` is a line-oriented controller boundary. It emits JSON events on stdout and consumes one JSON object per stdin line. Blank lines are ignored. A controller should inspect the latest `observation.screenshot.path` before sending the next action.

## Events

- `ready`: launch metadata and `black_box: true`.
- `observation`: the initial frame or the frame after one action. `screenshot` is `null` only when capture failed; `capture_error` explains why.
- `recorded`: a report-only command was accepted.
- `command_error`: the line was invalid; the session remains alive.
- `finished`: the chosen outcome.
- `reports`: absolute paths to `session.json` and `report.md`.
- `error`: launch/runtime failure; reports are still written.

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

`state_summary` and `player_facing_audio` are optional visible observations attached to the post-action frame; they must describe only what the player can perceive.

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
