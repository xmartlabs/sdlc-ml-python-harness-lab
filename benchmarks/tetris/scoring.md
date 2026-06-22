# Tetris scoring

Scoring is **manual first**. Push the four scores below to Langfuse against the run's
`session_id` so runs are comparable in the UI. A numeric scale lets us compare methodologies
even when none of them reaches "perfect Tetris".

## 1. Functional level (0–10) — Langfuse score `tetris_functional_level`

Each level **subsumes the previous one**. Record the highest level reached.

| Level | Meaning |
|-------|---------|
| 0 | No useful code produced |
| 1 | Files generated but it does not run |
| 2 | Runs without crashing |
| 3 | Opens a window |
| 4 | Shows a piece |
| 5 | Piece falls |
| 6 | Lateral movement works |
| 7 | Rotation works |
| 8 | Collisions + lock work |
| 9 | Clears lines + score |
| 10 | Playable: score, game over, reasonable controls, no blocking bugs |

### Functional checklist (record yes/no for the report)

- [ ] Runs / no crash
- [ ] Window opens
- [ ] Piece falls
- [ ] Lateral controls
- [ ] Rotation
- [ ] Collision + lock
- [ ] Line clears
- [ ] Score
- [ ] Game over
- [ ] Serious bugs? (describe)

## 2. Engineering quality — Langfuse scores `scope_adherence` (0–5), `human_interventions` (int)

- **Files changed** — count.
- **Diff size** — added/removed lines.
- **Scope adherence (0–5)** — 5 = touched only what the task needed; 0 = sprawled across the repo.
- **Validation commands run** — which, and did they pass.
- **Tests** — if any, pass/fail.
- **Human interventions** — count of times a human had to unblock/correct/retry.
- **Stability across runs** — does the same cell produce similar results across its 3 reps?

## 3. Operational cost — pulled from Langfuse/LiteLLM by `session_id`

Input tokens · output tokens · USD · wall time · turns · calls · model per call.
Use `scripts/export_langfuse_usage.py --session <session_id>` once automated; manual until then.

## Extra Langfuse scores

- `runnable` (0/1) — does it run at all (level ≥ 2).

## Automation later (optional, not at start)

- Run `python main.py` for ~5s and detect it does not crash → auto `runnable`.
- Optional screenshot for the window-opens check.
- Pure-function tests if the game ends up modular.
Do not automate everything up front. Early on, **consistency beats coverage**.
