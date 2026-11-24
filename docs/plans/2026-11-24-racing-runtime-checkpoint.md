# PR2 runtime checkpoint — 2026-11-24

## Preserved state and approved requirements

PR1 #59 is verified merged (be36c8d); authoritative origin/main is57a8055,
including later local-model-server fixes. Current branch is
`codex/fixed-racing-runtime`, created from origin/main. Previous
`codex/fixed-racing-game` is preserved. PR2 tracked changes were backed up to
`/tmp/pr2-2026-11-24-before-sync.patch`; untracked files except user notes to
`/tmp/pr2-2026-11-24-untracked.tar`. The stash named 'PR2 racing before merged
upstream sync2026-11-24' is retained after clean application. No commits lost,
no conflicts. CONTRIBUTION_IDEAS.local.md remains untouched and untracked.

Owner-approved addition: ONE slim purpose-built game runtime image, with baked
built browser game assets and executable rules, graphical browser delivery and
headless API/CLI observations/actions/metrics. It must be integrated into actual
Compose/API/worker execution. No Python/coding agents/compilers/model weights or
full desktop/browser needed in final runtime. Existing game-creation tasks stay
unchanged. Graphical mode means WebGL in the client browser; headless model play
needs no renderer. Chromium inside runtime would add unnecessary bloat.

## Chosen integration after read-only background audit

- Multi-stage Dockerfile.game-runtime, pinned Node Alpine base, npm ci builder,
  standalone racing browser entry. Final stage copies only explicit artifacts,
  shared ESM rules and dependency-free Node HTTP server/CLI. Never COPY .env/repo.
- Bounded opaque HTTP sessions expose create/step/observe/delete and health on
  internal Compose network. Same rule source as browser; finite session/tick/body
  budgets and TTL cleanup. Runtime contains no model credentials or persistence.
- Python RacingEnvironment uses configured internal HTTP URL in containers;
  local subprocess remains a host/test fallback. API human re-simulation AND
  Celery model episodes use the runtime. Python retains PR1 recordings/DB/model I/O.
- Vite/nginx proxy only baked static game routes, not internal session endpoints;
  authenticated application /api routes retain model launch/results/save semantics.
- Build/run through Make+Compose, measure actual image size/deps, compare local/HTTP
  simulation parity, run live API→Redis→Celery→runtime proof, bounded installed models.

Docker29.9.3 is reachable with sandbox escalation. Compose currently has no running
services. Need inspect existing local images before building; no host installs.
LM Studio reachability/models still to recheck. Prior model failures are not full
race results. Prior local gates3366passed5skipped/9Node tests must be re-run after
this update. No new runtime code or container validation yet at this checkpoint.

Routine operations CLI/MCP only. No CUA. Xmartlabs SSH alias only; never default
Sephora gh credentials/fork. No global settings, no other repos, no publishing or
merge. Save updated milestones here and in proposed PR description. Do not use
usage reset credits. Starting allowance reported1%/99% remaining this new window.

## First runtime milestone

Safe upstream sync completed without conflicts; all original backups/stash retained.
Added bounded dependency-free HTTP runtime, standalone built browser entry, pinned
multi-stage image, Compose service and actual Python HTTP transport. Vite/nginx
proxy only /game/ baked assets; internal /v1/ remains private. API/worker receive
HARNESS_LAB_GAMEPLAY_RUNTIME_URL and depend on runtime health. Local subprocess is
only a host/test fallback. Header now records environment provenance/engine hash.

First build succeeded. Three runtime protocol tests pass; four Python bridge tests
pass, including remote/local parity and session cleanup. First Node-Alpine final
stage is being tightened further to pinned plain Alpine + Node binary/shared libs
only (no package-manager payload even in base layers). Actual final size pending.
Docker API/worker build in progress; log /tmp/pr2-api-worker-build.log.
LM Studio verified reachable; all three requested models installed, none loaded.
No current live inference or Docker/Celery proof yet. Old model evidence remains
in previous checkpoint and is not a completed race result.

Runtime transport and all four bridge tests pass; added static-source/path isolation
coverage. Dockerfile-specific allowlist excludes .env, data, user notes, git state
and unrelated repo contents from the game build context. Final stage is plain
pinned Alpine plus Node binary and libstdc++/libgcc copied from pinned builder;
compiled game assets are baked under /game, never covered by the lab source mount.
API and worker builds succeeded. Isolated live Compose smoke project is starting
(pr2-gameplay-smoke), separate data/tmp/pr2-runtime-smoke.db,ports18009/18080,
/tmp/pr2-auth credential mount, only this repository as the local sources root.
Normal coding runners/sandboxes and other repositories are not started or changed.

## Live Docker evidence

Final runtime image:195384357 bytes (~186.3MiB); existing worker1629538568 bytes
(~1.52GiB). Static built game payload780KiB. Runtime inspection confirms non-root
user and no npm/npx/Python/cc/gcc/git/Chromium/Claude/OpenCode. Read-only/cap-drop
container calibration finished12gates in22.4333 race seconds,0 impacts/offtrack/reset.
Node24.22.3 runtime source fingerprint:
a1f0a351224739bc1de4d44df5737d84a455bf5733aa6c86731d98726389309b.

