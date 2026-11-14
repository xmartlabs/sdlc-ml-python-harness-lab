## Phase 4 of 5 — the computer opponent

The rules engine and the 3D board from phases 1–3 are in the workspace. Read them
before you change them.

Add the computer, exactly as the contract in the task states:

- `best_move(budget_ms)` returns a legal move in UCI **without playing it**, within
  the budget, and finds a forced mate in one and in two;
- the `ai_side` property: when it is that side's turn in the scene, the scene asks
  `best_move` and plays it, and the board shows the reply;
- `godot --path {workspace} -- --ai=black` plays against the computer as white.

Verify `best_move` on positions with a forced mate in one and in two, check it stays
within its budget, and play a move with the mouse against the computer under
`godot-render` to see it answer on the board.

**Stop when phase 4 is done and verified.**
