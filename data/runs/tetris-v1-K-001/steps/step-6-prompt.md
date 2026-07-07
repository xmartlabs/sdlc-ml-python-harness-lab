<!-- SDLC continuation step 6 for RUN_ID=tetris-v1-K-001 -->

# Tetris — Session 6

You are continuing an ongoing build. Your plan and decision records are below.

---

## Current plan (from `workspace/tetris-v1-K-001/PLAN.md`)

# Tetris Build Plan — tetris-v1-K-001

## Architecture

`main.py` will be a single-file implementation using pygame. The game uses a `TetrisGame` class that encapsulates the board state, active piece, and game loop. The board is a 2D list (10 columns × 20 rows). Pieces are defined as 4-block shapes with rotation matrices. A fixed-interval gravity timer drives piece descent. The game loop runs at 60fps via `pygame.time.Clock`, processing events, updating state, and rendering each frame.

## Milestones
- [x] 1. Empty pygame window (300×600, 60fps, clean quit on QUIT event)
- [x] 2. Board grid rendered + static piece displayed
- [x] 3. Piece falls at fixed interval (gravity loop)
- [x] 4. Left/right movement (←/→ keys, within board bounds)
- [x] 5. Rotation (↑ key, wall-kick basic)
- [ ] 6. Collision detection + piece lock + spawn next piece
- [ ] 7. Line clearing + score counter displayed
- [ ] 8. Game over condition + restart (R key or automatic)

## Architecture Decision Records

### ADR-001: Single-file implementation for Milestone 1
**Decision:** Implement Milestone 1 as a single `main.py` file with a `TetrisGame` class.
**Reason:** The benchmark scope is incremental — each milestone adds one capability. A single file minimizes scaffolding overhead and keeps the smoke-testable artifact simple.
**Consequences:** As milestones accumulate, the file will grow. Future milestones may warrant splitting into modules (e.g., `pieces.py`, `board.py`), but that is deferred until needed.

### ADR-002: Piece representation and board rendering for Milestone 2
**Decision:** Pieces stored as lists of (row, col) offset tuples relative to an origin; board cells store RGB tuples (black for empty). Grid lines drawn via `pygame.draw.line` over a black background. Each cell gets a filled rect plus a 1px darker border.
**Reason:** Offset-tuple representation makes rotation trivial (swap row/col) and keeps piece data lightweight. RGB tuples in the board avoid needing a separate "filled" flag. Grid lines give visual structure without requiring a texture asset.
**Consequences:** The board list-of-tuples approach is simple but will need to change when we add line-clearing logic (we'll need to know which cells are filled vs empty quickly). This is fine for now since the board is only 10×20.

### ADR-003: Gravity timer design for Milestone 3

**Decision:** Use a frame-count-based gravity timer (`gravity_timer` incremented each frame, compared to `gravity_interval` of 35 frames ≈ 0.58s at 60fps) rather than `pygame.time.get_ticks()` deltas.

**Reason:** Frame-counting avoids timing drift concerns and keeps the smoke-test path (which runs a fixed number of frames) consistent with the live game loop. The interval is tuned so pieces fall visibly but not so fast as to be unplayable.

**Consequences:** Frame-rate dependency means gravity speed varies if FPS changes. This is acceptable for now since the game targets a fixed 60fps and milestones are built incrementally.

### ADR-004: Left/right movement via key polling for Milestone 4

**Decision:** Use `pygame.key.get_pressed()` for left/right movement, clamping `piece_col` so the piece stays within board bounds.

**Reason:** Key polling captures sustained presses (smooth movement) without requiring repeat event handling. Clamping uses the piece's min/max column offsets so any shape type stays fully on-screen.

**Consequences:** Continuous key-press movement means the piece moves one cell per frame while held — fast enough to be responsive at 60fps. Future milestones may want a delay/repeat mechanism for finer control, but this is sufficient for now.

### ADR-005: Rotation via offset tuple swap + wall-kick for Milestone 5

**Decision:** Rotate pieces 90° clockwise by transforming each offset `(r, c)` → `(c, -r)`. After rotation, try wall kicks at column offsets `[0, 1, -1, 2, -2]` to bring the piece back on-screen.

**Reason:** The offset-tuple representation (ADR-002) makes rotation a simple coordinate transform without needing a matrix. Wall-kick offsets follow the standard Tetris convention of trying the original position first, then ±1, then ±2 — enough to handle most edge cases without complex kick tables.

**Consequences:** This is a basic wall-kick system. Advanced kick tables (e.g., SRS) are deferred to Milestone 6 when collision detection is added. The `_can_place` helper only checks board bounds at this stage; full collision checking against placed pieces comes later.

## Session Log
- Session 1: ✅ Milestone 1 — empty window (300×600, 60fps, smoke check exits 0)
- Session 2: ✅ Milestone 2 — board grid rendered + static piece displayed
- Session 3: ✅ Milestone 3 — piece falls at fixed interval (gravity loop)
- Session 4: ✅ Milestone 4 — left/right movement (←/→ keys, within board bounds)
- Session 5: ✅ Milestone 5 — rotation (↑ key, basic wall-kick)

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
   - Append to Session Log: `Session 6: ✅ Milestone N — <what you built>`
5. End your response:
   - If milestones still remain `[ ]`: `DONE: Milestone N complete.`
   - If and ONLY if every milestone is now `[x]`: `ALL MILESTONES COMPLETE`
