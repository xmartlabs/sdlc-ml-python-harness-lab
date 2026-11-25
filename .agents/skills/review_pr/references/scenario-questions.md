# Scenario questions (standard and deep)

Why: a reviewer reading a diff line by line misses defects it would catch if
asked one pointed question about them. On one team's labelled pull requests,
adding this step raised the share of known defects found from 48% to 71%
(backend) and from 36% to 87% (frontend and tests), at about twice the cost.
It also adds findings that are true but minor, which is why the worth rating
in Step 4 decides what reaches the review.

## 1. Write the questions (the main model, never delegated)

Do not look for defects yet. Go through every changed function, handler, job,
script, component, workflow and test, reading the code around it you need
(callers, consumers of what it returns or writes, sibling implementations,
config, docs). For each, think about:

- each failure path: every raise, catch, early return, `None` or sentinel,
  and what each caller does with it;
- retries, re-deliveries, a second run, and what the first attempt left;
- concurrent or out-of-order execution: two writes racing, a late response;
- time: clocks, timeouts against another component's timeout, expiry;
- limits: the largest input the code allows against every cap on the path;
- every contract the change sets or relies on: a caller, a consumer, another
  repository, a docstring that states what the code does, a test meant to
  fail when the behaviour regresses.

Write each as one question with the concrete trigger and the observable
consequence, naming the file and line it starts from. For example: "If an
admin selects 500 VDIs, does the request fit the body limit the route reads
with, and what does the page show if it does not?"

Write the number `plan` printed (8 standard, 12 deep, or `--questions N`),
ranked by how likely the answer reveals a real defect. Prefer the ones a
line-by-line reader would not ask. Cover different parts of the diff and
different kinds of failure; never two questions about the same mechanism.

## 2. Answer them

Each answer is a closed task: read the code at `SHA` and say yes or no, with
the quote that settles it.

- **Where a subagent tool exists** (Claude Code `Agent`, or an equivalent),
  answer the questions in parallel with the smallest capable model (Haiku in
  Claude Code), several questions per subagent grouped by file. Give each
  subagent: the question, `SHA`, where to read (the local checkout path or the
  `gh api ...?ref=$SHA` form), the rule that it only reads and never runs the
  PR's code, and the output: per question, `defect: yes|no|unsure`, the file,
  the line, and the exact quote.
- **Without one**, answer them yourself in order.

Do not lower the effort of the answers to save time: on the same labelled
set, answering with less effort found 7 to 9 of 20 known defects against 10
to 12. The lever for speed is the number of questions, not the effort.

## 3. Verify what comes back (the main model)

An answer is a lead, not a finding. For each `defect: yes`, open the quoted
line yourself, confirm the quote is really there at `SHA`, trace the path,
and run the refutation in Step 4. Drop `unsure` unless you can settle it.
Merge answers that describe the same mechanism into one finding.
