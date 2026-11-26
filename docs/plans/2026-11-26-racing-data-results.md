# Racing data-only results — November 26

**Status: four verified recordings; Qwen 3.8 blocked before loading. Five-ghost video is not yet complete.**

Dashboard: http://localhost:18081/gameplay/racing/comparison

Frozen seed7,60Hz,0.5s actions,3s countdown+60s racing horizon,60s decision timeout,1800s episode wall budget. Same original track/physics.

| Model | Outcome | Lap / progress | Gates | Max / tick-mean speed (m/s) | Off-track (s) | Collisions / resets | Valid actions | Inference / wall (s) |
|---|---|---|---|---|---|---|---|---|
| google/gemma-4-e4b | DNF: simulation limit | 7.34% | 0 | 16.10 / 0.54 | 57.00 | 1 / 0 | 120 | 546.31 / 547.59 |
| qwen/qwen3.5-9b | DNF: simulation limit | 2.88% | 0 | 11.99 / 0.23 | 52.75 | 1 / 0 | 120 | 917.23 / 918.66 |
| prism-ml/bonsai-27b | finished | 16.083s | 12 | 21.00 / 18.52 | 0.00 | 0 / 0 | 33 | 375.20 / 375.58 |
| qwen/qwen3.8-27b | Not started: LM Studio memory guard | not started | — | — / — | — | — / — | — | 0.00 / 0.00 |
| gpt-6.1-sol | finished | 15.533s | 12 | 21.00 / 18.61 | 0.00 | 0 / 0 | 32 | 239.91 / 240.14 |

Bonsai and the requested GPT6.1Sol CLI completed actual verified laps; neither is scripted calibration. GPT requested ID accepted but effective response ID was not reported. Codex subscription wrapper/low reasoning differs from LM Studio API/none reasoning; no equivalent output-token cap is claimed. Monetary subscription cost is unknown. Token counts include reported returned responses; unavailable usage is not inferred. Episode wall excludes model loading.

All available recordings are independently replay-verified. Artifacts and authoritative state:
`data/tmp/gameplay-demo/racing-comparison/current.json` and `runs/<episode-id>/replay.json`.

Qwen3.8: installed4bit model requires15.70GB; LMStudiooMLX dynamic ceiling was12.05GB. No inference/action/journal exists. User asked to free4–5GB from unused apps. No guard/system settings changed. After memory is available, load with unchanged4096context/batch128/parallel1 requested settings, run ONLY the queued Qwen3.8 entry through run_attempt, verify and unload owned instance. Do not rerun other models or fabricate a fifth ghost.

Final video script `data/tmp/racing-data-nov26/record-comparison.mjs` requires allfive finalized verified recordings and captures real-time replay plus metrics/pause/restart. It intentionally refuses incomplete sessions. Prior Nov25 video remains intact.

Fullgate3553Python/5skip+41Node passed before GPT lifecycle fix; fix added2tests, with17Codexpolicy tests plus Ruff/mypy/Bandit passing. No trial rules changed. No commits/pushes/branches/PRs. User evidence review and discussion with Marcos still gate any publishing.

Preview MP4 ready: `data/tmp/racing-data-nov26/video-preview/racing-four-models-preview-2026-11-26.mp4`.
H.264 conversion/full decode passed; media-probe+SHA256 saved inpreviewreceipt.
Video showsFOURactualindependentghosts,clearlymarked4of5/incomplete.
Capture/conversionfinished,noownedhelpersremain; dashboardservicesleftavailable.
