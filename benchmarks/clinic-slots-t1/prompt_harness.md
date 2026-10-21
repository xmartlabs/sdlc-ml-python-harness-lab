# Engineering harness (read before you touch code)

You are operating inside a governed repository. Before writing code, ground
yourself in its harness — this is mandatory, not optional:

1. Read the governance docs at the repo root:
   - `.github/architecture.md`
   - `.github/standards.md`
   - `.github/domain-boundaries.md`
2. Read project memory for prior context:
   - `memory/context.md`
   - `memory/learnings.md`
3. Follow the working loop: Ground → Plan → Delegate → Verify → Compound.

Drive the work with the repo's skills, not ad-hoc improvisation:

- Use the **plan_and_execute_feature** skill to turn the task into explicit,
  phased work before you implement it.
- Use the **systematic_debugging** skill when something does not behave as
  expected — reproduce → isolate → hypothesize → fix → verify. No blind edits.
- Use the **verify_changes** skill to confirm the work before you consider it
  done.

Apply the harness to *how* you solve the task. The task itself, its scope and
its acceptance criteria are stated below and are not changed by this preamble.

---

# Task: list a clinic's free appointment slots

List a doctor's free appointment slots for a day — working hours, a lunch block, half-open intervals and a deterministic, sorted result.

All code goes in `{workspace}/clinic.py` — standard library only (`zoneinfo` is in the
standard library). No network, no third-party packages.

## The `clinic.py` contract (exact)

```python
free_slots(day: datetime, booked: list[tuple[datetime, datetime]], duration_min: int) -> list[datetime]
```

- Working hours are 09:00-17:00, with lunch 12:00-13:00, on `day`'s date (naive local times).
- A slot is a start time on the `duration_min` grid from 09:00; it is free when its whole
  `[start, start + duration)` lies inside working hours, outside lunch, and overlaps no booking.
- Intervals are **half-open**: `[09:30, 10:00)` and `[10:00, 10:30)` touch but do not overlap.
- Return the free starts sorted, without duplicates. A duration that fits nowhere returns `[]`.
- `duration_min <= 0` raises `ValueError`.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/clinic-slots-t1/tests/test_slots.py` and import `clinic`
from the workspace. Read them before you start — they are the acceptance criteria. Run
them with:

```bash
cd {workspace} && CLINIC_WORKSPACE={workspace} python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/clinic-slots-t1/tests/test_slots.py
```

Do not edit the tests.
