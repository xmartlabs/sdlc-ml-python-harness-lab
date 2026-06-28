# Tetris — SDLC Mode: Full Build (single autonomous session)

You are building a **complete, playable** Tetris game in Python (pygame) from scratch,
in ONE autonomous session. Work through all 8 milestones — do not stop after Milestone 1.
All files go inside `workspace/{run_id}/`. Entry point: `workspace/{run_id}/main.py`.

---

## Phase 0 — Ground (do this BEFORE planning)

Read for context — do not skip, do not modify these files:

- Governance: `.github/architecture.md`, `.github/standards.md`, `.github/domain-boundaries.md`
- Memory: `memory/context.md`, `memory/learnings.md`

Keep them in mind while you work.

---

## Phase 1 — Plan (before any code)

Create `workspace/{run_id}/PLAN.md`:

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
<!-- One line per milestone completed -->
```

---

## Phase 2 — Implement ALL milestones (one at a time, in order)

Go through milestones 1 → 8 **in order**. For EACH milestone:

1. Implement that milestone fully.
2. **Validate headlessly** (a hanging window or a `timeout` is NOT success):

   ```bash
   BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/{run_id}/main.py; echo "exit=$?"
   ```

   A correct build prints `exit=0`. Do not move to the next milestone until the current
   one validates with `exit=0`.
3. Update `workspace/{run_id}/PLAN.md`:
   - Mark the milestone `[x]` (done)
   - Add an ADR for any non-obvious architecture decision
   - Append a Session Log line: `Milestone N: ✅ <what you built>`

**Smoke mode requirement:** when env `BENCH_SMOKE=1`, `main.py` must run ~120 frames then
exit cleanly with code 0. Normal `python main.py` stays interactive.

---

## Definition of done (target: fully playable, level 10)

- Piece falls; left/right move; rotation; collision + lock; next piece spawns
- Lines clear and score is displayed; game over + restart works
- Runs at 60fps, quits cleanly, no blocking bugs

---

## Signal completion

When every milestone is `[x]` and the game is fully playable, end your response with EXACTLY:

```
ALL MILESTONES COMPLETE
```

If you must stop early for any reason, instead end with `DONE: Milestone N complete.` so the
operator knows where you stopped.
