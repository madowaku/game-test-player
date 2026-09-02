# game-test-player v0.1 design

## Intent

The product is an AI test player, not a conventional assertion runner. The primary artifact is a sequence of visible observations and small player decisions that explains where comprehension, feedback, flow, or interest breaks down.

The boot, main-verb, screenshot, and severity-ordered reporting emphasis follows the existing browser-oriented `game-playtest` approach, while this skill replaces its browser backend with a real Godot OS window and adds the first-time black-box policy.

## Components

| Component | v0.1 responsibility | Deliberate boundary |
| --- | --- | --- |
| `adapters/godot` | Launch one Windows game window, capture pixels, send ordinary input, stop it | No Godot introspection or state reads |
| `personas` | Change attention, patience, and exploration behavior | Never grant hidden knowledge |
| `scenarios` | Define a route-free stop contract and minimum smoke pass | Never encode a correct route |
| `scripts/run_session.py` | Serialize one action, one settle, one screenshot, and a decision memo | Does not decide for the AI |
| `scripts/session.py` | Maintain evidence, insight buckets, findings, and JSON/Markdown output | Does not invent missing observations |

## Control flow

`launch → initial screenshot → (observe → one action → settle → screenshot)* → finish → JSON + Markdown`.

The runner uses a JSON Lines stdin/stdout boundary so Codex, Gemini CLI, or another controller can inspect each screenshot and choose the next action without changing the adapter. Future Browser or Unity backends should expose the same operation names and return screenshot metadata in the same shape.

## Policy seam

`black_box: true` is recorded in every session. v0.1 has no flag that enables internal state. An instrumented mode can be added later only as a separate adapter/mode with explicit authorization and a separate report label; it must never silently run from `first_time`.

## Evidence and severity

Screenshots are durable files, not embedded model claims. A finding points to the frame that supports it and includes reproduction steps and impact. Severity is ordered `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `OBSERVATION`; empty insight buckets remain empty instead of being guessed.

## Non-goals for v0.1

- Cross-platform window backends
- Deterministic replay or input macros for first-time runs
- Godot editor automation or source inspection
- Engine-level assertions, hidden-state snapshots, or a generic game-AI planner
