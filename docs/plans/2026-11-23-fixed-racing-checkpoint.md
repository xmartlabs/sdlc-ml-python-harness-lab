> Historical Nov23 checkpoint. Current base, runtime integration and verified outcomes: [Nov24 checkpoint](2026-11-24-racing-runtime-checkpoint.md).

# PR2 checkpoint — 2026-11-23

Branch: `codex/fixed-racing-game`, stacked on PR1 commit `596c640`.
PR1 #59 verified open/unmerged at the start; upstream `main` remains `cfd93a0`.
All PR2 changes are uncommitted. Preserve `CONTRIBUTION_IDEAS.local.md` untouched.
Computer use is OFF. Use CLI/MCP only. SSH route `github-xmartlabs` authenticates
`mmerino-xmartlabs`; never use the existing Sephora HTTPS fork or default gh token.
No PR2 publication performed or assumed authorized.

## Implemented so far

- Shared authored Node20/browser ESM core: `frontend/src/gameplay/racing.mjs`,
  `.d.mts`, `.test.mjs`. Original Tidal Ring,293.94m,12 gates,60Hz,3s countdown,
 120s total cap. Same numeric throttle/brake/steer/reset controls for both actors.
  Nine Node tests pass. Calibration finishes in22.4333 race seconds with no impacts,
 offtrack or resets. Reverse-start progress-farming regression fixed by agent.
- `scripts/racing_bridge.mjs` and Python `gameplay/racing.py`: PR1 Environment
  port, persistent NDJSON Node subprocess, baseline controller and local fixed
 countdown noops for models. CLI extended with `--environment racing`.
- New SQLModel `GameplayEpisode`; `gameplay/racing_service.py`, `api.py`, `tasks.py`
  support persistent identity/results/artifacts, human-control resimulation, loaded
 model queue launch, cancellation and600s wall budget. Routers/imports wired.
- Original Three.js scene, keyboard game page/HUD/start/pause/end/model launch/results
  under `frontend/src/pages/Racing.*` and `gameplay/racingScene.ts`, lazy app route
 `/gameplay/racing`. Three.js/types added to frontend lock. These are not validated yet.

## Validation at this checkpoint

- PR1 prior baseline: make check3352 passed/7 skipped; sync, frontend and prepush green.
- Shared simulator:9 Node tests green; calibration complete.
- New Python bridge/service tests initially failed missing modules (expected red),
 then2 failed/4 passed because baseline controls omitted reset and metrics lacked
 finished flag. Agent has now fixed these core compatibility issues; rerun pending.
- New API routes, UI compile/build, browser rendering, model racing smokes,
 full make gates and independent review are PENDING. Do not claim PR2 complete.

## Next steps

1. Run focused Python and frontend type checks; fix integration errors. Add visible
 Benchmarks hub link to racing. Ensure core/action cadence/human save match exactly.
2. Meaningful API route/auth/replay tests; Node tests included frontend gate.
3. Headless CLI visual/keyboard/save smoke using isolated browser profile (no CUA).
4. Bounded racing attempts on installed Gemma4E4B/Qwen3.5-9B/Bonsai27B, one at time,
 no downloads, no claims of completed races unless observed. LM Studio was up on
 localhost:1234; last PR1 inventory had no loaded instances. No PR2 inference yet.
5. Review and full gates; guide/ADR/update this checkpoint with exact evidence and
 remaining constraints. Prepare proposed PR title/body, no publishing assumption.

## Runtime and allowance

Temporary Node24 is `/tmp/gameplay-node24-runtime/node-v24.22.3-darwin-arm64/bin`.
Prefix PATH for commands; no global installs or config changes.
`PYTHONPATH=$PWD/src .venv/bin/pytest tests/harness_lab_app/gameplay --no-cov`
`node --test frontend/src/gameplay/racing.test.mjs`
`make check` with `PYTEST_XDIST_AUTO_NUM_WORKERS=4`; `make check-sync`;
`make check-frontend`. Existing uv cache and socket tests need tool sandbox escalation.
`make gameplay-episode` is PR1 fixture; racing CLI:
`PYTHONPATH=src .venv/bin/python scripts/gameplay_episode.py --environment racing --episode-id <new-id>`.
Quota reported77% used/23% remaining five-hour,weekly12%. Do not use reset credits.
Save another concrete checkpoint before exhaustion; incomplete work stays incomplete.

