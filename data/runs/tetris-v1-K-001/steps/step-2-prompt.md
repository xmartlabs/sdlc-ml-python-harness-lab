<!-- SDLC continuation step 2 for RUN_ID=tetris-v1-K-001 -->

# Tetris — Session 2

You are continuing an ongoing build. Your plan and decision records are below.

---

## Current plan (from `workspace/tetris-v1-K-001/PLAN.md`)

# Tetris Build Plan — tetris-v1-K-001

## Architecture

`main.py` will be a single-file implementation using pygame. The game uses a `TetrisGame` class that encapsulates the board state, active piece, and game loop. The board is a 2D list (10 columns × 20 rows). Pieces are defined as 4-block shapes with rotation matrices. A fixed-interval gravity timer drives piece descent. The game loop runs at 60fps via `pygame.time.Clock`, processing events, updating state, and rendering each frame.

## Milestones
- [x] 1. Empty pygame window (300×600, 60fps, clean quit on QUIT event)
- [ ] 2. Board grid rendered + static piece displayed
- [ ] 3. Piece falls at fixed interval (gravity loop)
- [ ] 4. Left/right movement (←/→ keys, within board bounds)
- [ ] 5. Rotation (↑ key, wall-kick basic)
- [ ] 6. Collision detection + piece lock + spawn next piece
- [ ] 7. Line clearing + score counter displayed
- [ ] 8. Game over condition + restart (R key or automatic)

## Architecture Decision Records

### ADR-001: Single-file implementation for Milestone 1
**Decision:** Implement Milestone 1 as a single `main.py` file with a `TetrisGame` class.
**Reason:** The benchmark scope is incremental — each milestone adds one capability. A single file minimizes scaffolding overhead and keeps the smoke-testable artifact simple.
**Consequences:** As milestones accumulate, the file will grow. Future milestones may warrant splitting into modules (e.g., `pieces.py`, `board.py`), but that is deferred until needed.

## Session Log
- Session 1: ✅ Milestone 1 — empty window (300×600, 60fps, smoke check exits 0)

---

## Your task for this session

1. Read the plan above — find the first unchecked milestone (`[ ]`).
2. Implement **exactly that ONE milestone**. Do not implement more than
   one milestone this session, and do not touch completed milestones.
3. Validate headlessly (a hanging window or a `timeout` is NOT success):
   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/tetris-v1-K-001/main.py; echo "exit=$?"`
   A correct build prints `exit=0`.
4. Update `workspace/tetris-v1-K-001/PLAN.md`:
   - Mark the milestone as `[x]` (done)
   - Add an ADR if you made any non-obvious architecture decision
   - Append to Session Log: `Session 2: ✅ Milestone N — <what you built>`
5. End your response:
   - If milestones still remain `[ ]`: `DONE: Milestone N complete.`
   - If and ONLY if every milestone is now `[x]`: `ALL MILESTONES COMPLETE`
