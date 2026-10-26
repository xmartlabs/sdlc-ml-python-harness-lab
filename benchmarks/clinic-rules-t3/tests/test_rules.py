"""Graded tests for clinic-rules-t3 — tier 3 of the clinic ladder (ADR-0069).

Everything in tier 2, plus the rules a production scheduler gets wrong: wall-clock
times in the clinic's time zone across a DST change, storage in UTC, a minimum
lead time against an injected clock, and an audit trail that never carries
patient data. The agent writes `clinic.py`; these tests import it from the
directory in CLINIC_WORKSPACE (default: the current directory).
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.environ.get("CLINIC_WORKSPACE", "."))

TZ = "America/New_York"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
PATIENT = {"id": "p-1", "name": "Lucía Fernández", "dob": "1984-02-29", "phone": "+1-555-0100"}


@pytest.fixture
def clinic():
    sys.modules.pop("clinic", None)
    try:
        return importlib.import_module("clinic")
    except ImportError as error:  # no clinic.py yet: a failed test, not a broken suite
        pytest.fail(f"clinic.py could not be imported from the workspace: {error}")


@pytest.fixture
def schedule(clinic):
    return clinic.Schedule(timezone=TZ, now=lambda: NOW, min_lead=timedelta(hours=2))


def local(day: int, hhmm: str, month: int = 11) -> datetime:
    """A naive wall-clock time in the clinic's zone."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, month, day, hour, minute)


def book(schedule, start, doctor="dr-1", patient=PATIENT, minutes=30, fold=None):
    return schedule.book(
        doctor=doctor, patient=patient, start=start, minutes=minutes, fold=fold
    )


# --- tier-2 behaviour still holds ---------------------------------------------


def test_double_booking_a_doctor_is_still_refused(schedule, clinic):
    book(schedule, local(3, "10:00"))
    with pytest.raises(clinic.Conflict):
        book(schedule, local(3, "10:15"), patient={**PATIENT, "id": "p-2"})


# --- time zone and DST ------------------------------------------------------------


def test_bookings_are_stored_in_utc(schedule):
    booking_id = book(schedule, local(3, "10:00"))
    stored = schedule.get(booking_id).start_utc
    assert stored.tzinfo is not None and stored.utcoffset() == timedelta(0)
    assert stored == datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)  # EST, UTC-5


def test_a_wall_time_before_the_change_uses_daylight_time(schedule):
    booking_id = book(schedule, local(30, "10:00", month=10))
    assert schedule.get(booking_id).start_utc.hour == 14  # EDT, UTC-4


def test_an_ambiguous_fall_back_time_is_refused_without_a_fold(schedule, clinic):
    """01:30 on 2026-11-01 happens twice in New York."""
    with pytest.raises(clinic.AmbiguousTime):
        book(schedule, local(1, "01:30"))


def test_an_ambiguous_time_is_accepted_when_the_fold_is_given(schedule):
    first = book(schedule, local(1, "01:30"), fold=0)
    second = book(schedule, local(1, "01:30"), fold=1, patient={**PATIENT, "id": "p-2"},
                  doctor="dr-2")
    gap = schedule.get(second).start_utc - schedule.get(first).start_utc
    assert gap == timedelta(hours=1)


def test_a_time_skipped_by_spring_forward_is_refused(clinic):
    spring = clinic.Schedule(
        timezone=TZ,
        now=lambda: datetime(2026, 3, 1, tzinfo=timezone.utc),
        min_lead=timedelta(hours=2),
    )
    with pytest.raises(clinic.NonexistentTime):
        spring.book(doctor="dr-1", patient=PATIENT, start=datetime(2026, 3, 8, 2, 30),
                    minutes=30, fold=None)


# --- lead time against the injected clock -----------------------------------


def test_a_booking_in_the_past_is_refused(schedule, clinic):
    with pytest.raises(clinic.TooSoon):
        book(schedule, local(29, "10:00", month=10))


def test_a_booking_inside_the_minimum_lead_time_is_refused(schedule, clinic):
    """NOW is 08:00 New York time on 2026-10-30; 09:30 is only 90 minutes ahead."""
    with pytest.raises(clinic.TooSoon):
        book(schedule, local(30, "09:30", month=10))


def test_a_booking_exactly_at_the_lead_time_is_allowed(schedule):
    book(schedule, local(30, "10:00", month=10))


# --- audit without patient data ----------------------------------------------


def test_every_change_is_audited_with_ids_and_utc_times(schedule):
    booking_id = book(schedule, local(3, "10:00"))
    schedule.cancel(booking_id)
    actions = [entry["action"] for entry in schedule.audit()]
    assert actions == ["book", "cancel"]
    for entry in schedule.audit():
        assert entry["booking_id"] == booking_id
        assert entry["at"].utcoffset() == timedelta(0)


def test_the_audit_never_contains_patient_data(schedule):
    booking_id = book(schedule, local(3, "10:00"))
    schedule.cancel(booking_id)
    text = repr(schedule.audit())
    for secret in ("Lucía", "Fernández", "1984-02-29", "555-0100"):
        assert secret not in text


def test_errors_and_logs_never_contain_patient_data(schedule, clinic, caplog):
    caplog.set_level(logging.DEBUG)
    book(schedule, local(3, "10:00"))
    with pytest.raises(clinic.Conflict) as error:
        book(schedule, local(3, "10:00"), doctor="dr-2")  # same patient, same time
    blob = str(error.value) + caplog.text
    for secret in ("Lucía", "Fernández", "1984-02-29", "555-0100"):
        assert secret not in blob
