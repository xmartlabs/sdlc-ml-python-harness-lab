# Task: fix the clinic schedule page

`{workspace}/` already holds a small clinic-schedule web page — `index.html`,
`app.js`, `data.js`, `styles.css`. It is a static page opened straight from disk
(`file://`), with no server, no build step and no network. Front-desk staff say it
"mostly works". It does not. Fix it.

## Reported bugs

1. **The doctor filter shows nothing.** Choosing a doctor in the *Doctor* dropdown
   empties the list instead of showing that doctor's appointments; choosing *All
   doctors* again should bring every appointment back.
2. **The counter is wrong.** The line above the list must always state how many
   appointments are *shown*, as `5 appointments` / `1 appointment` — after filtering
   and after a cancellation.
3. **Cancel cannot be used without a mouse.** Staff who use the keyboard or a screen
   reader cannot reach or trigger *Cancel*. Each appointment's cancel control must be
   a real button named `Cancel`, reachable with Tab and activated with Enter. The
   existing status message announcing a cancellation must keep working.
4. **A patient name is executed as HTML.** One record contains markup; names must be
   shown as plain text, exactly as entered, never interpreted.

## Keep what works

The appointments still render in time order, each row still names its doctor, and the
page keeps its structure: the `Doctor` select, the `data-testid="appointment-count"`
counter, rows with `data-testid="appointment"`, and the `role="status"` message.

## Constraints

- Fix the existing files; do not rewrite the page from scratch or change `data.js`.
- Plain HTML, CSS and JavaScript; no dependencies, no network.
- Do not modify anything outside `{workspace}/`.

## Acceptance: the tests

The graded tests live at `/app/benchmarks/frontend-bugfix-v1/tests/test_schedule.py`
and drive the page in headless Chromium. Two of them are regressions — behaviour that
already works and must keep working. Read them before you start. Run them with:

```bash
cd {workspace} && SCHEDULE_WORKSPACE="$PWD" python -m pytest -c /dev/null -p no:cacheprovider -q /app/benchmarks/frontend-bugfix-v1/tests/test_schedule.py
```

Do not edit the tests.
