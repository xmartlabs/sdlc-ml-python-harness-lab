# The clinic ladder — clinic-slots-t1, clinic-booking-t2, clinic-rules-t3

Three cases on one domain, one per difficulty tier, so the lab can measure the
highest tier a model-harness pair does **reliably** — and whether a harness moves
that frontier (ADR-0069). The domain is appointment scheduling, the kind of
business logic a healthcare product lives on, with the rules that separate a
careful build from a fast one.

| Tier | Case | What it adds |
|---|---|---|
| 1 | `clinic-slots-t1` | free slots: working hours, lunch, half-open intervals, sorted output |
| 2 | `clinic-booking-t2` | bookings: no double-booking of a doctor *or* a patient, atomic reschedule, idempotency |
| 3 | `clinic-rules-t3` | time zones across DST, UTC storage, lead time on an injected clock, audit with no patient data |

Each case ships pytest tests graded one command per test; all are pure Python and
standard library, so they run on any model through any harness, including small
self-hosted models under opencode.

## Calibration (2026-10-21, outside the runner)

| Workspace | T1 | T2 | T3 |
|---|---|---|---|
| empty | 0 / 10 | 0 / 14 | 0 / 12 |
| a plausible naive build (closed intervals, doctor-only overlap, non-atomic reschedule, naive times, patient name in errors) | 5 / 10 | 11 / 14 | 4 / 12 |
| reference implementation (kept outside the repository) | 10 / 10 | 14 / 14 | 12 / 12 |

The naive build loses most at tier 3, where the rules a hurried model skips live.

## Reading a capability frontier

Tag-driven: each case carries `tier:1`, `tier:2` or `tier:3`. Run all three in one
experiment with 5 repetitions per arm; the experiment page then shows, per arm, the
highest tier reached with every lower tier reliable too.

```bash
make prompts
for c in clinic-slots-t1 clinic-booking-t2 clinic-rules-t3; do
  make case-import TASK=benchmarks/$c/task.yaml
done
```
