# Proposed title

feat: add fixed racing gameplay and a slim Docker game runtime

# Proposed description

Add Tidal Ring, an original fixed arcade time trial with keyboard human controls
and structured text-model actions. Both use the same60Hz deterministic rules and
5Hz control cadence: countdown,12 directional checkpoints, barriers, recovery,
valid lap time and unfinished progress. Inference latency remains separate from
simulated racing time. Existing game-creation benchmark tasks are unchanged.

Build the game in a pinned multi-stage Docker image. Its ~186MiB final runtime
contains compiled browser assets, the shared rules and a dependency-free Node
HTTP/NDJSON executable, with no npm, Python, compiler, weights or desktop stack.
Compose starts a bounded non-root/read-only runtime; API and Celery use its private
sessions. The frontend proxies only baked assets. Browser WebGL supplies graphical
play, while headless execution needs no renderer or host tool installation.

The racing page launches loaded models, records every attempt and lists numerical
results with solo replay/download. Human saves are re-simulated server-side;
GameplayEpisode rows and PR1 artifacts preserve identity, status, actions and
results. Recordings include an engine fingerprint and reject incompatible builds.
Visible human-versus-model ghost challenges, cooking and vision remain later PRs.

## Validation

- `make check`:3502passed,5skipped; format/lint/security/mypy and87%coverage.
- `make check-sync`, `uv lock --check`, `git diff --check`: passed.
- Docker verify stage: TypeScript,9 racing tests,5 HTTP runtime tests and production
  frontend build passed. Regression tests cover shared-rule HTTP parity, malformed
  URLs, input/session bounds, TTL cleanup, source isolation and replay fingerprints.
- Live isolated API→Redis→Celery→runtime deployment persisted actual model attempts;
  API/disk recordings matched and replay verification passed. Calibration through
  the human-save API finished12gates in22.4333 simulated seconds with zero impacts,
  off-track time or recoveries. Baked headless CLI/NDJSON both ran successfully.
- Headless desktop/mobile rendering and keyboard/countdown/pause checks passed,
  without JS errors or horizontal overflow. Embedded401 events reach the parent
  login flow. Public proxy rejects private session POSTs.
- Final ARM64 image195401876bytes; observed idle memory22.3MiB/192MiB; absent
  development/agent/browser tools verified. AMD64 was not exercised locally.

Bounded model outcomes are failures, not completed gameplay benchmarks: installed
Gemma4 E4B returned invalid actions after16.35s inference; Qwen3.5 9B after28.00s;
Bonsai27B timed out after30.04s. None completed a lap. Only test-owned instances
were unloaded. Further routine inference uses Gemma only at the user's request.

PR1 #59 is merged. Review this diff against authoritative main57a8055, on
`codex/fixed-racing-runtime`; the previous stacked branch and preservation stash
remain untouched. This proposed PR is not committed, pushed or published.