## Verification update (after review)

-33 gameplay Python tests passed before final review, then36 passed after orphan
 status and clock regressions. A final cancellation-race regression has been added.
- `make check` passed3363 tests/5 skipped before final reviewed corrections. Final
 `make ci` is now running; log `/tmp/racing-pr2-ci.log`, tool session51582.
- `make check-frontend` passed types,9 Node tests and Vite build before final fixes.
- Headless CLI screenshot/keyboard checks passed desktop+390px mobile, drive after
 countdown, pause, no JS errors or horizontal overflow. Artifacts `/tmp/racing-ready.png`,
 `/tmp/racing-paused.png`, `/tmp/racing-mobile.png`. Harness `/tmp/racing-visual/check.mjs`
 uses an isolated headless Brave executable, NEVER user browser session/computer control.
 Vite is tool session19368 on localhost5173; stop with Ctrl-C after final validation.
- Reviewed P2 fixes: cancellation refresh at finalization, key release/focus pause,
 orphaned running rows fail closed after660s, final action interval consumes transport
 ticks while preserving exact crossing time. Final focused/full validation pending.
- Model attempts: Gemma accepted one action then invalid reply; Qwen timed out20s;
 Bonsai timed out20s with final corrected latency accounting. No local model completed
 a lap. Early Gemma/Qwen artifacts predate countdown-latency correction and should not
 be compared using their old medians. Final one-decision reruns are running serially
 for Gemma/Qwen (tool session82359),20s cap each. Only test-created instances unload.
- Rendering shadow acne was found by screenshot inspection and corrected; final
 screenshot recheck still needed after latest grass shadow change.
- API/service are validated via focused tests, not a live deployed Docker/Celery
 end-to-end run. Need final docs/PR body and final checkpoint with exact outcomes.

## Final model evidence

Final corrected-instrument recordings (all verified by their smoke script):
- Gemma: invalid first driving action,16.825s inference; no lap.
  `data/runs/racing-smoke-gemma-4-e4b-1795449709/gameplay/replay.json`
- Qwen:20.004s timeout before first driving action; no lap.
  `data/runs/racing-smoke-qwen3-5-9b-1795449729/gameplay/replay.json`
- Bonsai:20.004s timeout before first driving action; no lap.
  `data/runs/racing-smoke-bonsai-27b-1795449666/gameplay/replay.json`
Each test-created instance was unloaded. No downloads, no global configuration or
identity changes, no other repositories touched. Final PR2 diff is uncommitted.
Proposed PR text: `docs/plans/2026-11-23-fixed-racing-pr-description.md`.

## Handoff status

Final `make ci` PASSED (3366 Python tests,5 skipped) after all reviewed code fixes (Python/static/security,
skill synchronization, TypeScript,9 Node tests and production Vite build).
Final headless desktop/mobile/rendering/keyboard checks PASSED, no JS errors or
horizontal overflow. Full gate log: `/tmp/racing-pr2-ci.log`.
Known non-failing warnings: existing deprecations and large frontend bundles.
Visual artifacts are the final rerendered `/tmp/racing-*.png` files.
The temporary Vite server has been stopped. No computer-control calls were used.

PR2 is implemented and locally verified, but operational deployment via live
Docker/Redis/Celery is unverified, and bounded local models did not finish a lap.
No PR2 commit, push or PR submission has occurred. All work remains reviewable on
`codex/fixed-racing-game`; compare against596c640. Keep user notes excluded.
Next continuation: inspect diff/review any remaining operational limits, optionally
validate a live queue launch when its services are available, and obtain explicit
PR2 publishing authorization before commit/push. Use only Xmartlabs SSH routing;
gh still lacks correct-account token, so never fall back to the Sephora account.
Last measured quota94% used in five-hour window; no reset credits used.

Final renderer cleanup replaces Three.js removed PCFSoftShadowMap alias with PCFShadowMap, matching the actual runtime fallback. Frontend gate re-run below; Python code unchanged.

Final frontend gate after shadow alias cleanup PASSED (types,9 Node tests,build).
Log: `/tmp/racing-pr2-final-frontend.log`. Final allowance reading96% used/4%
remaining five-hour,15% weekly. No resets used. Vite stopped. No PR2 publication.
