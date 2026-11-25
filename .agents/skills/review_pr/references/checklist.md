# Review checklist (every depth)

Look for, in this order. A line here is a place to look, not a finding: report
only what you traced at `SHA`.

## 1. Bugs and logic errors

- Wrong condition, inverted logic, off-by-one, wrong variable.
- Unhandled `None` / `null`, and truthiness confused with presence: `if x:`
  where `0`, `""` or `[]` are valid; `dict.get()` whose default hides a
  missing key.
- **Return contract, success and error path.** When a function changes, its
  error shape is part of the contract. A swallowed error that returns `{}`,
  `[]`, `0` or `None` is falsy and flips callers that test the result; a
  non-empty error object is truthy. Read every caller in the repo.
- **A try/except that wraps independent work.** When the handler only logs,
  the sibling operations inside it silently never happen.
- **Every call the diff adds:** must it succeed, or is it best-effort? What
  existing path can its failure now abort?
- Race conditions on shared state, module-level caches or globals; code that
  is only safe with one worker.
- Read-modify-write on the same row or key without a transaction, a lock or
  an atomic update: a retried job or a second request is enough to lose an
  update. This needs no attacker.
- Resource leaks: a file, connection or process not closed on the error path.

## 2. Edge cases

- Empty inputs, zero, limits. When the code sets a limit (a count, a size, a
  page), check every other limit on the same path: a body size cap, a
  timeout, a column length, a proxy's request size.
- Encodings, time zones (naive mixed with aware datetimes), money in `float`.
- Retries and re-delivery: what state did the first attempt leave behind?
- Partial failures: two writes where the second can fail.
- An older or newer version of the other side: a backend without the new
  route, a client that does not send the new field.

## 3. Security

For each, name how it is reached (which input, which role, signed in or not)
and what it gives. No plausible path: it is a question, not a finding.

- Injection: SQL, shell (`shell=True`, a command built from input), template.
- Missing authentication or authorization, and object-level checks (the row
  belongs to the caller), not only a login check.
- Secrets in code, config defaults or logs; sensitive data in responses.
- Unsafe deserialization (`pickle`, `yaml.load` without a safe loader).
- Path traversal, SSRF.
- CI and deploy: a manual trigger that runs from any branch, a secret any
  workflow can read, an input that lands in a shell or overwrites a released
  artifact.

## 4. Missing tests where they matter

New or changed logic with real risk and no test that would fail if it broke.
Not "every function needs a test". A test that asserts only that a mock was
called, or that patches the function it tests, does not count.

## Python projects, also

- Mutable default arguments and mutable class attributes.
- Late-binding closures in loops.
- Async: a blocking call inside a coroutine, a task whose reference is
  dropped, shared state mutated across an `await`.
- `raise X` inside `except` without `from`, a bare `except:`.
- Data contracts: a changed pydantic model, dataclass, `TypedDict` or schema
  (a field renamed, made required, retyped, or `extra` handling changed).
  Check the producers, the consumers and the stored data that reads it back.
- `assert` used for validation (stripped under `python -O`).
