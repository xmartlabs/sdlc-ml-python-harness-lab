# Prompt: tetris_naive_v1 (single-shot baseline)

**Variant id:** `tetris_naive_v1`
**Intent:** measure single-shot capability with no incremental loop, no plan/execute split.
Frozen — do not edit between runs of a cell. Changing it creates a new variant id.

---

Build a playable Tetris game in Python using `pygame`.

Requirements:
- A single runnable entry point (`python main.py` opens the game window).
- The 7 standard tetromino pieces, falling automatically.
- Left/right movement, rotation, soft drop.
- Collision detection and piece lock.
- Line clearing with a score counter.
- Game over when pieces reach the top.

Deliver the complete code now. Keep it self-contained and runnable.
