"""Graded tests for clinic-booking-t2 — tier 2 of the clinic ladder (ADR-0069).

The agent writes `clinic.py` with a `Schedule` class; these tests import it from
the directory in CLINIC_WORKSPACE (default: the current directory).
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.environ.get("CLINIC_WORKSPACE", "."))


@pytest.fixture
def clinic():
    sys.modules.pop("clinic", None)
    try:
        return importlib.import_module("clinic")
    except ImportError as error:  # no clinic.py yet: a failed test, not a broken suite
        pytest.fail(f"clinic.py could not be imported from the workspace: {error}")


@pytest.fixture
def schedule(clinic):
    return clinic.Schedule()


def at(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 3, hour, minute)


def book(schedule, doctor="dr-1", patient="p-1", start="10:00", minutes=30, key=None):
    return schedule.book(
        doctor=doctor, patient=patient, start=at(start), minutes=minutes, idempotency_key=key
    )


def test_a_booking_returns_an_id_and_is_listed(schedule):
    booking_id = book(schedule)
    assert booking_id
    listed = schedule.bookings(doctor="dr-1")
    assert [b.id for b in listed] == [booking_id]


def test_the_same_doctor_cannot_be_double_booked(schedule, clinic):
    book(schedule, patient="p-1", start="10:00")
    with pytest.raises(clinic.Conflict):
        book(schedule, patient="p-2", start="10:15")


def test_the_same_patient_cannot_be_in_two_places(schedule, clinic):
    book(schedule, doctor="dr-1", patient="p-1", start="10:00")
    with pytest.raises(clinic.Conflict):
        book(schedule, doctor="dr-2", patient="p-1", start="10:15")


def test_back_to_back_bookings_are_allowed(schedule):
    book(schedule, patient="p-1", start="10:00")
    book(schedule, patient="p-2", start="10:30")
    assert len(schedule.bookings(doctor="dr-1")) == 2


def test_different_doctors_and_patients_may_overlap(schedule):
    book(schedule, doctor="dr-1", patient="p-1", start="10:00")
    book(schedule, doctor="dr-2", patient="p-2", start="10:00")
    assert len(schedule.bookings()) == 2


def test_cancelling_frees_the_slot(schedule):
    first = book(schedule, patient="p-1", start="10:00")
    schedule.cancel(first)
    book(schedule, patient="p-2", start="10:00")
    assert len(schedule.bookings(doctor="dr-1")) == 1


def test_cancelled_bookings_are_not_listed(schedule):
    first = book(schedule)
    schedule.cancel(first)
    assert schedule.bookings() == []


def test_cancelling_an_unknown_booking_raises_not_found(schedule, clinic):
    with pytest.raises(clinic.NotFound):
        schedule.cancel("does-not-exist")


def test_rescheduling_moves_the_booking(schedule):
    booking_id = book(schedule, start="10:00")
    schedule.reschedule(booking_id, at("14:00"))
    [moved] = schedule.bookings()
    assert moved.start == at("14:00")
    assert moved.id == booking_id


def test_a_failed_reschedule_leaves_the_original_intact(schedule, clinic):
    """Atomic: a conflict on the new slot must not lose the old one."""
    keep = book(schedule, patient="p-1", start="10:00")
    book(schedule, patient="p-2", start="14:00")
    with pytest.raises(clinic.Conflict):
        schedule.reschedule(keep, at("14:00"))
    starts = sorted(b.start for b in schedule.bookings(doctor="dr-1"))
    assert starts == [at("10:00"), at("14:00")]


def test_a_booking_does_not_conflict_with_itself_when_rescheduled_nearby(schedule):
    booking_id = book(schedule, start="10:00", minutes=60)
    schedule.reschedule(booking_id, at("10:30"))
    [moved] = schedule.bookings()
    assert moved.start == at("10:30")


def test_the_same_idempotency_key_returns_the_same_booking(schedule):
    first = book(schedule, key="req-1")
    again = book(schedule, key="req-1")
    assert first == again
    assert len(schedule.bookings()) == 1


def test_the_same_key_with_a_different_request_is_a_conflict(schedule, clinic):
    book(schedule, start="10:00", key="req-1")
    with pytest.raises(clinic.Conflict):
        book(schedule, start="11:00", key="req-1")


def test_bookings_are_listed_in_time_order(schedule):
    book(schedule, patient="p-3", start="15:00")
    book(schedule, patient="p-1", start="09:00")
    book(schedule, patient="p-2", start="11:00")
    starts = [b.start for b in schedule.bookings()]
    assert starts == sorted(starts)
