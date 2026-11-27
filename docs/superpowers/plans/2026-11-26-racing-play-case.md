# Racing Play Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A lab case `racing-play-v1` where any configured harness/model plays one lap of the Tidal Ring circuit through a `race` CLI tool, graded deterministically from a replayed action log, with each run labelled `played` or `scripted`.

**Architecture:** The case is an ordinary `benchmarks/` directory run by the unchanged `bench_run.py`. A starter seeds the workspace with a Node tool (`race.mjs`) that replays an append-only action log through the shared engine (`frontend/src/gameplay/racing.mjs`) on every call. A hidden Python grader replays the same log offline with the lab's own copy of the engine and emits one validation check per ladder rung plus `metrics.json`. A new evaluator reads `metrics.json` and the run's tool-call trajectory to emit the play-mode label and the raw racing scores.

**Tech Stack:** Node 20 ESM (runner image), Python 3.11 stdlib grader, FastAPI/SQLModel evaluation registry, pytest, React (Results badge).

**Spec:** `docs/superpowers/specs/2026-11-26-racing-play-case-design.md`

## Global Constraints

- Branch `feat/gameplay-lab-case`, worktree `../sdlc-ml-python-harness-lab-gameplay`. The user is the sole author: do **not** commit, push or branch; leave changes staged-free in the worktree. "Commit" steps below mean "checkpoint: run the tests and stop", never `git commit`.
- Frozen condition, copied verbatim: seed `7`, `60` Hz, `30` ticks per action (0.5 s), 3 s non-interactive countdown (180 ticks), `60` s racing horizon ⇒ hard stop at tick `3780`.
- `frontend/src/gameplay/racing.mjs` is **not modified** (the existing `/gameplay/racing` page and stored replays depend on it).
- Grading is offline (`network_mode="none"`) and uses `/app/frontend/src/gameplay/racing.mjs`, never the workspace copy.
- Control bounds, same as `gameplay/api.py::Controls`: `throttle` 0..1, `brake` 0..1, `steer` -1..1, `reset` 0..1; all four required, finite numbers.
- Infrastructure failures (Node missing, grader crash, timeout) are inconclusive (exit 124/126/127 or "command not found"), never a failed rung.
- Humans are out of scope. No changes to `GameplayEpisode`, `/gameplay/racing`, or Mauricio's branch.
- Code artifacts in English; absolute imports; docs in `docs/`. ADR number: next free is `0101` — check `docs/adr/` at write time, another session may have taken it.

## Review Focus

1. **Malformed controls** (`race step 'nope'`, missing key, `steer: 5`, `NaN`) → tool refuses with a clear message and does **not** append to the log. Test in Task 1.
2. **Step after terminal or past tick 3780** → refused, log unchanged, terminal state reported. Test in Task 1.
3. **Tampered log** (agent edits an observation or an action so replay differs from what `race step` printed) → grader `episode_valid` fails conclusively; no progress rung passes. Test in Task 2.
4. **Hand-written log with no `calls.jsonl`** → episode still graded if it replays, labelled `scripted`. Test in Task 3.
5. **Run with no trajectory at all** (harness emitted nothing parsable and no `calls.jsonl` timing signal) → label `unknown`, never `played`. Test in Task 3.

---

### Task 1: The `race` tool and its shared condition module

**Files:**
- Create: `benchmarks/racing-play-v1/starter/race.mjs` — the agent's CLI.
- Create: `benchmarks/racing-play-v1/starter/race` — POSIX shim: `#!/bin/sh\nexec node "$(dirname "$0")/race.mjs" "$@"` (mode 0755).
- Create: `benchmarks/racing-play-v1/starter/condition.mjs` — frozen condition + `replay(actions)`; imported by both tool and grader.
- Create: `benchmarks/racing-play-v1/starter/racing.mjs` — a byte copy of `frontend/src/gameplay/racing.mjs` so the tool works in the workspace (the grader ignores it).
- Test: `tests/harness_lab_app/test_racing_play_tool.py` (drives `node` via `subprocess`; skip if `shutil.which("node")` is None).

