# pr-review-v1 scoring

The grade is a hidden answer key (`grader/key.json`), one command per check. A finding
hits a seeded defect when it names the defect's file and lands within three lines of
it, or anywhere inside the function that holds it (functions up to `span_max_lines`
long, resolved with `ast` against the starter). Decoys keep the strict three-line window.
The span exists because reviewers point at a function's `def` when the defect is the
function's behaviour: three reviews of the first trial named the right race at the
`def` and missed by two lines. Levels are tagged with what establishes them: `[check]` an automated check, `[diff]`
the review text itself.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No valid `review.json`, or the code under review was modified | [check] |
| 2 | A valid review with the right verdict (`request_changes`), at least one real defect | [check] |
| 4 | Three of the six defects found, precision at least 0.6 | [check] |
| 6 | Four or five defects found, neither decoy flagged at medium or above, precision ≥ 0.6 | [check] |
| 8 | All six defects found, no decoy flagged, precision ≥ 0.6 | [check] |
| 10 | Level 8, and every explanation gives a concrete trace (input → line → wrong outcome) and a correct minimal fix | [check] + [diff] |

The six defects, by class (locations only in the key):

- a caller **outside the diff** broken by a changed return type (contract);
- an inclusive/exclusive threshold contradicting the PR description (edge case);
- an "all or nothing" promise broken on the failure path (correctness);
- a check-then-act race and a lock the code documents but does not take (concurrency);
- untrusted input interpolated into SQL (security);
- a new test that cannot fail (tests).

Two decoys look wrong and are correct; flagging either at medium or above is a false
positive. `precision` (blocking findings that hit a real defect ÷ all blocking findings)
stops a review from scoring by listing everything.

`canary` fails when the key's secret token appears in the run's transcripts or
workspace: a run that read the answer key is not a review. It is inconclusive (124)
for a harness whose logs do not record tool output (opencode).

Calibration (`data/reference/pr-review-v1/`): the untouched starter fails every review
check; the gold review passes all twelve measurable checks; a shotgun review (eight
plausible findings) passes three and fails precision; an empty `approve` passes only the
two decoy checks and `untouched`.
