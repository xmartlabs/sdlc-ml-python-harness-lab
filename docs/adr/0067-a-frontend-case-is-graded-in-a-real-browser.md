# ADR-0067: A frontend case is graded in a real browser

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md),
  [ADR-0050](0050-a-case-ships-its-own-tests.md),
  [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md)

## Context

Every case the lab can grade objectively is Python: a pygame Tetris, a stdlib CLI,
SWE-bench issues. Teams now do much of their AI-assisted work on frontends, and the
question a harness most often has to answer there is not "does it compile" but "does it
work for a person": the keyboard reaches every control, state is announced to a screen
reader, data survives a reload, user text is never parsed as HTML. None of that is
visible in a diff, and an LLM judge reading one guesses. The runner had no browser;
`runner_capabilities` declared `browser` as a service the audit could ask for and the
lab could never provide.

## Decision

**A frontend case ships a Playwright suite that drives the agent's page in headless
Chromium through an accessible contract stated in the prompt.**

1. **The contract is roles, accessible names, `data-testid` hooks and the keyboard** —
   what a user or assistive technology relies on — never class names or DOM shape. The
   prompt states every name the tests use.
2. **The page works from `file://`**, so grading needs no dev server and no network and
   runs in the network-less validation lane. A case that needs a server is a different
   case and a different decision.
3. **One validation command per test**, after a tamper guard and an entry-point check
   (ADR-0050), so `test_pass_rate` has the suite's resolution.
4. **The runner image carries the browser** in a pinned layer: Python Playwright,
   `pytest-playwright` and Chromium with its system libraries, and `BROWSER_AVAILABLE=1`
   so the capability table reports it.
5. **A case is calibrated before it is trusted:** an empty workspace fails every test
   (ADR-0057), a reference implementation kept outside the repository passes every
   test, and a plausible weaker build lands in between.

`todo-web-v1` is the first such case: twelve tests covering add, blank input, the
counter's wording, complete, delete, filters, `aria-pressed`, persistence, clear
completed, a keyboard-only flow, and an injection attempt. Calibrated inside the runner
layer with no network: empty 0/12, weaker build 9/12, reference 12/12.

## Consequences

- The lab can run harness and harness x model experiments on frontend work, and test
  capability expansion — whether a harness lets an agent ship sound frontend outside a
  team's main stack.
- The runner image grows by Chromium and its libraries (on the order of a few hundred
  MB); the layer sits before the agent CLIs so a Playwright bump does not rebuild them.
- A bug-fix frontend case (start from a broken page) is not possible yet: a workspace
  starts empty, from a git target, or from a SWE-bench Pro image, and there is no way to
  seed it from files in the case directory. That mechanism is the next step.
- Running a calibration surfaced an ambiguous locator (`name="Completed"` also matched
  *Clear completed*) that would have failed a correct page; locators for short names use
  `exact=True`.