**Interfaces:**
- Produces (`condition.mjs`):
  - `export const CONDITION = {seed: 7, tick_rate: 60, ticks_per_action: 30, countdown_ticks: 180, horizon_ticks: 3780, environment: 'tidal-ring', version: 'racing-play.v1'}`
  - `export function replay(engine, actions) -> {state, observations: object[], terminal: boolean, reason: 'finished'|'horizon'|null}` — `engine` is the imported `racing.mjs` module (injected so the grader can pass `/app`'s copy). Starts with `createRace(7)`; first advances through the countdown (180 ticks, zero controls) before the first action; each action steps `min(30, 3780 - tick)` ticks; `terminal` when engine phase is `finished` or tick ≥ 3780.
  - `export function validateControls(value) -> {throttle, brake, steer, reset}` — throws `Error` with a message naming the bad field.
- Produces (workspace files, the contract the grader reads):
  - `.race/actions.jsonl` — one `{"i": n, "controls": {...}}` per accepted step.
  - `.race/calls.jsonl` — one `{"i": n, "t": <Date.now() ms>}` per accepted `race step`.
- CLI: `race rules` prints the rules text; `race state` prints `JSON.stringify(observeRace(state))` plus `{"terminal", "reason", "actions_taken"}`; `race step '<json>'` validates, refuses if terminal (exit 2, message `episode is over: <reason>`), appends to both logs, prints the new observation and that step's events. Bad input → exit 2, nothing appended.

- [ ] **Step 1: Write the failing tests** in `test_racing_play_tool.py`, each in a fresh `tmp_path` copy of `starter/` with `cwd=tmp_path`:
  - `test_state_before_any_step_is_racing_at_tick_180` — `race state` → `phase == "racing"`, `tick == 180`, `actions_taken == 0`.
  - `test_step_advances_30_ticks_and_logs_once` — one valid step → `tick == 210`; `actions.jsonl` and `calls.jsonl` each have 1 line.
  - `test_invalid_controls_are_refused_without_logging` — parametrize over `'nope'`, `'{"throttle":1}'`, `'{"throttle":1,"brake":0,"steer":5,"reset":0}'`, `'{"throttle":NaN,...}'` → returncode 2, both logs absent or empty.
  - `test_horizon_stops_at_tick_3780` — 120 full-brake steps → last `tick == 3780`, `reason == "horizon"`; the 121st step returns 2 and the log stays at 120 lines.
  - `test_replay_is_deterministic` — run the same 20 actions in two tmp dirs → identical final `race state` JSON.
  - `test_starter_engine_matches_frontend_engine` — bytes of `starter/racing.mjs` == bytes of `frontend/src/gameplay/racing.mjs` (guards drift).
- [ ] **Step 2: Run** `uv run pytest tests/harness_lab_app/test_racing_play_tool.py -v` → all FAIL (files missing).
- [ ] **Step 3: Implement** `condition.mjs`, `race.mjs`, the shim, and copy `racing.mjs`. `race.mjs` reconstructs state by `replay(engine, readActions())` on every invocation — no daemon.
- [ ] **Step 4: Run** the same command → all PASS.
- [ ] **Step 5: Checkpoint** — stop; no git commit.

### Task 2: The hidden grader

**Files:**
- Create: `benchmarks/racing-play-v1/grader/grade.py` — stdlib only.
- Create: `benchmarks/racing-play-v1/grader/replay.mjs` — `node replay.mjs <engine_path> <condition_path> <actions_path>` prints `{"observations": [...], "metrics": {...}, "terminal": bool, "reason": str|null, "ticks": int}`. Imports engine and `condition.mjs` by absolute path from argv.
- Test: `tests/harness_lab_app/test_racing_play_grader.py` (load `grade.py` via `importlib` like `test_pr_review_case.py`; skip when no `node`).

**Interfaces:**
- Consumes: Task 1's `CONDITION`, `replay`, `.race/actions.jsonl`, `.race/calls.jsonl`.
- Produces:
  - CLI `python grade.py check <name> <workspace> <run_id>` → exit 0 pass, exit 1 fail, exit 127 + "command not found: node" when Node is absent (classifier marks it inconclusive).
  - Check names, in ladder order: `episode_valid`, `progress_25`, `progress_50`, `progress_75`, `all_gates`, `finished`, `clean`, `fast`.
  - `grade(workspace: Path, engine: Path) -> dict` returning `{"valid": bool, "invalid_reason": str, "metrics": dict, "actions": int, "episode_complete": bool, "ended_by": "finished"|"horizon"|"cut"}`. `"cut"` = log ends before terminal (session ended mid-race).
  - Writes `<workspace>/.lab-grade/racing.json` = the `grade()` dict plus `"calls": [...]` (the `t` values from `calls.jsonl`, `[]` if absent) and `"condition": CONDITION`.
  - `PAR_SECONDS = 24.0` (starting point: scripted calibration finished in 22.0 s; adjust after Task 5 calibration and record in README).
- Rules: `episode_valid` fails when the log is missing, empty, unparsable, has a control out of bounds, or has more actions than the horizon allows. Progress rungs use `metrics.progress` ≥ 0.25/0.5/0.75. `all_gates`: `checkpoints_passed == checkpoints_total` (12). `finished`: `metrics.finished == 1`. `clean`: finished and `collisions == 0` and `resets == 0`. `fast`: finished and `finish_time_seconds <= PAR_SECONDS`. Every rung fails if `episode_valid` fails.
- Engine path: `/app/frontend/src/gameplay/racing.mjs` when it exists, else `<repo>/frontend/src/gameplay/racing.mjs` (tests).

- [ ] **Step 1: Write the failing tests:**
  - `test_missing_log_fails_every_rung` — empty workspace → every check exits 1.
  - `test_scripted_baseline_lap_passes_through_finished` — build `actions.jsonl` by calling the engine's `baselineControls` through a small node helper at 30 ticks/action → `episode_valid`..`finished` exit 0; `racing.json["ended_by"] == "finished"`.
  - `test_cut_episode_is_graded_on_progress` — first 10 baseline actions only → `episode_valid` 0, `finished` 1, `episode_complete is False`, `ended_by == "cut"`.
  - `test_out_of_bounds_action_invalidates_episode` — one action with `steer: 3` → `episode_valid` 1 and `progress_25` 1.
  - `test_grader_uses_lab_engine_not_workspace_copy` — overwrite workspace `racing.mjs` with a file that throws on import → grade still succeeds.
  - `test_no_node_is_inconclusive` — monkeypatch `shutil.which` to None → exit 127 and output contains "command not found".
- [ ] **Step 2: Run** `uv run pytest tests/harness_lab_app/test_racing_play_grader.py -v` → FAIL.
- [ ] **Step 3: Implement** `grade.py` and `replay.mjs`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Checkpoint.**

### Task 3: Play-mode label and racing scores (evaluator)

**Files:**
- Create: `src/harness_lab_app/racing_play.py` — pure functions.
- Modify: `src/harness_lab_app/evaluation.py` — add `RacingPlayEvaluator` (id `racing_play`), register in `EVALUATORS`, add to the `"default"`, `"no_judgement"` and `"outcome_only"` profiles; add `racing_grade: dict | None = None` to `EvaluationContext`.
- Modify: `src/harness_lab_app/score_service.py` (`_context`, ~line 212) — load `<run_dir>/workspace/.lab-grade/racing.json` (fallback newest `steps/step-*.snapshot/.lab-grade/racing.json`, same pattern as `api._bound_keys`) into `racing_grade`.
- Modify: `src/harness_lab_app/scores.py` — register definitions.
- Test: `tests/harness_lab_app/test_racing_play_evaluator.py`.

**Interfaces:**
- Consumes: Task 2's `racing.json`; `trajectory.build(...)` output (`steps[].tool`, `steps[].input`, `source`).
- Produces in `racing_play.py`:
  - `count_race_step_calls(trajectory: dict | None) -> int | None` — counts steps whose `tool` is a shell tool (`Bash`, `shell`, `run_shell_command`, `exec_command` — match case-insensitively on names containing `bash` or `shell` or `exec`) and whose `input` contains `race step`; `None` when `trajectory` is None or its `source != "claude_events"`.
  - `play_mode(actions: int, event_calls: int | None, call_times_ms: list[int]) -> tuple[str, str]` → `(label, source)`:
    - `event_calls is not None`: `("played","events")` if `event_calls >= actions` else `("scripted","events")`.
    - else if `call_times_ms` non-empty: `("scripted","timing")` if any run of ≥5 consecutive calls each <1000 ms apart, else `("played","timing")`.
    - else if `actions > 0`: `("scripted","timing")` (a log with no recorded calls was written without the tool).
    - else `("unknown","none")`.
- Score definitions (all `source="racing_play"`, `version="v1"`): `racing_play_mode` CATEGORICAL/OBSERVATION/NEUTRAL; `racing_progress` NUMERIC 0..1 FACT HIGHER; `racing_finish_seconds` NUMERIC FACT LOWER (only when finished); `racing_collisions` NUMERIC FACT LOWER; `racing_resets` NUMERIC FACT LOWER; `racing_actions` NUMERIC FACT NEUTRAL; `racing_episode_complete` BOOLEAN FACT NEUTRAL.
- Evaluator emits nothing when `racing_grade` is None (other cases untouched). `racing_play_mode` reason = `"<label> · <source>"`; `provenance_detail` carries `actions`, `event_calls`, `ended_by`.

Note: the trajectory is clipped to 120 steps (`trajectory.MAX_STEPS`) but `step_count` is the full count; `count_race_step_calls` must count over the **unclipped** parse. Add a `race_step_calls` field computed inside `trajectory.build` over `parsed["steps"]` (before `_clip`) and have `count_race_step_calls` read it.

- [ ] **Step 1: Write the failing tests:**
  - `test_play_mode_matrix` — parametrize: `(120, 120, [])→played/events`, `(120, 3, [])→scripted/events`, `(120, None, [0,5000,10000,...120 items])→played/timing`, `(120, None, [0,10,20,30,40,50]+...)→scripted/timing`, `(120, None, [])→scripted/timing`, `(0, None, [])→unknown/none`.
  - `test_count_race_step_calls_counts_unclipped` — trajectory built from 200 synthetic claude `tool_use` Bash events with `command: "race step '{...}'"` → 200.
  - `test_opencode_trajectory_gives_none` — `source == "opencode_logs"` → `None`.
  - `test_evaluator_silent_without_racing_grade` — any other context → `[]`.
  - `test_evaluator_emits_scores` — context with a finished `racing_grade` and played trajectory → names include `racing_play_mode`, `racing_finish_seconds`; every value passes `ScoreValue.as_row()` (registry check).
- [ ] **Step 2: Run** `uv run pytest tests/harness_lab_app/test_racing_play_evaluator.py -v` → FAIL.
- [ ] **Step 3: Implement** the module, trajectory field, evaluator, registry entries and context loading.
- [ ] **Step 4: Run** the new test file plus `uv run pytest tests/harness_lab_app -k "evaluation or score or trajectory" -q` → PASS (no regressions).
- [ ] **Step 5: Checkpoint.**

### Task 4: The case definition, prompts and docs

**Files:**
- Create: `benchmarks/racing-play-v1/task.yaml`
- Create: `benchmarks/racing-play-v1/prompt_default.md`; generate `prompt_harness.md` with `make prompts`.
- Create: `benchmarks/racing-play-v1/scoring.md`, `benchmarks/racing-play-v1/README.md`.
- Create: `docs/adr/0101-a-model-plays-a-fixed-game-as-a-lab-case.md` (or next free number) via `/adr`.
- Modify: `docs/guide-*.md` that lists cases (find with `grep -rl "racing3d-v1" docs/ | head`) — add one line.
- Test: `tests/harness_lab_app/test_racing_play_case.py`.

**Interfaces:**
- `task.yaml` keys: `task_id: racing-play-v1`, `category: game-play` (falls through to `build_from_scratch` type in `case_service._case_type_from_task`; acceptable — do not add a CaseType), `tags: [play, racing, deterministic, graded, hidden-grader, node]`, `subject_repo`/`base_commit` copied from tetris-v2, `starter: benchmarks/racing-play-v1/starter`, `deliverable: report`, `max_steps: 1`, `entry_point: .race/actions.jsonl`, `run_timeout_minutes: 90`, `agent_hidden: [benchmarks/racing-play-v1/grader/, benchmarks/racing-play-v1/scoring.md, benchmarks/racing-play-v1/README.md]`, `validation: objective`, `files_forbidden: [benchmarks/racing-play-v1/grader/grade.py, benchmarks/racing-play-v1/grader/replay.mjs]`, `prompts: {default, harness}`, `scoring_rubric`, `target_level: 10`, `complexity: medium`.
- `validation_commands`: first a tamper guard over `grader/` and `starter/` (same form as pr-review-v1), then one `cd {workspace} && timeout 120 python /app/benchmarks/racing-play-v1/grader/grade.py check <name> "$PWD" {run_id}` per Task 2 check name, in ladder order.
- `success_criteria`: level_0 no valid episode; level_2 valid episode; level_4 25%; level_5 50%; level_6 75%; level_7 all gates; level_8 finished; level_9 clean; level_10 fast.
- Prompt content (exact obligations): the rules (`race rules` text), how to call `race state` / `race step`, that game time only advances on `race step`, the goal "finish one valid lap as fast as you can, cleanly", and this sentence verbatim: "You may also write a program that calls `race step` for you; that is allowed, and the run will be labelled as scripted rather than played." No mention of the grader, par time or ladder.

- [ ] **Step 1: Write the failing tests:**
  - `test_case_loads_through_case_service` — `CaseService` sync/load yields `racing-play-v1` with `starter_dir` set and the 8 checks + tamper guard.
  - `test_validation_commands_match_grader_checks` — check names parsed from `task.yaml` == `grade.CHECKS` in order.
  - `test_grader_is_hidden_from_agent` — `agent_hidden` contains `grader/`.
  - `test_prompt_states_scripting_is_allowed` — the verbatim sentence is in `prompt_default.md` and `prompt_harness.md`.
- [ ] **Step 2: Run** `uv run pytest tests/harness_lab_app/test_racing_play_case.py -v` → FAIL.
- [ ] **Step 3: Write** the files; `make prompts`; write ADR and README (README records the frozen condition, cost expectations, the played/scripted rule and that look-ahead with the readable engine still counts as played).
- [ ] **Step 4: Run** → PASS; then `make check-sync` (prompts/skills mirrors current).
- [ ] **Step 5: Checkpoint.**

### Task 5: Results badge and live end-to-end

**Files:**
- Modify: the run-detail / Results component that renders score rows (find with `grep -rln "skill_invoked" frontend/src | head`) — show `racing_play_mode` as a badge (`Played · events`, `Scripted · timing`, `Unknown`) and `racing_finish_seconds` / `racing_progress` in the run summary when present.
- Test: the component's existing test file (add one case), `cd frontend && npm test -- <file>`.

- [ ] **Step 1: Write the failing frontend test** — rendering a run whose scores include `racing_play_mode = "scripted"` with reason `"scripted · events"` shows text `Scripted · events`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Live check (manual, recorded in README):** `make app-up`; create one run of `racing-play-v1` from the Models flow with a Claude harness and one with OpenCode → LM Studio (small model loaded by the user). Confirm: the run appears in Runs; validation shows the 8 rungs; Results shows the badge and finish/progress; `.lab-grade/racing.json` exists. Record wall time, tokens/cost and outcome per run in `benchmarks/racing-play-v1/README.md`; set `PAR_SECONDS` from what was observed if 24.0 is clearly wrong.
- [ ] **Step 6: Gate** — `make check` and `make check-sync` pass. Leave all changes uncommitted for the user.
