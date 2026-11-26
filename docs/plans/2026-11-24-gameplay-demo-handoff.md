> Current Nov25 state: [resume checkpoint](2026-11-25-gameplay-resume-checkpoint.md) and [demo runbook](2026-11-25-gameplay-demo-runbook.md). Older milestones below are historical.

# Gameplay demo handoff — 2026-11-24

## Current resume checkpoint: review phase

The user resumed at14:50 after reset and prioritized Marcos's reviews before demo
work. All3comments by marcossotoxl were accepted. PR64 security fix597286e is pushed
and GitHub CI passed. PR65 rebased cleanly onto that fix; calibration-reuse/runtime503
fix6e52a1f is pushed with an exact remote lease. Full pre-push and both latest
GitHub CI jobs passed. All three review threads have evidence-based replies and
are resolved. No declines or merges. Latest gate: 3,513 Python tests passed (5 skipped),
19 game/runtime tests passed, both frontend/quality CI jobs green on each head. Original branch heads preserved in review-backup branches.
See [review checkpoint](2026-11-24-gameplay-review-checkpoint.md). With the review phase verified complete, continue the demo-first plan below; the prior stop/pause is historical.
No demo implementation or model run occurred during review. Handoff docs remain
local/uncommitted, user notes untouched. Final review receipt:
`data/tmp/gameplay-review-submission-receipt.json`. All test-owned containers
and sessions removed; no models loaded. Latest allowance22%used/78%remaining. Task-scoped Xmartlabs auth still verified.

## Earlier pause — superseded by the November24 14:50 resume

The earlier checkpoint below was documentation only. Do not start services, inference, builds,
implementation, commits, pushes or another PR now. Resume when the user returns
around **14:30–14:45 America/Bogota on November 24**; the last reported reset was
approximately 14:34. Recheck the actual account meter then: this is an estimate,
not a scheduled restart. No automation or reset-credit purchase/use is requested.
Account allowance is distinct from conversation context; context percentage is unknown.

## Latest priority: functional demonstration before more polish or submissions

The next usage window is for a complete **local, personally observable end-to-end
demo of two simple fixed games**: existing racing and a minimum original cooking
game inspired by cooperative kitchen games. First make racing understandable and
reliable, then build enough cooking to prove the whole loop. Reuse the existing
Docker runtime, text contract, evaluation, recording and ghost approach. High-end
visual polish is deliberately deferred; do not expand into a game engine migration.

The user must personally see/play the games and see recorded **Gemma, Qwen and
Bonsai** ghosts working. Launch the required Docker services at resume and fix
observed gaps. Tests alone are not proof of this experience. Preserve a functional
baseline and evidence first; the user will capture video for Marcos Soto before
further PR submissions. A later five-hour window is for better visuals, controls,
gameplay behavior and selected extra functionality. Cross-machine testing and
simultaneously competing models are future work, not this demo.

## Verified baseline, not a claim that model gameplay is solved

- PR1 #59 merged: text observation/action contracts, deterministic runner, metrics,
  durable replay/journal, LM Studio adapter.
