# Tidal Ring: fixed racing gameplay

PR2 builds on the text episode instrument in PR1 (#59). It adds an original fixed
arcade time trial, not another task asking an agent to generate game code. Existing
`racing3d-v1`, chess and city creation tasks remain unchanged. Human-versus-model
interactive ghost challenges are PR3; cooking and vision are later.

## Play and inspect runs

Start the normal Docker stack with `make app-up` (development) or `make app-up-prod`
(nginx production). Both build and start the dedicated `game-runtime` image along
with API, serial Celery worker, Redis and frontend. Open **Benchmarks → Tidal Ring
time trial**, or `/gameplay/racing`. The page embeds the baked game build at `/game/`.
Three.js renders original geometry in the user's WebGL browser; headless control
needs no renderer. No host Node/npm, Godot, Blender, GPU service or external assets
are required for this Docker path.

Click Start, then drive with WASD/arrows, Space/S brake, R recovery and Escape pause.
The canvas has keyboard focus; changing tabs/windows pauses. Inputs are sampled at
5Hz and held for12 fixed60Hz ticks, the same cadence models receive. Rendering is
separate and may interpolate the camera. Slow rendering stretches wall time rather
than inventing simulation time. The three-second countdown cannot influence racing.

Finish the lap or reach the time cap to save a human recording. The server accepts
only bounded control intervals, re-simulates the entire attempt, and computes the
result itself; client scores, poses and checkpoints are never trusted. Human input
recordings have zero inference time. Saved model/human runs can be watched alone or
downloaded as JSON. Solo playback is not a human-versus-model ghost challenge.

For model attempts, load a model in LM Studio, refresh the model list, choose it and
click Run model. Discovery/inference use `LMSTUDIO_BASE_URL` from Settings/environment,
with the existing Docker host rewrite. The app never loads/downloads models. Runs
queue on the existing serial worker lane. Cancel is explicit; in-flight inference
can take up to30s to return. A600s wall budget bounds each queued attempt. A worker
crash may leave a running record; Cancel marks it cancelled, and a new attempt gets
a new identity rather than silently continuing an unknown state.

## Rules and measurements

Tidal Ring v1 is a293.94m closed course with12 ordered directional gates. The car
uses deterministic kinematic handling with bounded acceleration, braking, steering,
off-road drag and barriers. Recovery returns to the last earned gate without gaining
progress; elapsed time and the reset count still advance. No reverse gear, opponents,
items or collision ghosts exist. Seeds are recorded for comparison provenance; the
v1 track layout is intentionally identical across seeds.

The simulation caps at7200 ticks including180 countdown ticks (117 racing seconds).
Finish time is measured at the actual crossing tick after countdown, not the next
model decision boundary. Unfinished attempts report progress below1 and omit a
finish time. Every result records checkpoints, impacts (debounced during scrapes),
off-track seconds and recoveries. `finished=1` identifies a valid lap; an episode's
`completed` state alone does not mean its race was finished.

Model observations expose position, heading, speed, signed lateral offset, road
width, lookahead point, heading error, upcoming turn, gates and progress. Positive
steer increases heading toward+z; use the sign of heading_error to turn toward the
lookahead. No grader, hidden driver or tool access is supplied. The calibration
path follower is a separately labeled instrument check, not model performance.

PR1's observation IDs, action bounds and durable replay verification are reused.
Fixed countdown noops carry `model_call=false` and are excluded from model latency
aggregates so scripted intervals cannot make a slow model look fast. Model replies
remain bounded to512 output tokens; empty/truncated/invalid actions stop rather
than receive invisible corrective retries. Timeouts/provider errors are inconclusive.

## Architecture and commands

`frontend/src/gameplay/racing.mjs` is the single physics/rules implementation. The
browser build imports it directly; containerized `RacingEnvironment` calls the
private runtime HTTP API configured by `HARNESS_LAB_GAMEPLAY_RUNTIME_URL`. Both human
save validation and Celery episodes use it. A local Node subprocess over NDJSON
remains an explicit host/test fallback. The `.d.mts` boundary types the renderer.
Node tests exercise rules and HTTP parity, bounds, cleanup and static isolation.
Python integration tests cover recording, persistence, payload validation and auth.

`Dockerfile.game-runtime` uses a pinned multi-stage build and locked `npm ci`.
Only Node, required shared libraries, rules, HTTP/CLI entry points and compiled game
assets enter the pinned Alpine final image. No npm, Python, compilers, coding agents,
model weights, Chromium or desktop are included. Compose gives it a read-only root,
non-root user, dropped capabilities, 192MiB memory, one CPU and32 processes. There
are no runtime source/data mounts or public ports. Nginx/Vite proxy `/game/` assets;
the internal `/v1/` endpoints are never forwarded. An image rebuild is required after
rules/art changes. See [ADR-0100](adr/0100-fixed-games-run-as-baked-builds-in-a-slim-runtime.md).

The measured ARM64 runtime is195MB decimal (~186MiB), versus1.63GB (~1.52GiB) for the
existing worker. Its static build is780KiB and Node dominates its size. Idle runtime
memory was22.3MiB of192MiB. Manifests support ARM64/AMD64; AMD64 was not tested here.
Fingerprinted engine/runtime provenance is recorded, and verification rejects an
engine mismatch rather than silently accepting a different build.

`GameplayEpisode` rows are the identity/status/results source of truth, kept separate
from coding-agent `Run`/leaderboards. Recordings live at
`data/runs/<race-id>/gameplay/`; user-visible API routes are session-gated. New tables
are created by the existing SQLModel startup path. The worker runs trusted game code,
not model-supplied programs. No additional broker or model server is introduced.

```bash
make game-runtime-build     # Build entirely inside Docker; no host npm.
make ci FRONTEND_IN_DOCKER=1 # Full Python/sync gate + container frontend verification.
make game-runtime-verify    # Builder: types, rules, HTTP tests, production UI build.
make game-runtime-check     # Headless calibration executable; numerical metrics.
make app-up-prod            # Graphical mode: http://localhost:8080/gameplay/racing

# Record + verify from the worker, using its private HTTP runtime.
docker compose exec worker /opt/venv/bin/python scripts/gameplay_episode.py --environment racing --episode-id race-calibration
docker compose exec worker /opt/venv/bin/python scripts/gameplay_episode.py --environment racing --verify data/runs/race-calibration/gameplay/replay.json
# Already-loaded Gemma; new episode ID each time, small explicit smoke budget.
docker compose exec worker /opt/venv/bin/python scripts/gameplay_episode.py --environment racing --episode-id race-model-smoke --model google/gemma-4-e4b --max-ticks 216 --timeout 20

# Standalone headless NDJSON executable (reset, observe/step, metrics).
printf '%s\n' '{"op":"reset","seed":7}' '{"op":"step","controls":{"throttle":1,"brake":0,"steer":0,"reset":0},"ticks":12}' | docker compose run --rm -T --no-deps game-runtime node game-runtime/cli.mjs stdio
```

CLI recordings do not automatically create database rows; launch from the racing
page to see a persistent queued episode in that page's results. The fixture remains
available with the original CLI defaults. Same rules/version/seed/cadence are required
for comparisons. Browser replays resimulate actions rather than showing stored claims;
future ghost rendering/retention is intentionally separate.


## Private runtime protocol

HTTP JSON `game-runtime.v1` is a trusted internal service, not a public model tool.
`GET /health` reports environment/version, engine SHA-256, Node version and session
count. `POST /v1/sessions` accepts `{"seed":7}` and returns an opaque `session_id`,
state, metrics, terminal flag and fingerprint. `GET /v1/sessions/:id` observes;
`POST /v1/sessions/:id/step` accepts `{"controls":{"throttle":1,"brake":0,"steer":0,"reset":0},"ticks":12}`
and returns state/events/metrics/terminal. `DELETE /v1/sessions/:id` releases it.
The internal `/baseline` operation is a labeled calibration helper, never exposed
to evaluated models. Models receive only the PR1 observation/action schema.

Controls must satisfy the shared numerical bounds; intervals are1..240ticks,
requests at most8KiB, concurrent sessions at most32 and inactive sessions expire
in15minutes. The Python client limits each HTTP exchange to10seconds and closes
sessions on reset/exit. Restart/expiry fails an episode instead of silently resetting.
Persistence and credentials belong to the lab, not this ephemeral runtime. HTTP and
NDJSON cover the requested communication path without adding an unused MCP daemon.

## Verified local evidence (2026-11-24)

An isolated Docker API→Redis→Celery→runtime deployment ran all three previously
requested installed models. Gemma returned invalid actions after16.35s inference;
Qwen3.5 9B returned invalid actions after28.00s; Bonsai27B timed out after30.04s.
None finished a lap. All final recordings matched the API and verified against the
runtime fingerprint. Calibration inputs through the human-save HTTP path completed
12gates in22.4333 simulated racing seconds with no impacts, off-track time or resets.
That is an instrument check, not a human/model benchmark. Routine development now
uses scripted checks or only Gemma to conserve resources; larger models stay unloaded.
