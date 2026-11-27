# racing-play-v1 — a model plays one lap

The agent drives a car around the Tidal Ring circuit through a `race` tool in its
workspace, one 0.5 s decision per `race step` call. Any harness and model the lab
runs can play. See ADR-0102 and `scoring.md`.

## Frozen condition

Seed 7 · 60 Hz · 30 ticks (0.5 s) per decision · 3 s countdown (not interactive) ·
60 s racing horizon (tick 3780) · one lap, 12 checkpoints. Defined once in
`starter/condition.mjs`, shared by the tool and the grader. The engine is the
unchanged `frontend/src/gameplay/racing.mjs`; `starter/racing.mjs` is a byte copy
(pinned by `tests/harness_lab_app/test_racing_play_tool.py`).

## Played or scripted

Both are allowed. `racing_play_mode` is:

- `played · events` — the harness's tool events show at least one `race step`
  shell call per decision (Claude, Codex, Gemini, Antigravity), the tool's own
  call log has one entry per decision with no burst, and the logged controls were
  issued, in order, by those recorded calls.
- `scripted · events` — anything less: a loop or program drove steps, the call
  log is missing or incomplete, or the action log was rewritten after playing.
- `played · timing` / `scripted · timing` — no tool events (OpenCode): five or more
  steps in a row under a second apart is a program. A log with no recorded tool
  calls at all was written without the tool, and is scripted.
- `unknown · none` — no valid episode to label.

The engine is readable in the workspace. An agent that simulates ahead with it but
still issues one `race step` per decision is played.

## Integrity

Each line of `.race/actions.jsonl` carries what the tool showed after that step
(tick, position, speed, heading). The grader replays the log with the lab's own
engine and rejects the episode if any line does not match: an in-place edit is an
invalid episode. A log rewritten wholesale with consistent states replays, but its
controls were never sent by a recorded tool call, so it is labelled scripted.

The grader deletes `data/runs/<id>/racing.json` and the workspace copy before every
grade and rewrites them after; the evaluator reads only the run-level copy, and
only for this case. A missing Node, a replay crash (exit 127) or a replay timeout
(exit 124) is inconclusive, never a failed rung.

The timing label (OpenCode) is the weak signal: a loop with `sleep 1` between
steps reads as played. The badge names its source for that reason.

## Cost and time

An episode is up to 120 decisions (a finishing lap is ~30–45). Measured before this
case on the same condition, through a direct API loop rather than a harness:
Gemma 4 E4B 548 s and Qwen 3.5 9B 919 s for 120 decisions (no lap); Bonsai 27B
376 s and a Codex subscription model 240 s for a finished lap. A harness adds its
own turn overhead; `run_timeout_minutes` is 90. Record measured harness runs below.

| Date | Harness · model | Mode | Outcome | Wall | Notes |
|------|-----------------|------|---------|------|-------|
