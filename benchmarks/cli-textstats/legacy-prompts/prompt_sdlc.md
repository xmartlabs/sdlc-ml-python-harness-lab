You are building a small, well-engineered CLI. Follow a disciplined SDLC loop:
**plan → implement → test → verify**. Do not skip steps.

## Task

Build a single-file Python command-line tool at `main.py` (standard library
only) that reads text from standard input and prints one statistic, selected by
the first argument (the subcommand).

### I/O contract (exact)

Run as `python main.py <subcommand>`; read the full input text from stdin. Print
the integer result followed by a single trailing newline — nothing else (no
labels, no extra output).

| Subcommand | Output |
|---|---|
| `words` | number of whitespace-separated tokens |
| `lines` | number of non-empty lines (≥1 non-whitespace char) |
| `unique` | number of distinct tokens, compared case-insensitively |

Edge cases: empty input prints `0`; an unknown subcommand exits non-zero with a
short error on stderr.

## How to work

1. **Plan** — restate the contract and list the cases you must handle (including
   empty input and whitespace-only lines) before writing code.
2. **Implement** — write `main.py` cleanly: a small function per subcommand, a
   thin `argv` dispatch, no third-party imports.
3. **Test** — add a `tests/` file with assertions covering each subcommand and
   the empty-input case; run it and make it green.
4. **Verify** — manually exercise all three subcommands from the shell and
   confirm the output matches the contract exactly.

Optimize for correctness first, then clarity. The objective checks assert the
exact stdout of each subcommand.
