# PR3: recorded ghost challenges — approved execution plan

Base: pushed PR2 codex/fixed-racing-runtime at6531346; separate branch
codex/racing-ghost-challenges. PR2 publication awaits scoped gh login; never mutate
its head with ghost work. Create PR3 against PR2 branch while dependency is open.

Scope: simultaneous human race and translucent non-interacting recorded car,
selected model identity, explicit partial/failed coverage, progress/time comparison,
fixed shared seed/build/cadence and simulation clock. Keep solo playback. No live
agents, inference, cooking, vision, physics changes or existing task changes.

1. Test first: independent ghost timeline synchronizes per tick, freezes after
   coverage, restarts, and cannot mutate human state. Validate recording shape,
   build/track/seed/cadence and useful movement; reject corrupt/incompatible data.
2. Authenticated reference endpoint verifies stored replay against actual runtime;
   explicit scripted calibration reference for demo/testing, never model success.
   Persist source reference ID/provenance with human challenge recording.
3. Extend baked game rendering/UI: selected reference, translucent car, clear
   distance/progress and finish-time comparison; pause/restart both together;
   recording stops at its known coverage and never extrapolates a failed model.
4. Meaningful Python/Node/HTTP tests, Docker/frontend/full gates, headless visual
   interaction, bounded read-only review. No LLM resources needed.
5. Document, commit/push separately; correct-account CLI only, no global changes.

Status at start: not implemented; PR2 full pre-push passed. Model attempts from
PR2 have no meaningful post-countdown movement and must not be offered as laps.
Task-scoped gh auth session4471/config /tmp/pr2-xmartlabs-gh is pending user step.
Last usage46%used/54%remaining. Save exact outcomes/commands before limit.

## First implemented checkpoint

Ghost timeline+compatibility tests3 pass. Python service tests cover full scripted
reference, persisted human link, missing/corrupt fingerprint/state, wrong seed and
real partial/timeout eligibility. API uses full PR1 replay verification before
returning reference; browser independently verifies frames/build/cadence. No physics
source changes. Ghost has its own state and only advances to human simulation ticks;
partial coverage freezes with explicit label. Scripted reference endpoint is labeled
calibration, never a model benchmark. Selected source stored in policy_json and replay
provenance on human save. Final-time comparison uses recorded target even if human
finishes first. Renderer uses a translucent purple car without collision coupling.

Read-only review found two async UI transition races: pending selection could reset
a new race, and failed Watch could clear live challenge attribution. Fixed by locking
conflicting transitions while busy/active and moving mutations after successful load.
Final gates rebuilding; isolated pr3-gameplay-smoke Compose DB data/tmp/pr3-ghost-smoke.db
ports18009/18080/18081. No LLM loaded or inference. PR2 head remains6531346 and scoped
gh auth session4471 still pending. Usage51%used/49%remaining at milestone.

## Validated milestone

Full `make check`:3507passed,5skipped,87%coverage; format/lint/security/mypy green.
`make check-sync` green. Docker verify:13racing+ghost tests and5HTTP tests, frontend
types/build green. Browser found legitimate JSON float round-trip drift at tick564
(upcoming_turn differed by5e-17); browser comparison now permits only1e-9 absolute
number drift, server full replay verification remains exact. Regression proves
small round-trip drift accepted and meaningful corruption rejected.

Isolated production-proxy/headless controlled-animation test passed: scripted
translucent car visibly alongside human, no JS errors, synchronized pause and
restart, natural117race-second human DNF, saved/verified reference metadata.
Human calibration-challenge smoke race-c587c59e66404d2f9e78 references
race-3ad888ad5d1f4e03a15c. This is scripted reference+stationary human test input,
not model performance. Screenshot /tmp/pr3-ghost-challenge.png and log
/tmp/pr3-ghost-visual.log. Real countdown/render/pause checked with controlled
animation frames; restart used actual DOM click event because animation was
intentionally controlled by test. No models loaded/inference. PR2 unchanged.

Current usage62%used/38%remaining. Scoped gh session4471 still pending despite
user browser completion report; `gh api user` in task config says login required.
Do not use default token. Remaining: transition-lock/mobile/partial visual checks,
cleanup test project, finalreview/docs, commit/push PR3; publish PR2/PR3 only when
correct CLI identity verified. PR3 base must be codex/fixed-racing-runtime, not main
until PR2 merged. Pending authentication is the only publishing blocker so far.

## Submission checkpoint

Fresh task-scoped gh flow completed and API user verified EXACT mmerino-xmartlabs.
PR2 #64 is OPEN/ready review: https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/64
base main,head codex/fixed-racing-runtime6531346. Initial checks rollup empty, not
claimed green. No global auth/default-token changes. PR3 will target that branch.

Transition-lock and mobile checks passed; mobile pause overlay exposed HUD overlap,
fixed by moving paused/end summary into the reference panel and showing in-game HUD
only during play. Docker gate reverified final JSX. Full Python gate unchanged green.
Final headless evidence in /tmp/pr3-ghost-{visual,locks}.log. Source/reference metadata
saved through actual API; no inference. Ghost selection errors stay useful; mutable
playback state changes only after successful request. Independent review P2s resolved.

Ready to commit/push all PR3 files, excluding local user notes/artifacts. Proposed
body in 2026-11-24-racing-ghost-pr-description.md. No PR4 work or physics/task edits.

## Published handoff

PR2: https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/64
main ← codex/fixed-racing-runtime6531346. GitHub quality+frontend jobs PASSED.
PR3: https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/65
codex/fixed-racing-runtime ← codex/racing-ghost-challenges4db336c (implementation).
Both OPEN,non-draft/ready-review,author mmerino-xmartlabs verified via scoped CLI.
PR3 full pre-push gate passed; GitHub frontend passed,quality running at checkpoint.
Check the PR's current check rollup for final CI; don't treat pending as green.

Final smoke rerun passed pause/restart/save metadata/transition locks/mobile after
readability fix. Final saved human test race-f0e4fcef84e74139a990 references scripted
race-2451fc02e2b34d668eb0; human DNF117s,no fabricated model success. Partial coverage
is proven by Node freeze tests and Python real timeout fixtures, not a completed
model lap. Isolated test project/network removed; recordings remain ignored under
data/runs. No models were loaded. User notes remain untouched/untracked.

No merges performed. PR3 depends on PR2; once #64 merges, retarget #65 to main and
verify its diff stays ghost-only. PR4 cooking/vision not started. No global/system
or other-repository changes; task auth remains under /tmp/pr2-xmartlabs-gh. Future
CLI commands must unset ambient GH/GITHUB tokens, use that config and verify user.
Runtime image :pr3 is available for local review; `make app-up-prod` rebuilds the
normal image from the checked-out branch. Last account milestone62%used,25%weekly.
