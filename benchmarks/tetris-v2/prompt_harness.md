# Engineering harness (read before you touch code)

You are operating inside a governed repository. Before writing code, ground
yourself in its harness — this is mandatory, not optional:

1. Read the governance docs at the repo root:
   - `.github/architecture.md`
   - `.github/standards.md`
   - `.github/domain-boundaries.md`
2. Read project memory for prior context:
   - `memory/context.md`
   - `memory/learnings.md`
3. Follow the working loop: Ground → Plan → Delegate → Verify → Compound.

Drive the work with the repo's skills, not ad-hoc improvisation:

- Use the **plan_and_execute_feature** skill to turn the task into explicit,
  phased work before you implement it.
- Use the **systematic_debugging** skill when something does not behave as
  expected — reproduce → isolate → hypothesize → fix → verify. No blind edits.
- Use the **verify_changes** skill to confirm the work before you consider it
  done.

Apply the harness to *how* you solve the task. The task itself, its scope and
its acceptance criteria are stated below and are not changed by this preamble.

---

# Task: build a testable Tetris

Build a playable Tetris game in Python using `pygame`, with its game logic
exposed as a class so it can be tested without a display.

All files go inside `{workspace}/`. Entry point: `{workspace}/main.py`.

## Requirements

- A single runnable entry point (`python main.py` opens the game window).
- The 7 standard tetromino pieces, falling automatically.
- Left/right movement, rotation, soft drop, hard drop.
- Collision detection and piece lock.
- Line clearing with a score counter.
- Game over when pieces reach the top.

## The `Game` contract (exact)

Your build is graded by an automated test suite that drives the game through a
class named `Game` in `main.py`. The tests import `main` with `pygame` running
under `SDL_VIDEODRIVER=dummy`, so **importing `main` must not open a window,
block, or start the loop** — put the interactive loop behind
`if __name__ == "__main__":`.

```python
game = Game(width=10, height=20, seed=42)
game.board          # list[list[int]] — height rows × width cols; 0 = empty
game.current        # the active piece; has .shape (list[list[int]]), .x, .y
game.score          # int, starts at 0
game.lines_cleared  # int, starts at 0
game.game_over      # bool, starts False
game.move(dx)       # dx in {-1, +1}; returns True if the piece moved
game.rotate()       # rotate clockwise; returns True if it rotated
game.soft_drop()    # move down one row; returns True if it moved, else locks the piece
game.hard_drop()    # drop to the bottom and lock; returns the number of rows dropped
game.step()         # one gravity tick: same as soft_drop()
```

Semantics the tests rely on:

- `Game(seed=N)` is deterministic: the same seed yields the same piece sequence.
- A piece that cannot move down on `soft_drop()`/`step()` is **locked** into
  `board` (its cells become non-zero) and the next piece spawns at the top.
- `move()` and `rotate()` return `False` and change nothing when blocked by a
  wall or a locked cell.
- When a row of `board` is completely non-zero after a lock, it is removed, the
  rows above shift down, `lines_cleared` increases by the number of rows removed,
  and `score` increases (any positive amount per line; more for more lines at
  once).
- When a newly spawned piece overlaps a locked cell, `game_over` becomes `True`.

## Constraints

- Single Python file: `main.py`.
- `pygame` only — no external assets (sprites, sounds).
- Do not modify the base repo outside `main.py`.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/tetris-v2/tests/test_game.py`. Read
them, and run them yourself before you finish:

```bash
cd {workspace} && SDL_VIDEODRIVER=dummy python -m pytest /app/benchmarks/tetris-v2/tests -q
```

Do not edit the tests. Also keep the headless smoke mode: with the environment
variable `BENCH_SMOKE=1`, `python main.py` runs about 120 frames and **exits
cleanly with code 0**; without it, `python main.py` stays fully interactive.

Deliver the complete code. Keep it self-contained and runnable.
