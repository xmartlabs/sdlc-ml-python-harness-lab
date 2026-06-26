# Tetris — SDLC Mode: Plan + Execute

You are building a Tetris game in Python (pygame) from scratch.
All files go inside `workspace/{run_id}/`. Entry point: `workspace/{run_id}/main.py`.

---

## Phase 1 — Plan (do this FIRST, before any code)

Create `workspace/{run_id}/PLAN.md` with the following structure:

```markdown
# Tetris Build Plan — {run_id}

## Architecture
<!-- One paragraph: how you will structure main.py (classes, game loop, state) -->

## Milestones
- [ ] 1. Empty pygame window (300×600, 60fps, clean quit on QUIT event)
- [ ] 2. Board grid rendered + static piece displayed
- [ ] 3. Piece falls at fixed interval (gravity loop)
- [ ] 4. Left/right movement (←/→ keys, within board bounds)
- [ ] 5. Rotation (↑ key, wall-kick basic)
- [ ] 6. Collision detection + piece lock + spawn next piece
- [ ] 7. Line clearing + score counter displayed
- [ ] 8. Game over condition + restart (R key or automatic)

## Architecture Decision Records
<!-- ADRs added as you implement. Format:
### ADR-001: <title>
**Decision:** ...
**Reason:** ...
**Consequences:** ...
-->

## Session Log
<!-- One line per session: what was completed -->
```

---

## Phase 2 — Implement Milestone 1

After writing PLAN.md, implement **only Milestone 1**:
- Empty pygame window, 300×600 pixels
- Runs at 60fps
- Closes cleanly on QUIT event
- Entry point: `python workspace/{run_id}/main.py`

Validate: `python workspace/{run_id}/main.py` — window must open and close without error.

---

## Phase 3 — Update the plan

After validating Milestone 1:

1. In `PLAN.md`, mark Milestone 1 as `[x]` (done)
2. Add an ADR for any non-obvious architecture decision you made
3. Add a session log entry: `Session 1: ✅ Milestone 1 — empty window`

---

## Signal completion

End your response with:

```
DONE: Milestone 1 complete — empty pygame window opens, runs at 60fps, closes cleanly.
PLAN.md created. Next: Milestone 2 (board grid + static piece).
```
