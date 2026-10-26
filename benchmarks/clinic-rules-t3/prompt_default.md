# Task: a time-zone-safe, auditable clinic scheduler

Tier-2 booking plus the rules production schedulers get wrong — wall-clock times in the clinic's time zone across DST, storage in UTC, a minimum lead time against an injected clock, and an audit trail with no patient data.

All code goes in `{workspace}/clinic.py` — standard library only (`zoneinfo` is in the
standard library). No network, no third-party packages.

## The `clinic.py` contract (exact)

```python
class Conflict(Exception): ...
class NotFound(Exception): ...
class AmbiguousTime(Exception): ...
class NonexistentTime(Exception): ...
class TooSoon(Exception): ...

class Schedule:
    def __init__(self, timezone: str, now: Callable[[], datetime], min_lead: timedelta): ...
    def book(self, doctor: str, patient: dict, start: datetime, minutes: int = 30,
             fold: int | None = None) -> str
    def get(self, booking_id: str) -> Booking          # NotFound if unknown or cancelled
    def cancel(self, booking_id: str) -> None
    def audit(self) -> list[dict]
```

`patient` is a dict with `id`, `name`, `dob` and `phone`. `Booking.start_utc` is a
timezone-aware UTC datetime.

Rules:
- Everything from tier 2 still holds: no overlapping bookings for a doctor or a patient.
- `start` is a **naive wall-clock time in `timezone`** (an IANA name, e.g. `America/New_York`).
  Store it as UTC in `start_utc`.
- A wall time that happens twice (DST fall-back) raises `AmbiguousTime` unless `fold` is
  0 or 1; a wall time skipped by spring-forward raises `NonexistentTime`.
- A booking must start at least `min_lead` after `now()`; otherwise raise `TooSoon`.
  Exactly `min_lead` ahead is allowed. Use the injected `now`, never the system clock.
- `audit()` returns one entry per change, oldest first, each with `action` (`"book"`,
  `"cancel"`), `booking_id` and `at` (aware UTC).
- **No patient data** — name, date of birth, phone — may appear in the audit, in any
  exception message, or in any log record. Refer to patients by id.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/clinic-rules-t3/tests/test_rules.py` and import `clinic`
from the workspace. Read them before you start — they are the acceptance criteria. Run
them with:

```bash
cd {workspace} && CLINIC_WORKSPACE="$PWD" python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/clinic-rules-t3/tests/test_rules.py
```

Do not edit the tests.