Real API→Redis→Celery→HTTP game-runtime pipeline proved with Gemma. Episode
race-1e64ba369cbe426584f1 finished as failed/invalid_action after16.3528s inference,
no lap or checkpoints. API recording matches disk; replay verified against runtime.
Artifact data/runs/race-1e64ba369cbe426584f1/gameplay/replay.json. Test-owned loaded
instance was unloaded. This is successful infrastructure validation, NOT successful
model racing. Qwen and Bonsai are next sequential bounded live pipeline checks.

Executable fingerprint verification now refuses replays against a different build;
a dedicated regression failed before the guard. Server TTL/body/session/tick bounds,
static isolation and remote/local parity are covered. Final gates still pending.
Production frontend built; proxy + baked browser visual check is starting on18081.
Current allowance21% used this window; no resets. Backups/stash remain retained.

## Final verification milestone

PR1 is merged; current branch is on authoritative main57a8055, not stacked on openPR1.
All-three live Docker model checks are complete (do not rerun larger models):
- Gemma race-1e64ba369cbe426584f1: invalid_action,16.3528s inference.
- Qwen race-07dc2999be964fa2b01f: invalid_action,28.0048s inference.
- Bonsai race-df52ce61294146debaa0: timeout,30.0363s inference, no game metrics.
Each API recording matched disk and verified with the HTTP runtime; owned loaded
instances unloaded. None completed a lap. User now requests only Gemma for any
further necessary inference; use deterministic fixtures otherwise.
Human-save API calibration race-bd716686e8124cbeb94b:12gates,22.433333race seconds,
zero impacts/offtrack/reset,0inference. This is calibration, not a person/model run.

`make check` passed3502tests,5skipped,87%coverage, plus format/lint/bandit/mypy.
First run was sandbox-blocked on loopback sockets; rerun with permission passed.
`make check-sync` and `git diff --check` passed. Docker verify target passed types,
9rules and5HTTP tests plus production build. Final image rebuild passed.
Headless isolated Brave/Playwright screenshots showed keyboard drive/countdown/
pause, no JS errors/desktop or mobile horizontal overflow. Review caught terrain
coplanarity and decorative trees on road, now fixed; timeout missing metrics no
longer render NaN/undefined. Read-only agent review caught malformed URL crashing
HTTP listener (fixed with regression) and iframe401 propagation (same-origin bridge,
parent checks origin+source, final production test pending).

Current artifacts/logs: /tmp/pr2-final-check.log, /tmp/pr2-check-sync.log,
/tmp/pr2-runtime-final-verify.log, /tmp/pr2-runtime-final-build.log,
/tmp/pr2-{gemma,qwen,bonsai}-docker-smoke.log, /tmp/pr2-racing-{desktop,driving,mobile}.png.
Temporary smoke Compose overlay /tmp/pr2-gameplay-smoke-compose.yml uses isolated
project pr2-gameplay-smoke, database data/tmp/pr2-runtime-smoke.db, ports18009/18080/
18081, /tmp/pr2-auth and only this repository for local sources. API, worker,
Redis, runtime and frontend-prod currently running. Stop only this test project
and our test-started normal runtime after final checks; don't touch user's services.

Remaining: finish final production visual/auth test +baked NDJSON CLI check,
record exact final image size, reconcile proposed PR body, final diff review and
checkpoint. No commit/push/PR publication. New ADR0100 explains artifact boundary.
Latest allowance35%used/65%remaining; no credits or resets used.

## Review-ready checkpoint

Final baked ARM64 image195401876bytes (~186.35MiB). Docker verify rebuilt after
all visual fixes:9racing tests,5HTTP tests,types and production build passed.
Baked NDJSON accepts reset+step and returns state/events/metrics; baked calibration
finishes22.433333s. `uv lock --check` passes; unrelated greenlet wheel-list churn
was removed. `make check-sync` initially hit restricted uv cache; permitted rerun
passed with no drift. No skill/governance artifacts changed.

Final production app browser check (isolated headless CLI, no CUA): no JS errors,
keyboard/countdown/pause work, absent timeout metrics show placeholders, mobile
has no horizontal overflow. Iframe401 reaches parent once through origin/source
checked message relay. Public /v1/sessions POST returns405 and never reaches the
runtime. Screenshots /tmp/pr2-final-{desktop,driving,mobile}.png; log
/tmp/pr2-final-visual-result.log. Real human driving on a physical keyboard and
AMD64 image execution are not independently verified; automated keyboard was.

Proposed title/body updated in 2026-11-23-fixed-racing-pr-description.md. Current
branch codex/fixed-racing-runtime, base57a8055. All changes remain uncommitted,
unpublished. No author/credentials/global/system settings changed; user notes
untouched. Review-ready implementation, with model capability failures documented.
To launch: make app-up-prod; browse localhost:8080/gameplay/racing.
To verify: make check; make check-sync; make game-runtime-verify;
make game-runtime-build; make game-runtime-check.

Temporary test services will be stopped before final handoff. Recorded artifacts
remain ignored under data/runs/race-*/gameplay and isolated DB in data/tmp.
No further Qwen/Bonsai tests; only Gemma if a new bounded live check is necessary.

Cleanup completed: isolated Compose project (including prod-profile frontend)
stopped/removed and network removed; our separately started default game-runtime
stopped. No other containers were running or stopped. All three tested model
loaded_instances counts are0. LM Studio service itself left running. Images and
ignored recordings/DB retained for review; no user notes or model downloads touched.

Final limitation: this establishes deterministic game/runtime execution and
recording integrity, not competitive LLM driving ability. No model finished a lap;
further policy improvements or larger budgets must be separately evaluated.
