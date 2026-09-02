# Scenario: short-game (v0.1 smoke pass)

This scenario is intentionally route-free so it can be reused with a short Godot game, tutorial, menu flow, or vertical slice.

## Setup

1. Launch the packaged game (or pass its project directory to the Godot executable).
2. Wait for the first player-actionable frame and capture it as `initial`.
3. Choose a persona and keep its behavior visible in the decision memos.

## Play contract

- Repeat observe → choose exactly one action → act → wait for a visible transition → observe.
- Run 20 action cycles when the game remains playable. If it reaches a player-facing success/failure/end screen earlier, stop and record the early end rather than inventing actions.
- Use `record=insight` for concise confusion, misread UI, rule, stuck, bug, fun, interest, or critical-UX notes. Use `record=finding` only when the evidence supports a severity.
- Include the screenshot path in findings that depend on visual evidence.

## Stop conditions

Finish with `success` when the intended short scenario is visibly complete, `failure` when the game visibly reports failure, `timeout` when the configured time/step limit is reached, and `aborted` when the run cannot continue (for example, the window never appears). Set `reached_point` to the last player-understandable state.

This file never supplies a correct route. The player must infer the route from the screen.
