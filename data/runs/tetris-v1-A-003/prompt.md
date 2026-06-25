# Benchmark run: tetris-v1-A-003

> All files must be created in `workspace/tetris-v1-A-003/`
> Entry point: `workspace/tetris-v1-A-003/main.py`

---

# Prompt: tetris_incremental_v1 (incremental loop)

**Variant id:** `tetris_incremental_v1`
**Intent:** measure capability when work is sliced into small, independently-verifiable
increments — one validated increment per turn. This is the local-model-friendly path.
Frozen — do not edit between runs of a cell.

---

We are building a playable Tetris game in Python using `pygame`, **one increment at a time**.

Rules of engagement:
- Do **one** increment per turn, then stop and let me validate before the next.
- After each increment the program must still run (`python main.py`).
- Keep edits small and targeted. Do not rewrite whole files.
- Do not jump ahead to later increments.

Increment ladder (follow in order):
1. `main.py` opens an empty pygame window that closes cleanly. It runs.
2. Draw the board grid and a single static piece.
3. Make the piece fall on a timer.
4. Add left/right movement.
5. Add rotation.
6. Add collision detection and piece lock; spawn the next piece.
7. Add line clearing and a score counter.
8. Add game over and a restart key.

Start with increment 1 only. Stop after it runs.
