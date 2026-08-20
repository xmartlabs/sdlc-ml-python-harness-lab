# Harness: manual

Fallback for runs done by hand (e.g. a quick baseline or a harness not yet scripted), or for
recording a run where the tool did not inject metadata automatically.

## Procedure

1. Register the run with `create_run.py` as usual.
2. Run the task manually with the chosen model/tool, keeping the frozen prompt verbatim.
3. If calls go through the gateway, set the `session_id` / tags by hand (e.g. via the LiteLLM
   request `metadata` field or `litellm.headers`), per `metadata-standard.md`. If they cannot
   carry metadata, rely on the **virtual key per methodology** so usage is still attributable.
4. Capture diff, `result_commit`, log, screenshot into `data/runs/<run-id>/`.
5. Score manually with `score_tetris.py`.

Use this only when an automated harness recipe does not exist. Prefer `opencode-local` /
`opencode-conductor` for the matrix cells so runs stay comparable.
