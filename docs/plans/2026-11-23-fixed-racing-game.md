# PR2: fixed racing gameplay

Approved continuation of PR1 (#59, still open at start), stacked on 596c640.
No existing game-creation cases change. Ghost challenges, cooking and vision stay
out of this contribution. Scope is a complete original fixed arcade time trial.

1. Shared Node/browser deterministic ESM simulation, fixed 60 Hz, seeded scenery,
   ordered checkpoints, countdown/start/end, bounded throttle/brake/steer/reset,
   visible driving state and numerical race metrics. Independent Node tests and
   a calibrated path-following baseline must complete the course.
2. Python Environment adapter to the same Node simulation and PR1 runner/recorder.
   CLI run/verify supports racing. Fixed countdown actions avoid charging models
   for a non-interactive start sequence; actual racing decisions are model calls.
3. Authenticated API/Celery launch, persistent episode artifacts, saved human
   action validation/resimulation, results and model discovery. No private client
   state or claimed score is trusted. No automatic model lifecycle changes.
4. Original Three.js course/car/scenery, keyboard controls at the same model input
   cadence, race HUD, start/pause/restart/end, usable model launch/results surface.
5. Focused deterministic and HTTP/CLI tests, visual rendering via headless CLI,
   installed-model bounded smokes, make gates and independent review. Update guide
   and durable checkpoint with actual evidence and remaining work.

Use existing Node 20 in API/worker images: authored .mjs simulation has a typed
.d.mts boundary for the TypeScript UI, avoiding a runtime compiler or a second
physics implementation. The Python adapter communicates over NDJSON to Node.
Frontend dependencies and tools remain repository-local or temporary under /tmp.
Publishing PR2 is not assumed; finish reviewable code and proposed title/body.
Usage checkpoint: five-hour allowance 65% used at start. Never use reset credits.
