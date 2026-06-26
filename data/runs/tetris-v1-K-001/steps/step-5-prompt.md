<!-- SDLC continuation step 5 for RUN_ID=tetris-v1-K-001 -->

# Tetris — Session 5

You are continuing an ongoing build. Your plan and decision records are below.

---

## Current plan (from `workspace/tetris-v1-K-001/PLAN.md`)

# Tetris Build Plan — tetris-v1-K-001

## Architecture
Single `main.py` with three classes: `Game` (top-level game loop, state management, rendering dispatcher), `Board` (10x20 grid, line clearing, collision checks), and `Piece` (shape definition, rotation state, position). The game loop runs at 60fps with a gravity timer that drops the active piece every N milliseconds. All rendering batches to a single surface per frame via `Game.render()`.

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

### ADR-001: Single-file structure
**Decision:** Keep all code in `main.py` for this benchmark run.
**Reason:** The benchmark scope is small; splitting across modules adds complexity without benefit for a single-session delivery.
**Consequences:** Easy to run (`python main.py`), harder to scale beyond ~500 lines. Will refactor if milestones require it.

### ADR-002: Piece representation and color mapping
**Decision:** Store each tetromino as a list of (row, col) offset tuples; map piece types to fixed RGB colors via a lookup dict. Board grid stores (R, G, B) tuples per cell, zero-tuple for empty.
**Reason:** Offset tuples keep rotation logic simple (rotate around origin); color lookup avoids hardcoding shape-to-color logic scattered across the file.
**Consequences:** Easy to add new shapes by extending SHAPES/SHAPE_COLORS dicts. Rotation requires translating offsets — straightforward for this scale.

## Session Log
- Session 0: Plan created, implementation started.
- Session 1: ✅ Milestone 1 — empty window (300×600, 60fps, clean quit)
- Session 2: ✅ Milestone 2 — Board grid rendered + static piece displayed
- Session 3: ✅ Milestone 3 — Piece falls at fixed interval (gravity loop, 1s drop)
- Session 4: ✅ Milestone 4 — Left/right movement (←/→ keys, within board bounds)

---

## Your task for this session

1. Read the plan above — find the first unchecked milestone (`[ ]`).
2. Implement **that milestone only**. Do not touch completed milestones.
3. Validate: `python workspace/tetris-v1-K-001/main.py` must run without error.
4. Update `workspace/tetris-v1-K-001/PLAN.md`:
   - Mark the milestone as `[x]` (done)
   - Add an ADR if you made any non-obvious architecture decision
   - Append to Session Log: `Session 5: ✅ Milestone N — <what you built>`
5. If ALL milestones are `[x]`, say: DONE
