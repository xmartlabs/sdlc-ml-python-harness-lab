"""Graded tests for clinic-slots-t1 — tier 1 of the clinic ladder (ADR-0069).

The agent writes `clinic.py` in the workspace; these tests import it from the
directory in CLINIC_WORKSPACE (default: the current directory).
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


def at(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 3, hour, minute)


DAY = datetime(2026, 11, 3)


def test_an_empty_day_offers_every_slot_in_working_hours(clinic):
    slots = clinic.free_slots(DAY, booked=[], duration_min=30)
    assert slots[0] == at("09:00")
    assert slots[-1] == at("16:30")  # last 30-minute slot ends at 17:00
    assert len(slots) == 14  # 16 half-hours in 09:00-17:00, minus the two at lunch


def test_slots_follow_the_requested_duration(clinic):
    slots = clinic.free_slots(DAY, booked=[], duration_min=60)
    assert slots[:3] == [at("09:00"), at("10:00"), at("11:00")]
    assert slots[-1] == at("16:00")


def test_a_booking_removes_the_slots_it_overlaps(clinic):
    slots = clinic.free_slots(DAY, booked=[(at("10:00"), at("10:30"))], duration_min=30)
    assert at("10:00") not in slots
    assert at("09:30") in slots and at("10:30") in slots


def test_back_to_back_is_allowed_because_intervals_are_half_open(clinic):
    """[09:30, 10:00) and [10:00, 10:30) touch but do not overlap."""
    slots = clinic.free_slots(DAY, booked=[(at("10:00"), at("10:30"))], duration_min=30)
    assert at("09:30") in slots


def test_a_one_minute_overlap_blocks_the_slot(clinic):
    slots = clinic.free_slots(DAY, booked=[(at("10:29"), at("10:45"))], duration_min=30)
    assert at("10:00") not in slots
    assert at("10:30") not in slots


def test_the_lunch_block_is_never_offered(clinic):
    slots = clinic.free_slots(DAY, booked=[], duration_min=30)
    assert at("12:00") not in slots
    assert at("12:30") not in slots
    assert at("11:30") in slots and at("13:00") in slots


def test_a_slot_may_not_straddle_lunch(clinic):
    slots = clinic.free_slots(DAY, booked=[], duration_min=60)
    assert at("11:30") not in slots  # 11:30-12:30 would run into lunch


def test_the_result_is_sorted_and_has_no_duplicates(clinic):
    booked = [(at("15:00"), at("15:30")), (at("09:00"), at("09:30"))]
    slots = clinic.free_slots(DAY, booked=booked, duration_min=30)
    assert slots == sorted(set(slots))


def test_a_duration_longer_than_the_free_time_gives_no_slots(clinic):
    assert clinic.free_slots(DAY, booked=[], duration_min=600) == []


def test_a_non_positive_duration_is_refused(clinic):
    with pytest.raises(ValueError):
        clinic.free_slots(DAY, booked=[], duration_min=0)
