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

# Task: book, cancel and reschedule clinic appointments

Book, cancel and reschedule appointments — no double-booking of a doctor or a patient, atomic reschedule, and idempotent requests.

All code goes in `{workspace}/clinic.py` — standard library only (`zoneinfo` is in the
standard library). No network, no third-party packages.

## The `clinic.py` contract (exact)

```python
class Conflict(Exception): ...
class NotFound(Exception): ...

class Schedule:
    def book(self, doctor: str, patient: str, start: datetime, minutes: int = 30,
             idempotency_key: str | None = None) -> str          # returns a booking id
    def cancel(self, booking_id: str) -> None                     # NotFound if unknown
    def reschedule(self, booking_id: str, new_start: datetime) -> None
    def bookings(self, doctor: str | None = None) -> list[Booking]  # live, sorted by start
```

`Booking` has at least `.id`, `.doctor`, `.patient`, `.start` (datetime) and `.minutes`.

Rules:
- A doctor cannot have two overlapping bookings, and **neither can a patient** — raise `Conflict`.
- Intervals are half-open: back-to-back bookings are allowed.
- Cancelled bookings free their slot and are not listed.
- `reschedule` is **atomic**: if the new slot conflicts, raise `Conflict` and leave the
  booking exactly where it was. A booking never conflicts with itself.
- Idempotency: the same `idempotency_key` with the same request returns the same booking id
  and creates nothing new; the same key with a different request raises `Conflict`.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/clinic-booking-t2/tests/test_booking.py` and import `clinic`
from the workspace. Read them before you start — they are the acceptance criteria. Run
them with:

```bash
cd {workspace} && CLINIC_WORKSPACE={workspace} python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/clinic-booking-t2/tests/test_booking.py
```

Do not edit the tests.
