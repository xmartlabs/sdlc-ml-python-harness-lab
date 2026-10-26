"""Graded browser tests for todo-web-v1 (ADR-0067).

They open the agent's `index.html` from disk in headless Chromium and drive it
only through the contract in the prompt — roles, accessible names, the
`data-testid` hooks and the keyboard — never through implementation details.
No server, no network: the page must work from `file://`.

The workspace comes from the TODO_WORKSPACE environment variable (the case's
validation commands set it), defaulting to the current directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

WORKSPACE = Path(os.environ.get("TODO_WORKSPACE", ".")).resolve()
INDEX = WORKSPACE / "index.html"


@pytest.fixture
def app(page: Page) -> Page:
    # A missing page is the agent's failure, not a broken fixture: open a blank
    # page and let the test's own assertions fail on it.
    if INDEX.is_file():
        page.goto(INDEX.as_uri())
        page.evaluate("window.localStorage.clear()")
        page.reload()
    page.set_default_timeout(3000)
    expect.set_options(timeout=3000)
    return page


def _input(page: Page):
    return page.get_by_role("textbox", name="New todo")


def _items(page: Page):
    return page.get_by_test_id("todo-item")


def _add(page: Page, text: str) -> None:
    field = _input(page)
    field.fill(text)
    field.press("Enter")


def test_the_page_has_a_title_and_an_input_labelled_new_todo(app: Page):
    expect(app.get_by_role("heading", level=1)).to_have_text("Todos")
    expect(_input(app)).to_be_visible()
    expect(_input(app)).to_be_focused()


def test_enter_adds_a_todo_and_clears_the_input(app: Page):
    _add(app, "Buy milk")
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Buy milk")
    expect(_input(app)).to_have_value("")


def test_blank_or_whitespace_input_adds_nothing(app: Page):
    _add(app, "   ")
    _add(app, "")
    expect(_items(app)).to_have_count(0)


def test_the_counter_says_how_many_are_left(app: Page):
    expect(app.get_by_test_id("todo-count")).to_have_text("0 items left")
    _add(app, "One")
    expect(app.get_by_test_id("todo-count")).to_have_text("1 item left")
    _add(app, "Two")
    expect(app.get_by_test_id("todo-count")).to_have_text("2 items left")


def test_completing_a_todo_checks_it_and_updates_the_counter(app: Page):
    _add(app, "Write tests")
    _add(app, "Ship it")
    toggle = _items(app).first.get_by_role("checkbox")
    toggle.check()
    expect(toggle).to_be_checked()
    expect(app.get_by_test_id("todo-count")).to_have_text("1 item left")


def test_deleting_a_todo_removes_it(app: Page):
    _add(app, "Keep")
    _add(app, "Remove me")
    _items(app).nth(1).get_by_role("button", name="Delete").click()
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Keep")


def test_filters_show_all_active_and_completed(app: Page):
    _add(app, "Open task")
    _add(app, "Done task")
    _items(app).nth(1).get_by_role("checkbox").check()

    app.get_by_role("button", name="Active", exact=True).click()
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Open task")

    app.get_by_role("button", name="Completed", exact=True).click()
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Done task")

    app.get_by_role("button", name="All", exact=True).click()
    expect(_items(app)).to_have_count(2)


def test_the_active_filter_is_announced_with_aria_pressed(app: Page):
    app.get_by_role("button", name="Completed", exact=True).click()
    expect(app.get_by_role("button", name="Completed", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(app.get_by_role("button", name="All", exact=True)).to_have_attribute(
        "aria-pressed", "false"
    )


def test_todos_survive_a_reload(app: Page):
    _add(app, "Persist me")
    _items(app).first.get_by_role("checkbox").check()
    app.reload()
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Persist me")
    expect(_items(app).first.get_by_role("checkbox")).to_be_checked()


def test_clear_completed_removes_only_completed_todos(app: Page):
    _add(app, "Stay")
    _add(app, "Go")
    _items(app).nth(1).get_by_role("checkbox").check()
    app.get_by_role("button", name="Clear completed").click()
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("Stay")


def test_the_whole_flow_works_from_the_keyboard(app: Page):
    _add(app, "Keyboard only")
    app.keyboard.press("Tab")
    expect(_items(app).first.get_by_role("checkbox")).to_be_focused()
    app.keyboard.press("Space")
    expect(_items(app).first.get_by_role("checkbox")).to_be_checked()
    app.keyboard.press("Tab")
    expect(_items(app).first.get_by_role("button", name="Delete")).to_be_focused()
    app.keyboard.press("Enter")
    expect(_items(app)).to_have_count(0)


def test_text_is_rendered_as_text_not_html(app: Page):
    _add(app, "<img src=x onerror=\"document.title='pwned'\">")
    expect(_items(app)).to_have_count(1)
    expect(_items(app).first).to_contain_text("<img")
    assert app.title() != "pwned"
