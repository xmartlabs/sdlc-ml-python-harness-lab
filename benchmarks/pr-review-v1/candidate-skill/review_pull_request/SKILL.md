---
name: review_pull_request
description: Use when asked to review a pull request, a diff, a patch or a branch — "review this PR", "code review", "review the diff", "is this safe to merge". Finds real defects (correctness, edge cases, state, security, error handling, contracts, tests that do not test), proves each one against the code, and reports precisely located findings with a merge verdict. Not for writing or fixing the code itself.
---

# Skill: review_pull_request

## Purpose

Tell the author what will break if this change merges, and nothing else. A review is
judged on two things at once: the defects it finds (recall) and the noise it does not
add (precision). Ten plausible comments with two real bugs in them is a worse review
than the two bugs alone — the author has to disprove the other eight.

## Required input

- The change: a diff, a patch file, a PR description, or two refs to compare.
- Access to the code the change lands in, not only the hunks.
- The output format the requester wants, if any. When none is given, use the default
  format below.

## Workflow

1. **Understand the intent before the code.** Read the PR description, linked issue or
   commit message. Write down in one line what the change is supposed to do. A defect is
   a gap between that intent (or an existing contract) and the code.
2. **Read the whole diff once, then the context.** For every hunk open the surrounding
   function and its callers. Most real bugs live where the new code meets the old: a
   changed return type, a caller that still assumes the old behaviour, a lock the new
   path does not take.
3. **Hunt by category, one pass each.** Do not stop at the first finding.
   - *Correctness*: off-by-one, inverted condition, wrong operator, wrong unit, integer
     vs float division, mutated shared default, wrong variable.
   - *Edge cases*: empty, zero, negative, None/missing, duplicates, boundaries,
     unicode, very large input.
   - *State and concurrency*: check-then-act, read-modify-write without a lock, partial
     updates left behind on error, caches not invalidated, ordering assumptions.
   - *Security*: untrusted input reaching a query, a path, a shell, a deserializer or a
     template; missing authorisation on a new path; secrets in code or logs; timing-
     unsafe comparisons.
   - *Error handling*: swallowed exceptions, a resource not released on failure, a
     retry that is not idempotent, an error that leaks internals.
   - *Contract and API*: a signature, schema or default that changed under existing
     callers; behaviour that no longer matches the docstring.
   - *Tests*: a new test that cannot fail (asserts a mock, asserts nothing, catches the
     exception it should surface); new behaviour with no test.
4. **Prove every finding before you write it down.** For each candidate, trace a
   concrete input from entry point to the faulty line and state what happens. If you
   cannot construct that trace, or the surrounding code already handles the case (a
   guard upstream, a validated type, a lock held by the caller), drop it. Code that looks
   wrong but is correct is the most common source of review noise — look for the reason
   it is right before calling it wrong. Run the code or a quick test when you can.
5. **Locate precisely.** Point to the file and the line in the **new** version of the
   file where the defect is, not the start of the hunk or the function.
6. **Calibrate severity.**
   - `critical`: data loss, security breach, crash on a common path.
   - `high`: wrong result on a realistic input, or a race that will happen in production.
   - `medium`: wrong on an edge case, or a missing check that a caller could trip.
   - `low`: maintainability or clarity; never blocks a merge.
   Style preferences and naming are not findings unless the requester asked for them.
7. **Decide.** `request_changes` when any finding is medium or above; otherwise
   `approve`. Say why in one sentence.
8. **Do not fix the code** unless asked. A review reports; changing the code under
   review destroys the thing being reviewed.

## Output format

Use the requester's format when one is given. Otherwise write a JSON document:

```json
{
  "verdict": "request_changes",
  "summary": "One sentence: what the change does and whether it is safe to merge.",
  "findings": [
    {
      "file": "path/relative/to/repo.py",
      "line": 42,
      "severity": "high",
      "category": "correctness",
      "title": "Short statement of the defect",
      "explanation": "The concrete input, the path it takes, and the wrong outcome.",
      "suggestion": "The smallest change that fixes it."
    }
  ]
}
```

`category` is one of `correctness`, `edge-case`, `concurrency`, `security`,
`error-handling`, `contract`, `tests`, `maintainability`.

## Checklist before you hand it in

- [ ] Every finding has a concrete trace (input → line → wrong outcome).
- [ ] No finding is about code the diff does not touch or depend on.
- [ ] Every line number points at the defect in the new file.
- [ ] Nothing that the surrounding code already guards is reported.
- [ ] The verdict follows from the severities.
- [ ] The code under review is unchanged.
