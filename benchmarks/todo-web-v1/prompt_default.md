# Task: build an accessible todo web app

Build a small todo-list web app as **one static page** that works opened straight
from disk (`file://`), with no server, no build step and no network.

All files go inside `{workspace}/`. Entry point: `{workspace}/index.html`. You may
add `.css` and `.js` files next to it; do not use a framework, a package manager or
anything fetched from the network — it must run offline.

## Requirements

- Add a todo by typing into the input and pressing **Enter**; the input clears.
  Blank or whitespace-only input adds nothing.
- Each todo can be marked complete with a checkbox, and deleted with a button.
- A counter shows how many todos are still open.
- Filters: **All**, **Active**, **Completed**.
- **Clear completed** removes every completed todo and nothing else.
- Todos and their completed state survive a page reload (`localStorage`).
- Everything works from the keyboard alone, in a sensible tab order.
- What the user types is shown as text — never interpreted as HTML.

## The page contract (exact)

The page is graded by an automated browser test suite that finds elements the way
a user or a screen reader would. These names are the contract:

| Element | How the tests find it |
|---|---|
| Page heading | an `<h1>` whose text is exactly `Todos` |
| New-todo input | a text box with the accessible name `New todo` (a `<label>` or `aria-label`); focused when the page loads |
| Each todo | an element with `data-testid="todo-item"` containing the todo's text, its checkbox and its button |
| Complete toggle | a checkbox inside the todo item |
| Delete | a button inside the todo item, accessible name `Delete` |
| Counter | an element with `data-testid="todo-count"` whose text is exactly `0 items left`, `1 item left`, `2 items left`, … |
| Filters | three buttons named exactly `All`, `Active`, `Completed`; the selected one has `aria-pressed="true"`, the others `aria-pressed="false"` |
| Clear | a button named `Clear completed` |

Keyboard: after a todo is added, one **Tab** from the input moves focus to that
todo's checkbox, **Space** toggles it, the next **Tab** reaches its **Delete**
button, and **Enter** deletes it.

## Constraints

- Plain HTML, CSS and JavaScript only; no dependencies, no network requests.
- Do not modify anything outside `{workspace}/`.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/todo-web-v1/tests/test_todo.py` and drive
the page in headless Chromium through the contract above. Read them before you
start — they are the acceptance criteria. Run them yourself with:

```bash
cd {workspace} && TODO_WORKSPACE={workspace} python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/todo-web-v1/tests/test_todo.py
```

Do not edit the tests.
