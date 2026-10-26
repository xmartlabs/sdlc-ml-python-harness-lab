# frontend-bugfix-v1 scoring

The grade is the shipped browser suite: `test_pass_rate` over ten assertions, two of
them regressions. Levels are tagged with what can establish them — `[check]` an
automated check, `[diff]` the code itself (quoted).

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | The page no longer renders: a regression test fails | [check] |
| 2 | The page still renders five appointments in time order with doctor names (tests 1-2), but no reported bug is fixed | [check] |
| 5 | The doctor filter works both ways and the counter states what is shown (tests 3-6) | [check] |
| 8 | Cancel is a button named `Cancel`, reachable and triggered from the keyboard, and each cancellation is announced in the status region (tests 7-9) | [check] |
| 10 | Patient names are rendered as text, never as HTML (test 10), and the fix edits the existing files rather than rewriting the page | [check] + [diff] |

The regressions are what make this a bug-fix case rather than a build: the broken
starter passes exactly two tests, an empty workspace passes none, so neither leaving
the page alone nor replacing it with something else earns a score. Level 10 needs the
diff to confirm the existing files were fixed — a rewrite from scratch that happens to
pass is a different task.
