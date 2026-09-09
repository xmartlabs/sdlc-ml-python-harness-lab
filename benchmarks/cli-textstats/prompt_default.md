# Task: build a stdin text-statistics CLI

Build a single-file Python command-line tool that reads text from standard
input and prints one statistic, selected by the first argument (the
subcommand).

All files go inside `{workspace}/`. Entry point: `{workspace}/main.py`.

## I/O contract (exact)

Run as `python main.py <subcommand>`, reading the full input text from stdin.
Print the integer result followed by a single trailing newline — nothing else:
no labels, no prefixes, no extra output.

| Subcommand | Output |
|---|---|
| `words` | number of whitespace-separated tokens |
| `lines` | number of non-empty lines (a line with at least one non-whitespace character) |
| `unique` | number of distinct tokens, compared case-insensitively (split on whitespace, lowercased) |

Edge cases:

- Empty input prints `0` for every subcommand — it must not crash.
- An unknown subcommand exits non-zero with a short message on stderr.

## Constraints

- Single Python file: `main.py`.
- Python standard library only — no third-party packages.
- Do not modify the base repo outside `main.py`.

## Acceptance: the objective checks

Your build is graded by three shell assertions that compare stdout byte for
byte. Run them yourself before you finish; each one must print `exit=0`.

```bash
test "$(printf 'alpha beta gamma' | python {workspace}/main.py words)" = "3"; echo "exit=$?"
test "$(printf 'one\n\ntwo\n' | python {workspace}/main.py lines)" = "2"; echo "exit=$?"
test "$(printf 'Cat cat dog' | python {workspace}/main.py unique)" = "2"; echo "exit=$?"
```

Note what each one pins down: `lines` counts `2`, not `3`, because the blank
line does not count; `unique` counts `2`, not `3`, because `Cat` and `cat` are
the same token. A trailing label or a second line of output fails the
comparison even when the number is right.

Deliver the complete code. Keep it self-contained and runnable.
