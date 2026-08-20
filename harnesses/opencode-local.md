# Harness: opencode-local

Local-only execution: OpenCode driving a self-hosted model through the AI gateway (LiteLLM).
Used by **Cell A** (`tetris_incremental_v1`).

## Per-run procedure (clones-per-run linkage)

1. **Freeze** the subject `base_commit` of `ml-python-base` in `benchmarks/tetris/runs.yaml`.
2. **Register** the run: `python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001`
   → creates `data/runs/tetris-v1-A-001/` and prints `session_id` / tags / branch.
3. **Isolate the subject** in a branch (the subject code is never committed into this lab):
   ```bash
   git -C ../ml-python-base worktree add ../bench-tetris-v1-A-001 -b bench/tetris-v1-A-001 <base_commit>
   ```
   (or a fresh `git clone` checked out to that branch).
4. **Configure OpenCode** in the worktree `.env` for local execution:
   ```bash
   OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build   # executor, output-capped
   OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build  # same model plans (local-only)
   GATEWAY_BASE_URL=http://localhost:4000/v1
   GATEWAY_TOKEN=<virtual key: bench-opencode-local>
   ```
   Serve the model at **≥32k context** (LM Studio context length / Ollama `num_ctx`), temp `0.2`.
5. **Run** the frozen `tetris_incremental_v1` prompt in the `build` agent, one increment per turn.
6. **Attach metadata** per the standard (`metadata-standard.md`): `session_id=bench-tetris-v1-A-001`.
7. **Capture** into `data/runs/tetris-v1-A-001/`: the final diff, `result_commit`, terminal log,
   and a screenshot of the running game.
8. **Score** manually: `python scripts/score_tetris.py --run tetris-v1-A-001`.

## Notes

- `make opencode-doctor` (in the subject repo) must be green first — confirms gateway + model reachable.
- Keep the instruction surface tiny; let `LOCAL_AGENT.md` carry the local rules.
- Do not change the prompt between the 3 repetitions of the cell.
