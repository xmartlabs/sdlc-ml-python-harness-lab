# Local gameplay demonstration — November 25, 2026

## Open and play

- Racing: http://localhost:18081/gameplay/racing — WASD/arrows, R recovery, Escape pause.
- Cooking: http://localhost:18081/gameplay/cooking — 1–6 selects station; hold E/Space to work. Pantry → chop → hob → retrieve soup → plate → delivery; bin discards mistakes.
- Choose Challenge ghost on an actual model row. Purple ghost replays recorded actions with independent state/resources; pause/restart share simulation time. SCRIPTED practice is explicitly separate.

User personally confirmed both controls work on Nov25. Video keyboard inputs are automated and labeled, not presented as footage of that user. Video review, sharing/discussion with Marcos and publishing approval remain pending. No new commits/pushes/PRs/contact to Marcos occurred.

## Tasks, clocks and scoring

Racing: one valid lap through all12ordered gates,3s countdown,117s maximum racing time,60Hz simulation/5Hz input. Valid finish time; otherwise unfinished progress/checkpoints. Collisions/off-track/recovery counts remain separate. An inconclusive provider/wall-budget result has no official score; its visible-state progress is labeled a snapshot.

Cooking: fulfill tomato-soup orders in90simulated seconds after3s countdown,10Hz simulation/1Hz input. Walking/preparation/cooking/deadlines use fixed ticks; orders served, expired, incorrect delivery, waste and burns are reported separately. A completed shift with0orders is not a task win. No invented weighted composite score or model success.

Same seed7 and fixed version/build for each game; inference pauses game time. Local installed Gemma4E4B Q4_K_M, Qwen3.5 9B Q4_K_M and Bonsai27B2bit run sequentially, one loaded model at a time. Policy structured-action.v2 requests JSONschema, temperature0, reasoning_effortnone,max512output tokens,context4096,evalbatch128. Actual served config/token usage is preserved in each replay.30s decision/600s episode wall budget; no silent repair or fake actions.

## Recorded evidence

| Model | Game | Result | Score / labeled snapshot | Valid actions | Game time | Inference total | Mean returned-action latency | Ghost UI |
|---|---|---|---|---:|---:|---:|---:|---|
| google/gemma-4-e4b | racing | provider_error | no valid lap; snapshot 18.93%, 2 gates | 138 | 27.6s | 600.23s | 4.349s | passed |
| google/gemma-4-e4b | cooking | completed | 0 orders, 2 expired, 1 burns | 90 | 90s | 313.39s | 3.482s | passed |
| qwen/qwen3.5-9b | racing | provider_error / interrupted recovery | no valid lap; snapshot 2.86%, 0 gates | 30 | 6s | 228.28s | 7.609s | passed |
| qwen/qwen3.5-9b | cooking | completed | 0 orders, 2 expired, 0 burns | 90 | 90s | 485.64s | 5.396s | passed |
| prism-ml/bonsai-27b | racing | provider_error | no valid lap; snapshot 60.27%, 7 gates | 51 | 10.2s | 603.33s | 11.830s | passed |
| prism-ml/bonsai-27b | cooking | provider_error | 0 orders, 1 expired, 0 burns (partial snapshot) | 65 | 65s | 602.80s | 9.274s | passed |

6/6 model/game evidence records currently available. Ghost checks passed: 6/6. A recovered Qwen racing partial preserves all original actions; only an explicit non-model interruption frame was added. Original journal/row backups remain with its artifact. No model rerun was substituted or disguised as success.

Replays: `data/tmp/gameplay-demo/runs/<episode-id>/gameplay/replay.json`; each manifest entry links its exact path. Numerical action/state/event verification uses the recorded engine hash. This verifies consistency, not authenticity of a wholly replaced recording.

