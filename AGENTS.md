# AGENTS.md

## Project mission

game-test-player exists to produce evidence-backed game playtests and diagnostics. Preserve the distinction between player-perspective black-box testing and explicit instrumented diagnosis.

## Operating modes

### Black-box

Use visible game output and ordinary player input only. Do not inspect MGOP, source code, scene trees, hidden values, debug state, saves, or known solution routes.

`first_time` is black-box by default and must remain so unless the caller explicitly selects another mode.

### Instrumented

Instrumented work may use MGOP state, metrics, recent errors, and named diagnostic scenarios in addition to visible screenshots and ordinary player input.

### Diagnostic

Diagnostic work may prioritize reproducibility and internal observation, including named fixture loading. Do not confuse a fixture pass with a real player journey pass.

## Game Development Loop

When changing gameplay or test infrastructure:

1. Reproduce the issue before editing when practical.
2. Record the visible state and, in instrumented mode, relevant MGOP state before the change.
3. Prefer deterministic named scenarios for diagnosis.
4. Trace the relevant system before making a speculative patch.
5. Make the smallest coherent change that addresses the observed cause.
6. Re-run the original reproduction after the change.
7. Compare before/after evidence under materially equivalent conditions.
8. Run related player-journey coverage when the change affects a critical flow.
9. Report remaining uncertainty instead of upgrading weak evidence into certainty.
10. Leave subjective fun, feel, art direction, and final visual approval to the human reviewer.

## MGOP contract

The canonical protocol is `.agents/skills/game-test-player/references/MGOP_SPEC.md`.

- Consumers should reason against MGOP fields, not engine-specific internals.
- Engine-specific data belongs under `extensions` unless it maps naturally to a core MGOP field.
- Unsupported operations must fail explicitly.
- Do not add outcome-forcing diagnostic commands such as `defeat_boss()` or `grant_win()`.
- Runtime diagnostic bridges must be opt-in and should be disabled in production builds.
- Never silently expose MGOP data to a black-box session.

## Runner contract

The normal instrumented entry point is:

```text
python .agents/skills/game-test-player/scripts/run_session.py --game <game> --mode instrumented --scenario <scenario> --out <evidence>
```

`--mgop` is an equivalent shortcut for opting into instrumented mode.

In instrumented mode, the runner must:

- enable the MGOP bridge only for the launched diagnostic game process;
- require a successful MGOP v1 startup handshake and never silently downgrade to black-box;
- preserve ordinary keyboard/mouse input as the gameplay action path;
- collect screenshot + state + metrics + errors at the initial observation and after each action;
- align all evidence by the same step number;
- preserve MGOP enablement across `restart_game`;
- restore the caller environment when the run exits;
- record per-channel collection failures without discarding the remaining evidence; and
- finalize `bundle.json` beside `session.json` and `report.md`.

## Evidence requirements

A significant diagnostic fix should preserve enough evidence to answer:

- What was reproduced?
- What did the player see?
- What internal state was relevant, if instrumentation was enabled?
- What changed?
- Did the same reproduction pass afterward?
- Were nearby journeys rechecked?

An instrumented evidence bundle should keep screenshots and MGOP state together through the same session step numbers.

Do not declare a gameplay verification successful when both `action_count` and `observation_count` are zero. Observation-only diagnostic sessions are allowed only when that intent is explicit in session metadata.

## Performance work

Do not optimize from intuition alone when a measurable baseline is available.

Use this loop:

`baseline -> hypothesis -> change -> same benchmark -> compare`

Do not directly compare numbers gathered on materially different machines, renderers/backends, build modes, or scenarios without calling out the difference.

## Godot MGOP bridge

The reusable Godot 4 runtime bridge template lives at:

`.agents/skills/game-test-player/adapters/godot/runtime/mgop_bridge.gd`

It is intended for development/test Autoload use and is activated by `MADOWAKU_MGOP=1`.

Game-specific projects should register Callables for state, metrics, errors, scenario loading, and reset behavior rather than editing generic protocol behavior into the bridge.

## Validation

Preserve existing black-box self-check behavior. New instrumented features should add checks without weakening the original player-perspective contract.

When the host can execute Python, run both dependency-free checks after changing runner/MGOP plumbing:

```text
python .agents/skills/game-test-player/scripts/self_check.py
python .agents/skills/game-test-player/scripts/self_check_mgop.py
```

When the host is Windows and Godot 4 is available, changes to the Godot adapter, MGOP bridge, instrumented runner, or Evidence Bundle are not ready to merge until the real-engine smoke fixture also passes:

```text
python .agents/skills/game-test-player/scripts/run_godot_smoke.py
```

That smoke gate must prove one ordinary `SPACE` action changes the fixture from `IDLE` to `ACTIVATED` and produces matching screenshot, state, metrics, and errors evidence at the same step. A passing infrastructure smoke does not replace black-box or journey coverage.
