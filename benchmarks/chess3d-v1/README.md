# chess3d-v1

The first case of the 3D game lane (ADR-0078): build a playable 3D chess game in
**Godot 4.7** — a rules engine that is exactly right, a board played with the mouse,
and a computer opponent. It is meant for frontier models. Smaller models are expected
to stop partway up the ladder, and the 39 checks say how far each one got.

## How it is graded

`grader/grade.py` runs in two parts. The first part takes no score of its own:

1. **harvest** generates this run's probes from a seed derived from the run id:
   random middlegames, random games, and mate-in-one and mate-in-two puzzles found
   by searching them. It loads the agent's project in Godot twice, once for the
   rules (`godot --headless`) and once for the scene (`godot-render`, a software
   OpenGL renderer), and records every raw answer in `<workspace>/.lab-grade/`.
2. **check `<name>`**, 39 times. Each check decides one behaviour against
   [python-chess](https://python-chess.readthedocs.io/), an engine the agent did
   not write.

The probes change with every run because an agent can read the lab's git history:
a committed list of test positions would be one `git show` away. The trap positions
and the standard perft suite are public, and they are hard because every rule has to
be right, not because they are secret.

The scene checks drive the 3D board through its camera. They project
`square_to_world()` to the screen, inject mouse clicks at those pixels, and read the
pieces back from the `chess_pieces` group. One mouse line covers en passant and both
castles. Other checks confirm the board follows a position loaded through the API,
that a click promotes a pawn, that the HUD names check and mate, and that the
computer answers a move made with the mouse.

## Calibration

Measured in the hardened validation lane (no network, every capability dropped,
4 GB of memory) through the worker's real `ValidationService`:

| Workspace | Checks (39) | Notes |
|---|---|---|
| empty | 0 | the harvest fails as the entry-point check |
| weaker build | 24 | no en passant, no castling, no SAN disambiguation, no halfmove clock, first legal move as "AI" |
| reference | 39 | kept outside the repository, in `data/reference/chess3d-v1/` (git-ignored) |

The tamper guard passes only once the grader is committed (ADR-0071).

## Running it

```bash
make images-3d                                   # runner + sandbox images with Godot
make prompts                                     # regenerate prompt_harness.md
make case-import TASK=benchmarks/chess3d-v1/task.yaml
```

Open any finished run and use the **Play** tab: a desktop in your browser with the
game already running against the computer (`play_command` in `task.yaml`).

A full harvest takes about a minute, so validation takes one to two minutes per run.
Per-step reward harvests each step's snapshot again, which costs about a minute per
step.
