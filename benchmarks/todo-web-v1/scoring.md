# todo-web-v1 scoring

The grade is the shipped browser suite: `test_pass_rate` over twelve independent
assertions driven through the page's accessible contract, all objective. The rubric
exists so the LLM judge and a human reviewer land on the same ladder; every level is
tagged with what can establish it — `[check]` an automated check, `[diff]` the code
itself (quoted), `[manual]` only a human using the page.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No useful code produced | [diff] |
| 1 | `index.html` exists but the heading or the labelled input is missing (test 1 fails) | [check] |
| 2 | The page renders its heading and a focused `New todo` input (test 1) | [check] |
| 5 | Todos can be added, counted, completed and deleted; blank input is ignored (tests 2-6) | [check] |
| 8 | Filters work, announce their state with `aria-pressed`, and *Clear completed* removes only completed todos (tests 7, 8, 10) | [check] |
| 9 | Todos and their state survive a reload (test 9) | [check] |
| 10 | The whole flow works from the keyboard and user text is rendered as text, never HTML (tests 11, 12), with no dependency or network request in the code | [check] + [diff] |

Level 10 is the only one that needs the diff as well as the checks: the tests prove
behaviour, and the judge must confirm from the code that nothing is fetched from the
network and no framework or package was pulled in.

Two of the twelve are the failures a fast, unreviewed build most often ships: the
counter's wording (`1 item left`, not `1 items left`) and rendering user input with
`innerHTML`, which test 12 catches as an injection.
