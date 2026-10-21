"""Graded browser tests for frontend-bugfix-v1 (ADR-0068).

The workspace starts from a clinic-schedule page with four reported bugs. These
tests open the agent's fixed `index.html` from disk in headless Chromium and
check behaviour through the page's accessible contract. Two tests are
regressions: what already worked must keep working after the fix.

The workspace comes from SCHEDULE_WORKSPACE, defaulting to the current directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

WORKSPACE = Path(os.environ.get("SCHEDULE_WORKSPACE", ".")).resolve()
INDEX = WORKSPACE / "index.html"


@pytest.fixture
def app(page: Page) -> Page:
    if INDEX.is_file():
        page.goto(INDEX.as_uri())
    page.set_default_timeout(3000)
    expect.set_options(timeout=3000)
    return page


def _rows(page: Page):
    return page.get_by_test_id("appointment")


def _count(page: Page):
    return page.get_by_test_id("appointment-count")


# --- regressions: what worked before the fix still works ----------------------


def test_regression_all_appointments_render_in_time_order(app: Page):
    expect(_rows(app)).to_have_count(5)
    times = [row.inner_text().split()[0] for row in _rows(app).all()]
    assert times == sorted(times)


def test_regression_each_row_names_its_doctor(app: Page):
    expect(_rows(app).first).to_contain_text("Dr. Ana Pérez")


# --- bug 1: the doctor filter shows nothing ------------------------------------


def test_filtering_by_a_doctor_shows_only_their_appointments(app: Page):
    app.get_by_label("Doctor").select_option(label="Dr. Ana Pérez")
    expect(_rows(app)).to_have_count(2)
    for row in _rows(app).all():
        expect(row).to_contain_text("Dr. Ana Pérez")


def test_switching_doctors_and_back_to_all_restores_the_list(app: Page):
    app.get_by_label("Doctor").select_option(label="Dr. Bruno Silva")
    expect(_rows(app)).to_have_count(2)
    app.get_by_label("Doctor").select_option(label="All doctors")
    expect(_rows(app)).to_have_count(5)


# --- bug 2: the counter disagrees with the list -------------------------------


def test_the_counter_matches_what_is_shown(app: Page):
    expect(_count(app)).to_have_text("5 appointments")
    app.get_by_label("Doctor").select_option(label="Dr. Carla Gómez")
    expect(_count(app)).to_have_text("1 appointment")


def test_the_counter_follows_a_cancellation(app: Page):
    _rows(app).first.get_by_role("button", name="Cancel").click()
    expect(_count(app)).to_have_text("4 appointments")


# --- bug 3: cancel cannot be reached without a mouse --------------------------


def test_cancel_is_a_button_with_an_accessible_name(app: Page):
    buttons = app.get_by_role("button", name="Cancel")
    expect(buttons).to_have_count(5)


def test_cancel_works_from_the_keyboard(app: Page):
    first = _rows(app).first.get_by_role("button", name="Cancel")
    first.focus()
    expect(first).to_be_focused()
    app.keyboard.press("Enter")
    expect(_rows(app)).to_have_count(4)


def test_a_cancellation_is_announced(app: Page):
    _rows(app).nth(1).get_by_role("button", name="Cancel").click()
    expect(app.get_by_role("status")).to_contain_text("09:30")


# --- bug 4: patient names are parsed as HTML -----------------------------------


def test_patient_names_are_rendered_as_text(app: Page):
    expect(_rows(app).nth(4)).to_contain_text("<b onmouseover")
    assert app.locator(".appointment b").count() == 0
    _rows(app).nth(4).hover()
    assert app.title() != "pwned"
