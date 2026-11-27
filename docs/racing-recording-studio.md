# Racing recording studio

The studio compares saved human, AI and clearly labeled scripted runs. No model
weights, credentials or tested-model recordings are required to try it. Vision is
deferred; this release uses structured text observations only.

## Start with Docker

From the checkout root, with Docker running:

```sh
RACING_STUDIO_DATA_DIR="$PWD/data/tmp/racing-studio" docker compose -p racing-studio -f deploy/compose.racing-studio.yml up -d --build
```

Open http://localhost:18081/gameplay/racing/comparison. The isolated data directory
contains its database and recordings. The standalone Compose file mounts only this checkout, not
sibling repositories or personal credentials. The game runtime stays a non-root,
read-only Alpine/Node image with baked browser assets and a private HTTP engine.
API/worker dependencies stay in their existing application image.

To stop, use the same Compose arguments with `down` in place of `up ...`; omit
`-v` to retain volumes. Never open a live Docker SQLite database from the host.

## Try it without a model

1. Click **Create two SCRIPTED examples**. They are reference controllers, not
   model performances or human recordings.
2. Play the comparison. Switch **Overview camera** / **Follow camera**. The fitted
   overview centers the road with a small margin. Follow initially selects the
   best available valid lap, then unfinished progress; ties use recording ID.
3. Select a driver with the visible selector or number keys 1–8. Your choice stays
   selected when polling refreshes or another car leads. Keys do not intercept
   typing in form fields. Pause, seek and restart share one simulation clock.
4. Open **Drive the course yourself**. Choose standard (0.2s inputs) or data
   benchmark (0.5s inputs) rules. Use WASD/arrows, R to recover, Escape to pause.
   Save the verified run after it ends. A short recording ends explicitly; the
   server never appends invented idle actions.
5. Return to the studio, refresh saved recordings and select compatible sources.
   Human/human, human/AI, AI/AI and mixed groups use the same selector, up to eight
   participants. Matching build, environment, seed, cadence and horizon are
   required; incompatible choices are disabled and rejected by the API.

Standard human recordings can also be challenged directly from the driving page,
including recorded humans and models. Data-benchmark human recordings compare
with matching data-benchmark recordings in the studio. Partial recordings freeze
at their final recorded state. No ghost collides with another participant.

## Existing and new model evidence

Model runs remain optional. The driving page uses an already-loaded LM Studio
model. The separate `scripts/racing_comparison.py` CLI preserves explicit frozen
trial conditions, provider settings and provenance. Only one local model should
be loaded at a time. Codex subscription transport has a separate system wrapper;
it is not claimed equivalent to a direct local-model provider. No API billing
fallback or model download is performed.

Official trial `current.json` is retained separately from the studio's
`selection.json`. Creating a comparison or examples never overwrites frozen model
results. **Return to current benchmark** clears only the studio selection. Recorded outcomes, simulation times and inference latency remain
separate. A provider failure snapshot is not an official completed task score.

## Validation and artifacts

`make ci FRONTEND_IN_DOCKER=1` runs Python tests, types, security, skill sync,
frontend build and deterministic game/runtime tests. Camera geometry tests cover
landscape/portrait fitting; playback tests cover independent clocks, original and
benchmark recordings, target ranking and eight participants. Integration tests
verify mixed source roles, replay corruption, incompatible rules and exact short
human traces. Visual checks use headless browser screenshots/video; rendering
never advances or scores the simulation. Track geometry, physics and existing
game-creation benchmark tasks are unchanged.

Recordings and visual artifacts belong under ignored `data/` paths. Do not commit
model data, credentials, local databases, videos or personal notes. Deferred
cooking work remains on its earlier checkpoint branch and is outside this PR.
