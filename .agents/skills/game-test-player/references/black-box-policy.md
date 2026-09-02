# First-time black-box policy

This policy is a hard boundary, not a suggestion. In `first_time`, the tester may use only:

- pixels from the current visible game window;
- ordinary keyboard, mouse, and gamepad-equivalent input;
- audio, subtitles, and text that an ordinary player receives.

The tester must not use:

- game source, scripts, scenes, `project.godot`, assets, data tables, or configuration to infer an answer;
- SceneTree, Nodes, signals, debug overlays, console output, profiler output, hidden values, memory, save files, or network state;
- a precomputed route, expected coordinate list, test-only shortcut, or engine/instrumentation API;
- a screenshot taken from an internal render target that the player could not see.

Passing a project directory to the adapter is allowed only because it is a launch target. The adapter must not open or parse files in that directory. If a diagnosis would require hidden information, report the player-visible symptom and mark the cause as unknown rather than breaking the policy.

## Self-check before reporting

Confirm that:

1. the session JSON says `black_box: true`;
2. every action is an ordinary adapter operation;
3. evidence paths point to player-visible screenshots;
4. findings distinguish observation from verified impact;
5. no conclusion depends on data the player could not see.