- Model manifest: `data/tmp/gameplay-demo/model-evidence.json`
- Actual ghost UI receipt/screenshots: `data/tmp/gameplay-demo/ghost-browser-evidence.json`, `evidence/`
- Shareable video: `data/tmp/gameplay-demo/video/gameplay-evidence-2026-11-25.mp4` (H.264, 3m48s, 5.7MiB). Original WebM is retained. Video receipt records chapters, labels, zero JavaScript errors, inspected frames and SHA-256 checksums. All six verified model ghosts appear.
- Operational run log: `data/tmp/gameplay-demo/model-demo-nov25-retry.log`

## Runtime and recovery

Compose project gameplay-demo; overlay/tmp/gameplay-demo-compose.yml; ports18081web,18009API,18080private-test runtime, all loopback. Source mounted/app, separateDB/artifactsdata/tmp/gameplay-demo. Do not switch branches or restart worker/runtime during active model jobs.

Start: `HARNESS_LAB_LOCAL_SOURCES_DIR="$PWD" HARNESS_LAB_AUTH_DIR=/tmp/gameplay-demo-unused-auth docker compose -p gameplay-demo -f docker-compose.yml -f /tmp/gameplay-demo-compose.yml --profile prod up -d --no-build api worker frontend-prod`.
Stop same project with `--profile prod down`, without deleting data/volumes. Keep it running for review.

Never open live SQLite from the host: API/container access only, or stop API+worker first. Host access previously broke WAL visibility; no completed model record was lost, and the zero-action failed queue attempt is retained in the operational log.

Current branch codex/demo-gameplay,HEAD6e52a1f unchanged. Fetched origin/mainafbde7a; only upstreamdelta68a2a07..afbde7a applied uncommitted to preserve ghost/demo work. PR65 merged into PR64branch, not main; future clean submission needs explicit base reconciliation after video approval. No merges or new branches performed.

Latest combined check:3535Python tests passed,5skipped;33Nodegame/runtime tests; types/security/sync/Dockerbuild passed. Review fixes cover final cooking tick and Watch of short failed recordings. Exact current resumption/ownership details: docs/plans/2026-11-25-gameplay-resume-checkpoint.md.


## Paused November 25 — shutdown and tomorrow's restart

The user requested pausing until tomorrow. All five gameplay-demo containers
(API, worker, frontend, Redis, game-runtime) and their project network were
removed gracefully with compose down, WITHOUT deleting volumes, data or images.
Ports 18081, 18009 and 18080 have no listeners. No task-owned inference drivers,
capture helpers or Playwright browsers remain. Docker had no unrelated running
containers; Docker Desktop was then requested to stop gracefully.

LM Studio was intentionally left open: a newly loaded qwen/qwen3.5-9b instance
(context 40960, parallel 4) is not owned by this demo. Our ownership file is null;
our completed runs used different bounded settings and had already unloaded.
Do not unload or quit that user session without further instruction. Personal
browser windows and Codex were left alone.

All six verified recordings, six ghost checks, database, images, video and
uncommitted source/docs remain intact. User confirmed both controls work.
Next gate remains video review and discussion with Marcos, before publishing.
No development, commits, pushes, branches or PRs were performed during shutdown.

A durable copy of the temporary Compose overlay is now stored at
`data/tmp/gameplay-demo/compose.demo.yml`. To restart tomorrow, start Docker
Desktop, wait for its engine, then run from this repository:

```sh
HARNESS_LAB_LOCAL_SOURCES_DIR="$PWD" HARNESS_LAB_AUTH_DIR=/tmp/gameplay-demo-unused-auth docker compose -p gameplay-demo -f docker-compose.yml -f data/tmp/gameplay-demo/compose.demo.yml --profile prod up -d --no-build api worker frontend-prod
```

The existing images launch both games and their dependencies. Verify health
before reopening http://localhost:18081/gameplay/racing and
http://localhost:18081/gameplay/cooking. Recorded ghosts need no loaded model.
For future deliberate inference, first inspect LM Studio's user-owned models;
do not load another concurrently. Never open the live SQLite database on host.
Video: `data/tmp/gameplay-demo/video/gameplay-evidence-2026-11-25.mp4`.

Final shutdown verification: Docker Desktop exited (no app/backend processes);
all three demo ports are closed; task-owned model ownership is null.
