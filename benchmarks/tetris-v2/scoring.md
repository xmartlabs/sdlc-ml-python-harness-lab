# tetris-v2 scoring

The grade is the shipped test suite: `test_pass_rate` over ten independent
assertions plus a smoke check, all objective. The rubric below exists so the
LLM judge and a human reviewer land on the same ladder, and every level is
tagged with what can establish it — `[check]` an automated check, `[diff]` the
code itself (quoted), `[manual]` only a human running the game.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No useful code produced | [diff] |
| 1 | Files generated but `main` does not import or the smoke check fails | [check] |
| 2 | Smoke check passes: runs headless and exits cleanly | [check] |
| 4 | `Game` contract present; a piece spawns and soft-drops (tests 1-4) | [check] |
| 6 | Walls and rotation are respected (tests 5-6) | [check] |
| 8 | Hard drop locks; stacking never overlaps (tests 7-8) | [check] |
| 9 | Full rows clear and score (test 9) | [check] |
| 10 | The game ends when the stack reaches the top (test 10) and the interactive loop is wired to the same `Game` | [check] + [diff] |

Level 10 is the only one that needs the diff as well as the checks: the tests
prove the logic, and the judge must quote the interactive loop calling
`Game.move/rotate/soft_drop/hard_drop` from key events to award it.
