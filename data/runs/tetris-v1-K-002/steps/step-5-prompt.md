<!-- SDLC continuation step 5 for RUN_ID=tetris-v1-K-002 -->

# Tetris — Session 5

You are continuing an ongoing build. Your plan and decision records are below.

---

## Current plan (from `workspace/tetris-v1-K-002/PLAN.md`)

# Tetris Build Plan — tetris-v1-K-002

## Architecture

Single-file implementation in `main.py` using pygame. The game uses a `Game` class that encapsulates the board state, current piece, and game loop. The board is a 10-column × 20-row grid rendered as colored rectangles. A fixed timestep loop runs at 60fps with gravity ticking at a configurable interval. Smoke mode (`BENCH_SMOKE=1`) runs ~120 frames then exits cleanly for headless validation.

## Milestones
- [x] 1. Empty pygame window (300×600, 60fps, clean quit on QUIT event)
- [x] 2. Board grid rendered + static piece displayed
- [x] 3. Piece falls at fixed interval (gravity loop)
- [x] 4. Left/right movement (←/→ keys, within board bounds)
- [ ] 5. Rotation (↑ key, wall-kick basic)
- [ ] 6. Collision detection + piece lock + spawn next piece
- [ ] 7. Line clearing + score counter displayed
- [ ] 8. Game over condition + restart (R key or automatic)

## Architecture Decision Records

### ADR-001: Single-file architecture for Milestone 1
**Decision:** Implement Milestone 1 as a single `main.py` file with a `Game` class.
**Reason:** The benchmark scope is incremental — each milestone adds one capability. A single file minimizes import complexity and keeps the smoke test straightforward.
**Consequences:** As milestones accumulate, the file will grow. Future milestones may warrant splitting into modules (e.g., `board.py`, `pieces.py`), but that is deferred until needed.

## Session Log
- Session 1: ✅ Milestone 1 — empty window (300×600, 60fps, smoke check exits 0)
- Session 2: ✅ Milestone 2 — board grid rendered + static T-piece displayed at spawn position
- Session 3: ✅ Milestone 3 — piece falls at fixed interval (gravity loop)

- Session 4: ✅ Milestone 4 — left/right movement (←/→ keys, within board bounds)

### ADR-003: Movement via get_pressed() with clamping
**Decision:** Use `pygame.key.get_pressed()` for horizontal movement, clamped to `[0, COLS - shape_width]`.
**Reason:** `get_pressed()` returns continuous state each frame, giving smooth held-key movement. Clamping at the boundary is simpler than checking collision against the board grid, since no placed pieces exist yet.
**Consequences:** The piece cannot move off-screen. When Milestone 6 adds collision detection, this clamping will need to also check against placed blocks on the board.

### ADR-002: Gravity via accumulated dt
**Decision:** Use a float accumulator (`gravity_acc`) compared against `GRAVITY_INTERVAL` (1.0s) to move the piece down one row.
**Reason:** Accumulating dt avoids drift from integer frame counts and works correctly regardless of actual frame rate. The `while` loop handles cases where multiple intervals elapse in a single frame (e.g., on slow machines).
**Consequences:** The piece will fall faster if frames are dropped. This is acceptable for the benchmark scope; future milestones will add collision detection to stop falling when needed.

---

## Your task for this session

1. Read the plan above — find the first unchecked milestone (`[ ]`).
2. Implement **exactly that ONE milestone**. Do not implement more than
   one milestone this session, and do not touch completed milestones.
3. Validate headlessly (a hanging window or a `timeout` is NOT success):
   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/tetris-v1-K-002/main.py; echo "exit=$?"`
   A correct build prints `exit=0`.
4. Update `workspace/tetris-v1-K-002/PLAN.md`:
   - Mark the milestone as `[x]` (done)
   - Add an ADR if you made any non-obvious architecture decision
   - Append to Session Log: `Session 5: ✅ Milestone N — <what you built>`
5. End your response:
   - If milestones still remain `[ ]`: `DONE: Milestone N complete.`
   - If and ONLY if every milestone is now `[x]`: `ALL MILESTONES COMPLETE`
