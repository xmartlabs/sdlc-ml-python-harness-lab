# Read the results

The lab will happily show you a number. The skill is knowing whether the number
measured the agent, or the lab. Read every result in this order — the first two
steps exist because, on 2026-10-23, a whole grid of runs scored 0 to 3 out of 10
and none of it was about the models: the grader's container had no pandas.

## On an experiment page

**1. Is there a red or yellow banner at the top? Read it before anything else.**
The validity banner says when the comparison could not have measured what it
names. Its causes, in plain words:

| Banner | Means | Usually fix by |
|---|---|---|
| *These runs measured the connection* | runs died on the gateway, the CLI or the container, not on the task | the run log's last lines; Health |
| *The objective was never measured* | the validation commands could not run | the case's commands or `validation_requirements` |
| *The treatment never happened* | the arm that should have used the skill never used it | Activation, or the skill's description |
| *Nothing resolved on either side* | the task is too hard for every arm | an easier case, or a stronger model as control |
| *No headroom to detect an improvement* | the control already passes everything | a harder case |
| *The arm mixes treated and untreated runs* | the arm is not one condition | rerun the arm |

If the banner says *Comparison not valid*, stop: the rest of the page is
describing noise.

**2. Look at the verdict, and at its strength word.** The headline says which arm
did better and by how much, with a 95% interval. The strength word is the part to
believe: **exploratory** (one repetition — no direction claimed), **judgement**
(two or more), **validation** (five or more). An interval that crosses zero means
"we could not tell", whatever the point estimate says.

**3. Open the case matrix.** Per case: *fixed* (failed in control, passes in
treatment), *broken* (the reverse), *unchanged*. One case flipping carries a
headline on its own; check it is not that.

**4. For harness x model: read the panel, not the cells.** The **Harness x model**
panel gives the harness effect per model and how it changes from the smallest
model to the largest — "the harness effect shrinks from +47 pp on Haiku to +13 pp
on Opus". It calls the change *established* only when its interval excludes zero
and at least three cases were scored in every cell. The **Capability frontier**
panel, when cases carry `tier:` tags, gives per arm the highest tier done
*reliably*: every case passing in at least 4 of 5 runs. "Tier 2?" with a question
mark means "where the runs point, with too few cases to say".

**5. Read this experiment.** The button writes a plain-language reading of the
whole page — what it shows, what it does not, what to run next. It is a model's
reading of the numbers, not a new measurement.

## On a run page

- **Validation** — each check the case ran, *passed*, *failed* or *inconclusive*.
  Inconclusive means the check could not measure (a timeout, no network, a missing
  grader dependency); it counts for neither side.
- **Scores** come in three kinds and are labelled so: **Fact** (a command exited),
  **Observation** (read from the run's record), **Judgement** (a model's opinion —
  the rubric judge, DeepEval). Weigh them in that order.
- **The run list's failure label** says who failed: *model not served*,
  *CLI too old*, *endpoint down*, *harness error* are the lab's and are not counted
  as attempts; *agent error* is the agent's.
- **Run audit** reads the run's artifacts and reports how the agent worked: which
  skills it consulted, loops, whether it followed the working loop. It is the
  input to [improving the template](journey-improve-the-template.md).

## Across runs

- **Advanced → Compare** puts two to six runs side by side, field by field. It
  explains a difference; it does not establish one — an experiment does.
- **Advanced → Benchmarks** is the leaderboard by condition across everything the
  lab holds. Good for "which configurations have we tried"; not for a decision.
- **Evidence → Findings** is where a result you trust becomes a claim the team can
  see, with the experiment that supports it.

## When to believe a result

Believe it when the banner is clear, the strength word is *judgement* or better,
the interval excludes zero, the case matrix shows more than one case moving, and
— for a model or harness claim — you can name the cases it held on. Anything less
is a lead for the next experiment, and the page says so.

The statistics behind each panel: [ADR-0064](adr/0064-an-experiment-shows-its-uncertainty.md)
(intervals), [ADR-0066](adr/0066-harness-and-model-vary-together-only-in-a-full-grid.md)
(harness x model), [ADR-0069](adr/0069-a-model-is-judged-by-the-tier-it-does-reliably.md)
(capability). Every screen in detail: [Web UI guide](guide-web-ui.md).

**Next:** [bring your repo or skill](journey-bring-your-repo-or-skill.md).
