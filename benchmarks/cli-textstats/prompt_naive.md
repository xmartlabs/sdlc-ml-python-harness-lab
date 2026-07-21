Build a single-file Python command-line tool at `main.py` (standard library
only) that reads text from standard input and prints a statistic depending on
the subcommand given as the first argument.

Subcommands and their exact output (the integer result followed by a newline,
nothing else):

- `words` — the number of whitespace-separated tokens in the input.
- `lines` — the number of non-empty lines (a line with at least one
  non-whitespace character).
- `unique` — the number of distinct tokens, compared case-insensitively
  (split on whitespace, lowercased).

Run as `python main.py <subcommand>`, reading the full input from stdin.
Handle empty input without crashing (print `0`).
