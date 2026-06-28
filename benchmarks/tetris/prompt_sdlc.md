# Tetris — SDLC Mode: Plan + Execute

You are building a Tetris game in Python (pygame) from scratch.
All files go inside `workspace/{run_id}/`. Entry point: `workspace/{run_id}/main.py`.

---

## Phase 0 — Ground (do this BEFORE planning)

Read for context — do not skip, do not modify these files:

- Governance: `.github/architecture.md`, `.github/standards.md`, `.github/domain-boundaries.md`
- Memory: `memory/context.md`, `memory/learnings.md`

Keep them in mind while you work.

---

## Phase 1 — Plan (before any code)

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

## Phase 2 — Implement Milestone 1 ONLY

After writing PLAN.md, implement **only Milestone 1**:
- Empty pygame window, 300×600 pixels
- Runs at 60fps
- Closes cleanly on QUIT event

**STOP after Milestone 1.** Do NOT start Milestone 2 — later milestones happen in
later sessions, one per session. Implementing more than Milestone 1 now is a failure.

### Validate headlessly (a hanging window or a timeout is NOT success)

So validation is deterministic and does not block on an open window:

- Make `main.py` support a smoke mode: when env `BENCH_SMOKE=1`, run ~120 frames
  then exit cleanly with code 0. Normal `python main.py` stays interactive.
- Run and check the exit code:

  ```bash
  BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/{run_id}/main.py; echo "exit=$?"
  ```

- A correct build prints `exit=0`. Do NOT interpret a `timeout` as success.

---

## Phase 3 — Update the plan

After validating Milestone 1:

1. In `PLAN.md`, mark Milestone 1 as `[x]` (done)
2. Add an ADR for any non-obvious architecture decision you made
3. Add a session log entry: `Session 1: ✅ Milestone 1 — empty window`

---

## Signal completion

End your response with EXACTLY this line (and nothing claiming the game is finished):

```
DONE: Milestone 1 complete — window opens at 60fps, quits cleanly, smoke check exits 0. Next: Milestone 2.
```
