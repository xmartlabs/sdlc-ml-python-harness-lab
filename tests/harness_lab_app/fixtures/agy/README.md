# agy 1.5.14 fixtures

Captured on 2026-11-22 from the official Antigravity CLI binary
(`cli_linux_arm64.tar.gz`, version 1.5.14, sha512 verified) in a
`python:3.11-slim` container. No real credential was used: the model side was
either the real Gemini API answering a fake key (`error_invalid_key`), an
unreachable endpoint (`error_network`), or a local Gemini API mock reached
through the documented `GOOGLE_GEMINI_BASE_URL` (everything else). `init.tools`
is trimmed for size; every other byte is what the CLI printed.

| File | Run |
|---|---|
| `success_text.stream.jsonl` | `-p` one-word answer, usage with cache + thinking |
| `success_tools.stream.jsonl` | write_file, read_file of a `.agents/skills/*/SKILL.md`, run_command exiting 1, edit_file |
| `success_streamed_text.stream.jsonl` | a slow answer: ACTIVE `text_delta` chunks closed by a DONE `"\n"` |
| `subagent_and_tool_error.stream.jsonl` | allow-listed tools, a failing `search_web` (ERROR step) and `invoke_subagent` |
| `soft_denied.stream.jsonl` | a read outside the allow-list, soft-denied: the turn ends with SUCCESS and no answer |
| `error_invalid_key.combined.txt` | real Gemini API refusing a fake key (stdout+stderr merged, as the runner reads it) |
| `error_{403,404,429,500}.{stream.jsonl,stderr.txt}` | provider errors from the mock |
| `error_network.*` | connection refused, after the CLI's own retries |
| `error_timeout.*` | `--print-timeout 3s` against a hanging endpoint |
| `error_no_key.stderr.txt` | `modelProvider: gemini` without `GEMINI_API_KEY` (exit 1, no JSON) |
| `models.stdout.txt` | `agy --settings '{"modelProvider":"gemini"}' models` (stdout + stderr) |
