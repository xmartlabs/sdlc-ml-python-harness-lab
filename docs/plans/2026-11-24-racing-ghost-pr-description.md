# Proposed title

feat: challenge recorded racing ghosts on the shared simulation clock

# Proposed description

Let a human race beside a selected model recording in Tidal Ring. The translucent
purple ghost replays controls on the human's simulation clock and never interacts
with physics. Countdown, pause and restart stay synchronized; the HUD compares
course-progress distance and valid recorded finish times. Keep solo playback.

Verify source recordings against the actual runtime, then check build/seed/track/
cadence/state compatibility in the browser. Reject empty/countdown-only or corrupt
recordings. Meaningful failed partial attempts remain explicitly partial, freeze at
their last known state and never acquire a fabricated lap time. A labeled SCRIPTED
calibration ghost supplies practice/test coverage without pretending a model finished.
Human challenge recordings preserve reference identity and engine provenance.

This PR depends on PR2 #64 (https://github.com/xmartlabs/sdlc-ml-python-harness-lab/pull/64) and is based on codex/fixed-racing-runtime6531346 so its diff
contains only ghosts. Existing game-creation tasks, racing physics, cooking and vision
are unchanged. No LLM/model loading is required for playback or these tests.

Validation:
- `make check`:3507passed,5skipped; format/lint/security/mypy,87%coverage.
- Docker frontend verification:13racing/ghost+5HTTP tests, types/build passed.
- `make check-sync` and diff whitespace checks passed.
- Live isolated Docker API/runtime with headless browser: visible simultaneous
  translucent ghost, synchronized countdown/pause/restart, natural human DNF,
  verified save with source ID; transition locks and mobile no-overflow passed.
- Full reference state corruption/build/seed/cadence guards, meaningful timeout
  partial eligibility and countdown-only rejection tested. Browser permits only
  1e-9 numerical JSON drift; authoritative server replay verification stays exact.
- No model inference or loading; SCRIPTED references are explicitly labeled.

PR2 remains immutable at6531346. This PR should target its branch until PR2 merges;
then retarget main without merging PR2 here. Cooking/vision remain later work.
