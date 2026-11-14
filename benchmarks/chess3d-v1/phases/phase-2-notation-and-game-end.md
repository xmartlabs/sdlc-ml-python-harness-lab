## Phase 2 of 5 — notation and the end of the game

The rules core from phase 1 is in the workspace. Read it before you change it.

Add to the `Chess` autoload, exactly as the contract in the task states:

- **SAN out:** `play()` returns the move's standard algebraic notation, with `+`/`#`,
  captures, `O-O`/`O-O-O`, `=Q` promotions, and file/rank disambiguation when two
  pieces of the same kind can reach the square (`Nbd2`, `R1e2`, `Qh4e1`).
- **SAN in:** `play()` also accepts SAN as input, and refuses anything illegal or
  unreadable with `""` and no change.
- `history_san()`.
- `status()` and `result()`: checkmate, stalemate, insufficient material, the
  fifty-move rule and threefold repetition, with the precedence the contract states.

Verify SAN against positions where disambiguation is needed, replay a short game and
compare every SAN, and reach a threefold repetition and a fifty-move draw by play.

**Stop when phase 2 is done and verified.** Do not build the scene or the AI yet.
