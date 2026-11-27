# racing-play-v1 scoring

The grade is the shipped grader: `test_pass_rate` over eight rungs, all objective.
The grader replays `.race/actions.jsonl` with the lab's own engine
(`frontend/src/gameplay/racing.mjs`) and the frozen condition in
`starter/condition.mjs` — never the workspace copies — offline.

| Level | Meaning | Check |
|-------|---------|-------|
| 0 | No replayable episode: no log, unreadable log, a control out of bounds, an action after the race ended, or a line that does not match what the tool showed | [check] |
| 2 | A valid episode of at least one decision | `episode_valid` [check] |
| 4 | A quarter of the lap | `progress_25` [check] |
| 5 | Half the lap | `progress_50` [check] |
| 6 | Three quarters of the lap | `progress_75` [check] |
| 7 | Every checkpoint (12) in order | `all_gates` [check] |
| 8 | A finished lap within 60 s of game time | `finished` [check] |
| 9 | A finished lap with no collisions and no resets | `clean` [check] |
| 10 | A finished lap at or under par, 20.0 s of game time | `fast` [check] |

Par is set to beat the naive scripted controller, which finishes clean in 22.02 s
under this exact condition; two model laps on the same condition took about 15.5
and 16.1 s.

Not part of the grade, reported beside it by the `racing_play` evaluator:
`racing_play_mode` (`played` / `scripted` / `unknown`, with its source),
`racing_progress`, `racing_finish_seconds`, `racing_collisions`, `racing_resets`,
`racing_actions`, `racing_episode_complete`. A session that ended mid-race is
graded on the progress it reached and reported `racing_episode_complete = false`.
A missing Node, a replay crash or a replay timeout is inconclusive, never a failed rung.
