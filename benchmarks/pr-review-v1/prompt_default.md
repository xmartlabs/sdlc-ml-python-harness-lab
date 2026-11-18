# Task: review a pull request

`{workspace}/` holds a small Python shop backend with **PR #214 already applied**. The PR
description is `{workspace}/PR.md` and its diff is `{workspace}/pr.diff`. The service
runs on several threads and its stock and orders are real money.

You are the reviewer. Decide whether this PR is safe to merge and report every defect
you find that would cause a wrong result, a crash, a security issue, lost or duplicated
stock, or a test that does not test what it claims. Report only real defects you can
justify from the code. Style, naming and personal preference are not findings, and a
review full of guesses is worse than a short accurate one.

## What to hand in

Write `{workspace}/review.json`:

```json
{
  "verdict": "approve | request_changes",
  "summary": "one sentence",
  "findings": [
    {
      "file": "shop/orders.py",
      "line": 42,
      "severity": "low | medium | high | critical",
      "category": "correctness | edge-case | concurrency | security | error-handling | contract | tests | maintainability",
      "title": "short statement of the defect",
      "explanation": "the input, the path it takes and the wrong outcome",
      "suggestion": "the smallest fix"
    }
  ]
}
```

- `file` is relative to `{workspace}/`; `line` is the line of the defect in the file as
  it is now (after the PR).
- Use `request_changes` when any finding is `medium` or above.

## How it is graded

Against a key of the real defects in this PR: each one you locate counts (`file`, and
`line` within a few lines of it), the `verdict` must be right, and **precision** counts
too — most of your `medium`-or-higher findings must be real defects, and flagging correct
code as broken costs you. The code under review must be left untouched.

## Constraints

- **Do not modify the code under review**: every file already in `{workspace}/` must stay
  byte-identical. You may run the code and its tests, and write scratch files of your own
  elsewhere in `{workspace}/`.
- Do not modify anything outside `{workspace}/`.
- The existing tests pass. That does not mean the PR is correct.

Run the PR's tests with:

```bash
cd {workspace} && python -m pytest -q -p no:cacheprovider
```