- PR2 [#64](https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/64), ready review,
  author mmerino-xmartlabs, base main, head `codex/fixed-racing-runtime` at `6531346`:
  fixed original Tidal Ring, human/text controls, numerical results and recordings,
  dedicated ~186 MiB Alpine+Node image with baked game assets and headless HTTP/CLI.
- PR3 [#65](https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/65), ready review,
  same author, base PR2 branch, head `codex/racing-ghost-challenges` at `ac00f62`:
  simultaneous translucent, independent recorded ghost; seed/build/cadence checks,
  partial coverage labels, pause/restart, human recording reference provenance.
- Both PRs' final frontend and quality CI checks passed. Local full pre-push:
  3,507 Python tests + 18 game/runtime tests, types/security/sync/build passed. Reverify
  actual checkout/merge/CI state on resume; do not assume it remained unchanged.
- Docker API→Redis→Celery→runtime→DB/replay was exercised. Browser ghost challenge
  was demonstrated headlessly with **SCRIPTED calibration**, not a successful model.
  Calibration lap 22.4333 simulated seconds, zero impacts/off-track/recoveries.
- Actual installed-model attempts: Gemma invalid action after 16.35s inference,
  Qwen invalid after 28.00s, Bonsai timeout after 30.04s. **No model completed a lap.**
  Current failed/countdown-only attempts have no meaningful race ghost and are
  correctly rejected. Cooking is **not built**; cooking gameplay/ghosts are unproven.
- Test containers/networks were stopped/removed and test-owned models unloaded.
  Last verified checkout: PR3 branch, no tracked changes; only untouched untracked
  `CONTRIBUTION_IDEAS.local.md`. This handoff/link are new doc-only edits, not committed.

## First resume actions

1. Read AGENTS.md, governance, memory/context.md and memory/learnings.md. Read this
   handoff and linked checkpoints. Inspect git status/branch/remotes and PR states;
   preserve notes and all work. Do not merge. If the owner merged PR2, carefully
   retarget/rebase PR3 as needed and verify its diff remains ghost-only.
2. Recheck allowance/reset and Docker/LM Studio reachability. Inventory installed/
   loaded models without downloading or disturbing user-owned instances. Establish
   a focused written demo plan/checkpoint and isolated branch when implementation
   begins; keep new cooking/demo work out of the submitted PR2/PR3 review diffs.
3. Use Docker-first launch (`make app-up-prod`, URL
   `http://localhost:8080/gameplay/racing`) after checking existing services/ports,
   mounts and user data. Test stacks previously restricted local-sources mounts to
   this repository and used a separate DB, never sibling repositories. Do not reset
   the user's database or delete recordings. Images need rebuilding after changes.
4. Walk the user through racing start, controls, goal, countdown, finish/DNF,
   numerical score, replay and SCRIPTED practice ghost. Observe the actual UI.
   Then diagnose real model failures before calling the model loop demonstrated.

## Model testing and failure diagnosis

**Exactly one model loaded/running at a time.** Internal smoke uses Gemma first:
`google/gemma-4-e4b` Q4_K_M. Deliberate three-model demo verification, when the host
is available, sequentially uses `qwen/qwen3.5-9b` Q4_K_M and
`prism-ml/bonsai-27b` 2-bit. Recheck inventory/IDs at resume. No downloads or parallel
model loads. Unload only instances this work owns; do not silently replace the
user's loaded model. No live inference is needed to play a recorded ghost.

Inspect actual replies/journals, provider content/reasoning fields, truncation,
output/context limits, observation IDs/action bounds, latency and timeout cause.
Current bounded decisions use 512 output tokens / 30s, and UI runs have a 600s wall
budget. Establish whether failure is transport/schema/prompt/budget or driving
ability. Do not guess from a single timeout or silently repair invalid replies.
Tune only evidenced issues, preserve raw failures and record model/quantization/
configuration/policy provenance. Keep shared scenario/cadence/budgets fair and
explicit; version any rule/scoring changes and invalidate incompatible ghosts.
Never insert calibration controls into a model recording or manufacture a model win.

## Minimum cooking slice to implement locally after racing diagnosis

Proposed implementation defaults to resolve within the approved demo scope:
an original small top-down kitchen using the existing browser/Node stack, a fixed
layout, a few preparation/cook/plate/delivery stations and a small recipe/order
set. Favor a clear functional loop over polished art or many mechanics. Keep
original names/assets/identity; no copied Overcooked art. No host Godot/Blender
installation is presently justified. Review the existing runtime's game-specific
boundaries before generalizing only what the second game needs.

Define the task explicitly: fulfill valid orders in a fixed simulated duration
with a seeded, reproducible order schedule. Specify legal actions, inventory,
station state/timers and mistakes in structured text; provide human controls through
the same rules. Record numerical orders fulfilled, mistakes by defined type,
completion/unfinished outcome, game ticks/time and inference total/decision latency
separately. Freeze recipes/layout/scoring and action cadence for comparisons.
Document exact metrics and weights before collecting comparative results.

Cooking is not currently plug-and-play: RacingEnvironment, API/routes, ghost module
and renderer contain Tidal Ring assumptions. Extend the dedicated baked runtime
and application/recording boundaries coherently, without a speculative framework or
changing the existing game-creation tasks. Persist every attempt and retain engine/
scenario/version provenance; support API/CLI observations/actions/metrics and a
usable graphical launch/play/results path.

A cooking ghost is also a **non-interacting recording**: advance an independent
reference kitchen/actor on the human simulation clock and show understandable
reference order/progress comparison. It must not consume the human's ingredients,
block stations or help fulfill their orders. Choose a readable simple presentation,
not a live cooperative agent. Validate compatibility, freezing/partial coverage,
pause/restart and corruption handling as with racing. No vision support yet.

## Demonstration acceptance and evidence

For each game prove human play, model text observation→valid action→state/metric
updates→persistent results/recording→verified replay→selected model ghost challenge.
The user must see the real result and personally try controls; reserve time for that
interaction. Sequentially record all three models under stated settings, and retain
useful post-start behavior for each named ghost. If a model only emits invalid/empty
responses or times out before moving, it does not satisfy a working ghost demo:
report the exact blocker rather than relabeling a scripted ghost. Meaningful partial
attempts may be shown explicitly as partial/DNF, never as successful laps/orders.

Save an evidence manifest with game/build/seed/task goal/model IDs/settings, episode
IDs, metrics and both clock types, replay paths/verification, failures/limitations,
launch commands and screenshots. Keep scripted reference evidence separately labeled.
The user captures video after this functional baseline is demonstrated. Do not claim
all models solved either game if they did not. Only then prioritize polishing in
another window or prepare further submissions under current user direction.

## Entry points, evidence and constraints

- [Racing usage/runtime/protocol](../racing-gameplay.md)
- [PR2 checkpoint](2026-11-24-racing-runtime-checkpoint.md)
- [PR3 checkpoint](2026-11-24-racing-ghost-challenges.md)
- Final verified publication receipt: `data/tmp/racing-pr-submission-receipt.json`
  (ignored local artifact). Prior model and calibration replays: `data/runs/race-*/gameplay`.
- Core rules/rendering: `frontend/src/gameplay/racing.mjs`, `racingScene.ts`,
  `ghost.mjs`; UI `frontend/src/pages/Racing.tsx`; runtime `game-runtime/`,
  `Dockerfile.game-runtime`; Python `src/harness_lab_app/gameplay/`;
  CLI `scripts/gameplay_episode.py`. Use Make/uv and Docker frontend gate
  `make ci FRONTEND_IN_DOCKER=1`. Run appropriate tests/review for actual new code.
- CLI/MCP for routine operations. Computer use only for genuinely necessary visual
  interpretation; prefer headless screenshot tooling. Never browser GitHub/auth.
- ONLY SSH `github-xmartlabs` / account `mmerino-xmartlabs`. The HTTPS `fork` remote
  and default GitHub token belong to the wrong account. Task-scoped CLI config
  `/tmp/pr2-xmartlabs-gh` was verified, but may expire/disappear. Unset ambient
  GH_TOKEN/GITHUB_TOKEN and verify exact `gh api user` before authenticated writes;
  never fall back to the wrong account. No global/system/SSH/git identity changes,
  no other repositories. Developer sole author; no AI coauthor/generated lines.
- Preserve local notes, existing game-creation cases and submitted PR history.
  Bounded background audits/reviews are allowed; avoid overlapping file edits.
  Keep this checkpoint current for a fresh executor. No further work now.
