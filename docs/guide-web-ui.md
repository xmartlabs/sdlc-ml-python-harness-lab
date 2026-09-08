# Web UI guide — Agentic Harness Lab

The local web app (Vite on `http://localhost:5173`, FastAPI + Celery behind it)
is the operator surface for the lab: launch benchmark runs, watch them execute in
isolated containers, score them, and compare harnesses and models. This guide
walks the end-to-end flow and what each screen does.

Start it with `make app-up` (brings up api + worker + redis + frontend).

## The core loop

```
pick a Case → choose model + harness + prompt → Create run
   → watch it execute (logs stream live)
   → it finishes: SWE-bench auto-scores; a case with a rubric is LLM-judged
   → Compare runs across models / conditions
```

The LLM judge, the audit and the other in-process LLM calls go through one
transport: the AI gateway when `GATEWAY_BASE_URL` + `GATEWAY_TOKEN` are set,
otherwise the Claude Code CLI with a Claude credential
([ADR-0043](adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)).

Everything the web app runs also has a CLI equivalent (`make bench-run …`); the
form shows the equivalent command as a reference. The web lane and the CLI are
two front-ends over the same runner (`scripts/bench_run.py`).

That loop is one run at a time. A *comparison* needs more than one, and a comparison
you intend to act on needs a design — which is what the next section is about.

```
state a question → declare the arms → launch N repetitions
   → read the deltas and the case matrix
   → record a finding → test it with another experiment
```

## Four ways to use the lab

The screens below document *what each one does*. This section is the other half:
**which job you are doing**, and which screens that job touches.

The lab distinguishes four modes ([ADR-0028](adr/0028-evaluation-modes.md),
[ADR-0040](adr/0040-subject-validation-mode.md)), and they are not labels — each
one fixes a different set of factors and therefore licenses a different kind of
conclusion. An experiment declares its mode, and the lab **refuses a design the
mode cannot answer**.

| | **Mode A** — Harness evaluation | **Mode B** — Cross-repo | **Mode C** — Discovery | **Mode D** — Subject validation |
|---|---|---|---|---|
| **Question** | Does this change to the harness cause a better outcome? | Does the harness work outside the repo it was designed in? | How does this team work with agents? | Does the tool I built work reliably when an agent drives it? |
| **Held fixed** | repo, case, model | harness, model, task family | — | the subject, the case, the repo |
| **Varied** | the harness | the repository | — | harness and model |
| **Licenses** | a causal claim, with repetitions | where it holds and where it degrades | nothing — **exploratory evidence, not experimental evidence** | a pass rate **per arm** |
| **Never** | pool across repos | pool across repos | present as a measurement | pool across arms, or say anything about the harness |

### Mode A — did our change help?

The everyday question: a new skill, a rule, a prompt, a harness version.

1. **Experiments** → create one with mode `harness_evaluation`, your cases, and two
   conditions: a `control` (today's setup) and a `treatment` (the change).
2. Launch it with N ≥ 5 repetitions. Agents are stochastic; one trial per arm cannot
   tell a real difference from run-to-run variance, and the screen labels anything
   below 5 as exploratory.
3. Read the **experiment detail**: the condition table, the deltas, and — most
   usefully — the **case matrix**, which shows *where* the treatment helped.

The lab will reject the design if it varies the model or the repository as well:
whatever difference you measured could not be attributed to the harness.

### Mode B — does it travel?

1. **Repositories** → register the repos you want to cover.
2. **Experiments** → mode `cross_repo`, one condition per repository, everything else
   identical.
3. Read the result **per repository**. A mean across three codebases is not a
   measurement of anything — they are not equivalent conditions. What you legitimately
   learn is which repos the harness holds up in and which properties break it.

### Mode C — what can we learn from this repo?

No control, no causal claim. You are looking, not proving. Outputs are **findings**,
and everything they produce is labelled exploratory until an experiment tests it.

### Mode D — does my tool work?

The question most people arrive with, and the only one that needs nothing set up
first. You built something — an agent behind an API, an MCP server, a service
wrapper — and a skill that drives it. **Home → Validate your tool** walks it through
five gated steps: stage the skill, declare the subject and **an executable
validation command** (a hard block, because without one the runs execute and
report nothing), pick harness/model arms with no control role, watch the runs and
open a sandbox, and read **one pass rate per arm**.

Nothing is ablated, so the result says nothing about the harness. The sentence it
licenses is "this subject passed N of M under `<harness>`/`<model>`", and it never
adds the arms together. Lane C of
[guide-skill-validation.md](guide-skill-validation.md) is the walkthrough.

## Auditing someone else's repository — worked example

This is the flow that spans everything above: take a repository you did not write,
find out how an agent behaves in it, and turn what you see into something testable.
It has two halves — a **one-time setup** that ends when a case passes against the
repository, and a **workbench** you come back to afterwards.

### Setup: Audit a repository (three steps, once)

**1. Point at the repository.** The step opens a folder picker on the directory the lab
sits in. Each row is one folder: click it to open it, and the folders that are git
repositories say so — a branch icon, a `git` badge, and the **Audit this folder** button
sitting on the row itself, so choosing one is a single click rather than "open it, then
find the button". The breadcrumb above the list says where you are and every step in it
is a jump back; **Up one level** goes back one, and at the top it says so instead of
offering a dead button.

That one press registers the repository — the folder's own `origin` and branch supply the
URL and the ref — copies it into `data/checkouts/` and freezes it at the commit it is on.

For a repository that is not on this machine yet, register it with the form below the
picker and clone it yourself: the lab shows the exact `git clone` command with the
destination filled in, you paste it into your own terminal where your ssh keys and tokens
already work, and it watches for the clone, reports its HEAD, and you press **Pin this
commit**.

Either way the lab never contacts the remote and never asks for a credential (ADR-0042,
[ADR-0045](adr/0045-a-checkout-the-lab-can-be-given-not-only-watched.md)). Everything
downstream reads your local checkout at the pinned commit. `git pull` moves nothing until you press **Re-pin to HEAD**:
that is what lets two experiments a week apart compare agents rather than a moving
branch.

**2. Read what the repository already ships.** Rules files (`CLAUDE.md`, `AGENTS.md`),
native skills, agents, CI workflows, ADRs, and the validation gate inferred from its
own files — which you can **adopt** onto the repository in one press. Everything here
is *detected*; what the lab could not observe says "Not detected" rather than being
guessed at.

Then press **Run gate check**. It provisions the pinned commit in the runner image,
runs the repository's setup and its own validation commands, and stops — no agent, no
prompt, no model. Without it a red run is ambiguous between "the agent failed" and
"this repository does not build here", which look identical and mean opposite things.
A green gate check is what makes every later failure a fact about the agent.

**3. Get one case that passes.** Four ways to get one, all storing the case against
this repository:

| Source | When to use it |
|---|---|
| **AI draft** | Fastest. The assistant reads your checkout — README, layout, the repo's own rules file, its adopted gate — and drafts a task that repository plausibly gets asked for. |
| **From history** | Mines commits that changed source **and** its tests together: tasks the team performs and already knows how to verify. |
| **By hand** | You know exactly what you want measured. |
| **Existing case** | Reuse a case already in the catalog against this repository. |

> Running `build a Tetris clone` against a payments API measures nothing. This step is
> what makes everything after it interpretable.

**Freeze the case and its rubric before running anything.** A case edited after seeing
results is not a measurement. A derived candidate carries the commit it came from,
which is what lets you show later that the case predates the results.

Setup ends with a one-run validation against the repository (`native` harness) that
proves the case actually runs there. Pick the **Harness** and its **Executor model**
from the menus: `claude` and `codex` list their own CLI's model names and offer
*CLI default*, which needs no configuration at all, while `opencode` opens the
three-tier picker (source → provider → model) used everywhere else and starts on the
gateway's default. Switching the harness clears the model, because the two menus name
different things and a model carried across would name one the new harness cannot
reach.

### Workbench: the repository's own page

**Repositories → the repository** now has everything in one place: its **Checkout**
(re-pin, dirty-tree warnings), its **Readiness** and gate check, its **Cases** library,
its **Experiments**, and its **Report**.

The Experiments tab offers the two comparisons worth making, each with the checkout and
commit already fixed so the only thing varying is the harness:

- **Native vs native + skills** — same repository, same cases; the treatment arm gets
  the skills you pick. Leave **Enforce skill use** on when you are evaluating one
  specific skill: without it the arm measures the skill's *availability*, not its use
  (ADR-0041). Turn it off when the question is which skills an agent reaches for
  unasked.
- **Native vs the lab harness** — `native` against `injected`. This answers the
  question a client actually asks:

  > What does your harness add over how we already work?

  Injection copies the lab's skills, agents, adapter file and governance documents into
  the clone before the baseline commit — so they never appear in the agent's own diff.
  If their repo already ships a `CLAUDE.md`, it is displaced to `CLAUDE.md.native.bak`
  and the collision is recorded in the run's manifest: replacing a team's governance
  changes the thing being measured, so it is never silent.

**Read the runs.** The **Scores** panel separates facts (a command exited) from
observations (parsed from artifacts) from judgements (a model's opinion). The audit
reads **their** governance for a `native` run, not the lab's — so its rule-adherence
findings are about the rules the agent was actually given.

**Turn what you saw into claims.** Findings → record what the runs showed, with the run
ids as evidence, a recommendation, and the intervention that would implement it.
**Draft experiment** turns a finding into a design to review. A finding citing no runs
is labelled an observation; a finding an experiment **refuted** keeps its record.

The **Report** tab assembles all of it — the pinned commit, the gate check, the case
library, the experiments with their caveats, the accepted findings — as markdown you
can download and hand over.

```
Clone → Pin → Gate check → Case → Experiment → Finding → Report
```

**What this does not do.** The lab observes how an agent behaves in a repository; it
is not a static code-quality audit. What you can say afterwards is how *agent-ready*
the repo is and where an agent trips over it — missing tests, no lint gate, absent
conventions — each claim linked to runs. That is a stronger deliverable than a
scorecard, and a narrower one than it might be mistaken for.

## Where things live

The navigation is grouped by what you came to do, not by which table backs it.
Nothing was removed in the regrouping — the old paths still resolve, they just
redirect.

| Group | Screens | For |
|---|---|---|
| — | **Home** | choosing what to learn, and what needs attention |
| **Evaluate** | New evaluation, Experiments | stating a question and reading its answer |
| **Evidence** | Findings, Cases, Repositories, Skills | what the lab knows and what it measures against |
| **Advanced** | Runs, Compare, Benchmarks, Improvements | the operational machinery underneath |
| **System** | Health, Guide, Settings | the lab itself |

### Look and feel

The UI follows the Xmartlabs brand (`xmartlabs-branding` skill): Outfit for
text, Space Grotesk for headings, XL Pink as the single accent, and a dark
theme grounded in XL Dark. Everything visual lives in `frontend/src/styles.css`
as tokens and shared classes; pages do not carry `style={{}}` attributes. The
classes that every wizard shares:

| Class | What it is | Rule of thumb |
|---|---|---|
| `.stepper` | the numbered progress pills | `--done` (green index), `--current` (accent), disabled = not reached yet |
| `.pick-list` | a list of selectable rows | rows are full-width; the selected row tints (`accent-soft`) rather than fills, so its description stays readable |
| `.checkbox` | a checkbox or radio with its label | the control sits on the left; the base text-field sizing does not apply to it |
| `.tab-bar` / `.tab` | content switches | `.tab-bar--inline` when it sits next to a panel title |
| `.primary` / `.secondary` / `.ghost` / `.button-link` | actions, in descending weight | all lay out as `inline-flex`, so an icon aligns with the label |
| `.callout` (`--ok`, `--warning`, `--danger`) | inline notices | keep the icon as the first child |
| `.assist-card` | an optional AI helper inside a form | accent border, never a required field |

Icons come from `frontend/src/components/icons.tsx` (inline Lucide geometry),
never from emoji or text glyphs: emoji ignore the theme and render differently
on every platform. To check a change without opening a browser, screenshot the
dev server headlessly:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --window-size=1440,1400 --screenshot=out.png http://localhost:5173/evaluate/subject
```

## Screens

### Home — what do you want to learn?

The landing page asks one question and offers three answers. Each card states the
question it can answer, what it holds fixed, what it varies, and what it is **not**
allowed to claim — so the choice of mode happens before any data is collected
rather than after.

- **Validate your tool** → the five-step subject-validation wizard. First because
  it is the only door that opens on a fresh instance — no repository needed — and
  it leaves behind what the other two consume: a staged skill and a case with a
  real validation command.
- **Evaluate a harness change** → the wizard at **Evaluate → New evaluation**.
- **Audit a repository** → the three-step setup, then the repository's own page.

Cross-repo evaluation keeps its mode but not its card: it needs two registered
repositories, so its entry point lives on **Evidence → Repositories** and appears
exactly when the second repository does.

The **Validated runs passing** figure lower down pools every case, model, harness
and arm in the window. It is labelled as such because it is a sign of activity, not
an experiment result — the composite the lab refuses everywhere else.

Below the cards, **What needs attention?** lists only what someone can act on —
regressed cases, suggested findings waiting for review, findings with nothing
testing them, finished runs with no scores, failed experiment arms, experiments
below five repetitions, repositories with no validation gate. Every item carries
its own way in. When there is nothing to do it says so instead of filling the
space.

Under that, an **evidence summary** of six figures, each with its denominator. The
full leaderboard, the model and prompt filters and the official SWE reference rows
moved to **Advanced → Benchmarks**: a home page should answer *what should I do
next*, not *what data exists in the system*.

### New evaluation — the wizard for Modes A and B

**Where the cases run** is the first choice, and it decides which loop you are in:

- **This lab** (the default) — the harness under test is the lab's own governance,
  the synced `ml-python-base` release. This is the continuous-improvement loop the
  lab was built around: change a skill, measure it, cut a release, re-measure. No
  target repository is involved, and none is asked for.
- **An external repository** — the agent works inside someone else's codebase,
  cloned fresh per run. Only here does *Governance source* (`native` vs `injected`)
  mean anything, because only here are there two governance surfaces to compare;
  inside the lab that factor is disabled and says why.

The rest of the form is built so it cannot express a design the lab would refuse.
One factor varies at a time — one or **several** skills (several are tested as a
bundle: a win belongs to the bundle, not to any single member) — everything else
is pinned across the arms, and in cross-repo mode each repository becomes its own
arm and the results stay separated.

**Full harness** is the founding comparison, as a factor: a bare arm (plain
prompt variant, every governed skill hidden) against the whole harness (the
governance-loaded variant with the full skill catalog). Two levers move
together on purpose — the claim is about the harness as a unit; ablate factors
afterwards to see which parts do the work. The prompt pair is drawn from the
selected cases' own variants (`naive`→`sdlc`, `swe_default`→`swe_harness`), and
`prompt_variant` is a first-class condition axis for exactly this reason.

Setting **both arms to the same prompt** is allowed, and is often the better
design: the bare arm still hides the whole governed catalog, so the arms remain
distinct conditions and the experiment varies one axis instead of two — which is
what the backend's own confounding rule asks for. A case written in the Cases
screen declares a single prompt (`default`), so the same-prompt pair is its only
shape, and the honest one: such a prompt names no governance for the bare arm to
lose. The wizard runs it and says what it measures rather than refusing. It is
refused only when the arms would be genuinely identical: same prompt with no
governed skills to hide.

Read a same-prompt result as a claim about the **skill catalog**, not about the
harness as a unit — and inside *This lab*, a narrower one still: the ablation
rewrites only the generated skills region of `CLAUDE.md`, so the governance body
survives in **both** arms. For a control side carrying nothing of the harness,
use *Governance source* (`native` vs `injected`) against an external repository
([ADR-0031](adr/0031-harness-source-is-a-run-condition.md)). The wizard prints
both notes above the arm list.

**Ask the agent to use the harness** appears under the prompt pair whenever the
factor is *Full harness*, and is **on by default** — as is *Enforce skill use* for
*Add skills*. Both default on because the treatment there is a skill the arm already
has, and an arm that never invokes it measured availability
([ADR-0046](adr/0046-an-experiment-reports-validity-before-it-reports-a-verdict.md)).
Turn it off only to ask the different question: whether the agent reaches for the
skills unprompted. *Remove skills* leaves it off, since its treatment is removal. A prompt written in this UI says nothing about
governance, so the full-harness arm may never open a skill and the pair would
measure only what sat on disk. On *Full harness* the skill grid also starts with
the **whole catalog selected** — what that factor names is the catalog, and an
activation block built from an empty selection is empty, which is how a
full-harness arm reached its runs with `skills_invoke: []` and invoked none of
its skills. Untick individual skills to narrow the block. The selection is named in an
activation block at the start of every step of that arm (`skills_invoke`,
[ADR-0041](adr/0041-a-skill-under-test-is-invoked-not-just-available.md)). The
bare arm never receives it — it cannot see those skills at all, and a run told
to invoke a skill it cannot see is refused at preflight. "Add skills" offers the *staged* catalog and
"Remove skills" the *governed* one, because that is what `skills_extra` and
`skills_disabled` each accept.

**Enforce skill use** sits under the skill list for both factors. Off, the
selected skills are only *available* to the arm that has them, and the agent
opens them only when the case or the skill's own triggers call for it — the
test of "does it reach for them unasked", the right one for a whole harness of
skills. On, that arm is also told at the start of every step to invoke them and
follow them (`skills_invoke`, ADR-0041) — the test of "does it help when asked
for", the usual one for a single skill. It applies to the whole selection: a
multi-selection is either all asked for or all left to the agent. Whichever you
choose, every run records `skill_invoked` and `skill_invocations`, and an arm
that never opened its skills gets that said at the top of its verdict.

**How it runs** carries the classic run form's own controls — the three-tier
model picker, the harness, the prompt variant (union of the selected cases'
variants), the step budget, dry run — so the wizard is the whole journey.
**Create & launch runs** queues arms × cases × repetitions in one step;
*Create without launching* leaves a draft the experiment page can launch later. The panel at
the bottom shows the exact arms about to be created, and lists what is blocking
creation until nothing is.

**Check config** runs the same creation preflight as the classic form, and it
checks the *shared* configuration only — case, models, harness, budget — never
the per-arm prompts or skills. So a green "Ready" is withheld while anything is
blocking creation, where it would otherwise read as permission to launch; a
refusal is always shown, since it carries a reason the blockers cannot. Editing
anything that shapes the arms voids the verdict, which describes only the
configuration that produced it.

Repetitions are chosen from three named levels — **2 (exploratory only)**,
**5 (useful comparison)**, **10 (stronger evidence)**. Below five, the lab will
describe the result as exploratory however large the difference looks.

The experiment's id is derived from the question. Asking the same question
again — the usual case being *the same comparison, run on a different harness* —
does not collide: the wizard files the new experiment under the next free id
(`does-x-improve-outcomes-2`) and says so, leaving the earlier experiment and its
runs untouched.

Ticking **Use the regression suite** replaces the case picker with a suite's own
declared design — its canonical cases and its per-case repetition counts
([ADR-0037](adr/0037-regression-suite-produces-experiments.md)). What comes back is
an ordinary harness-evaluation experiment, read through the same case matrix. This
is how a harness release is validated: previous version vs new, over the suite,
producing **fixed / broken / unchanged**.

### Repository audit — Mode C: setup, then a workbench

Setup runs once per repository, at **Evaluate → Audit a repository**:

1. **Point at the repository** — pick the folder you already have on this machine and
   the lab registers, copies and freezes it in one press; or register a remote one and
   run the `git clone` command it writes out in your own terminal, where your
   credentials already work, and **pin** its commit
   ([ADR-0042](adr/0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md),
   [ADR-0045](adr/0045-a-checkout-the-lab-can-be-given-not-only-watched.md)).
   Nothing after this reads the remote.
2. **Repository readiness** — what the lab could actually read at the pinned
   commit: validation gates, setup, and any AI governance already there (rules
   files, skills, agents, commands, ADRs, CI). Anything it could not observe says
   **Not detected** rather than being guessed at. "Ready" is a narrow claim — a run
   would execute and be scored — and says nothing about how good that governance is.
   The **gate check** here runs the repository's own commands at the pin with no
   agent, which is what makes a later failed run readable.
3. **One case that passes** — drafted by the assistant from your checkout, mined
   from the repository's own history
   ([ADR-0032](adr/0032-cases-are-derived-from-a-targets-own-history.md)), written by
   hand, or reused from the catalog. Setup ends when the case validates against the
   repository.

Afterwards the repository has its own page under **Evidence → Repositories**, with
its checkout, readiness, case library, experiments and report. Two experiment presets
live there — `native` vs `native + skills`, and `native` vs the lab harness — both
with the pinned commit fixed so the harness is the only thing that varies. Findings
stay on **Evidence → Findings**, and the report links to them.

### Benchmarks (Advanced) — the leaderboard and the overview
Leads with what the lab knows about itself, computed server-side
(`GET /api/overview`) so the numbers cannot drift from what the same definitions
mean elsewhere:

| Figure | What it counts |
|---|---|
| **Harness version** | the version the most recent run carried |
| **Success rate** | objectively-validated runs that passed, in the window. A manual-score run is *unknown* and is excluded, never counted as a failure |
| **Regressions** | cases whose latest verdict is a failure **after an earlier pass**. A case that has only ever failed is an open problem, not a regression |
| **Median cost** | the median, not the mean — one runaway run distorts a mean badly. Beside it, cost per success, which is the figure a decision turns on |
| **Regression suite** | active cases carrying their own objective validation, i.e. the ones that can gate a change without a human |
| **Experiments** | created in the window |

Every figure shows its **denominator**. A rate over four runs and one over four
hundred are different claims, and the screen refuses to render them the same.

**Quality over time** plots the weekly success rate. A week with no validated run is
left blank rather than drawn as zero — a gap on the baseline would show a collapse
that never happened.

**Harness signal** is the second panel, and it answers a different question from
everything above it. The figures in the header come from `Validation` — *did the runs
pass, what did they cost*. These come from the score registry — **is the harness
earning its keep**:

- **Skill adherence** — the share of the available harness an auditor judged genuinely
  used. A harness of 24 skills an agent reaches two of is not a good harness, and no
  other figure on the page would say so. It is a *judgement*, and labelled one.
- **Items useful / redundant per run** — the per-item verdicts, averaged.
- **Diff size** (median) and **boundary violations** (forbidden paths touched).

When adherence falls below 40% the panel says what to do about it: that is a finding
about the *harness*, not the model — read the per-item verdicts in a run's **Harness
catalog**, ablate the items that never earn their keep, and measure the difference.

**Worth attention** surfaces what is actually actionable: regressed cases by name,
finished runs with no scores yet (scoring is on demand, so those are missing from
every experiment summary), and open findings with nothing testing them.

Below the overview is the lab's **leaderboard**, swebench.com-style: every terminal
run counts as one attempt, grouped by **condition** (harness + executor model +
prompt variant + harness version) and ranked by **% resolved** with a
proportional bar.

- **% resolved is per-attempt**: resolved runs / objectively-validated runs of
  that condition. Manual-score cases (e.g. tetris) have no objective verdict, so
  they show avg/best score but no %.
- **Filters**: lane (case type), harness, model, prompt variant, and **harness
  version** — so you can ask "does harness v0.4.0 + conductor + qwen3.6-35b beat
  the bare model?" directly. The per-run version comes from `template_ref` in
  `metadata.json` (recorded at run time; older runs fall back to the harness
  fingerprint hash).
- **Official reference rows** (toggle) come from the curated file
  `data/reference/swebench_official.json` — edit it by hand to refresh (agent,
  model, `pct_resolved`, date, source URL per row). They render with an
  **oficial** badge as a thermometer against the real leaderboard.
- ⚠️ The comparison is **indicative**: your % runs over the lab's curated subset
  and your conditions; official rows are pass@1 over the 500 SWE-bench Verified
  instances.

### Runs
Under **Advanced → Runs**, where single runs are launched and tracked. The
everyday entry is **Evaluate → New evaluation** (an experiment with a control
and a treatment); the **New run** form is the one-run alternative:

- **Case** — what the agent is asked to do (see Cases). Selecting a case sets the
  available **Prompt variants**.
- **Harness** — the agent scaffold under test: `opencode` (containerized,
  gateway models), `claude` (containerized too, with `CLAUDE_CODE_OAUTH_TOKEN`
  or `ANTHROPIC_API_KEY` in `.env`; host mode also works) or `codex` (host mode
  only, `make app-host`, native OpenAI login). To test claude/codex, follow the
  [claude/codex runbook](runbook-claude-codex.md); design in
  [harness-adapters.md](harness-adapters.md).
- **Planner / Executor model** — chosen in three tiers, **source → provider → model**:

  1. **Source.** *AI Gateway* routes through LiteLLM (Langfuse traces, per-model
     `max_tokens` caps, one name mapping in the middle). *Direct (local)* talks
     straight to your own hosts — LM Studio at `LMSTUDIO_BASE_URL`, Ollama at
     `OLLAMA_BASE_URL`. The distinction used to be invisible: a heading reading
     "LM Studio (local)" actually held gateway aliases, so two very different
     routes looked like one menu.
  2. **Provider.** Chips with a count. Under the gateway: LM Studio, Ollama,
     OpenAI, Claude, Gemini, NVIDIA, Other — split by id prefix, the only signal
     LiteLLM's `/models` list carries; an id no rule matches lands in *Other*
     rather than disappearing. Under Direct: one chip per host that answered.
  3. **Model.** The list for that source and provider, launchable models first.
     Under the select, one line states what the pick means for a run: a direct
     model's loaded window, or *not routable* for a gateway model nothing can
     reach (enable it on the Settings page first).

  A gateway card can be routable and still be dead, and those are different
  problems: *not routable* means this harness has no way to address the id,
  *backend unavailable* means the machine behind the card is not serving it —
  the LAN host is off, or the card declares no `api_base` and LiteLLM would
  silently send the request to its own default host. The picker reuses the
  direct-host probe it already ran for the Direct source, so the check costs no
  extra round-trip, and it fails open: a host nothing probed keeps its verdict
  open rather than being hidden. Dead cards still sort below working ones and
  carry the reason in the line under the select and as a badge in Settings.

  Gateway models come **live from LiteLLM** — add one to the gateway, press
  **Refresh from gateway** in Settings, and it appears here and becomes routable,
  no hand-edited lists. Direct models come from the host itself, carrying the two
  facts that decide whether a run works: whether the model is loaded and with how
  much context. Their ids are the ones the host registers, so no alias can rename
  or reroute them in transit.

  A source with nothing in it stays **clickable and says why** — "LM Studio at
  http://…:1234 did not answer", per host, since one machine can be up while the
  other is down. A disabled button with no reason is the one thing this picker
  must not do.

  **⟳ Reload** re-reads everything, dropping the server-side caches. The lists
  have separate cache lifetimes, so this is the one action that guarantees a
  model you just loaded is visible.

  See [ADR-0012](adr/0012-dynamic-gateway-model-list.md),
  [ADR-0016](adr/0016-opencode-config-overlay-for-model-routing.md) and
  [the direct lane](local-model-runtime-config.md#the-direct-lanes-skip-the-gateway-when-you-are-iterating).
- **Score mode** — web runs are `manual` (interactive is CLI-only), but
  "manual" now means *a human may overwrite it*, not *a human must supply it*:
  SWE-bench cases auto-score from their objective verdict, and any case that
  carries a `scoring_rubric` is graded against that rubric by the audit LLM
  (recorded with provenance `llm_judge`). A case with no rubric and no
  objective verdict is the only one that still waits for a person.
- **Max steps**, **Resume**, **Dry run** — run controls. Max steps is
  prefilled from the selected case's own `max_steps`, so a one-step case
  does not silently advertise a ten-step budget.
- **Skills** ablation — disable governed skills or enable experimental staged
  skills *for this run only*, to A/B the harness. A non-empty ablation makes the
  run a distinct arm (`condition_hash`). See [ADR-0011](adr/0011-per-run-skill-ablation-and-staging.md).
- **Validate config** — runs the creation preflight (`POST /api/runs/preflight`)
  without creating anything: case and harness availability, `GATEWAY_TOKEN`,
  whether the executor is routable and reachable, and the context window the
  backend is *actually serving* (ADR-0020). A green verdict means Create run will
  not be refused; on a cold model the first check pays the JIT load (~40s), and
  the measurement is memoized so pressing Create right after costs nothing extra.
  Editing any field clears the verdict, since it only describes the configuration
  that produced it.

#### Batch mode — queue a whole comparison at once

Every comparison worth making is more than one run: a harness A/B is two, a model
sweep is four, and telling a real difference from run-to-run variance means
repeating each of those. **Batch mode** builds that grid in one submission
([ADR-0023](adr/0023-batch-run-execution.md)).

Press **Batch mode** next to the preset button. Four of the single selects become
additive checkbox lists, seeded from whatever you currently have selected — so the
batch starts as "what I was about to run" and you widen it from there:

- **Cases**, **Prompt variants**, **Harnesses**, **Executor models** — each list is
  an axis. Every combination is queued.
- **Target repositories** — the axis that answers "does this hold outside the repo
  it was designed in?" ([guide](guide-target-repositories.md)). Read those results
  per repository: a mean across different codebases is not a measurement, because
  the repositories are not equivalent conditions.
- **Repetitions** — trials per combination.
- **Prompt variants** offers the *union* across the selected cases, not the
  intersection: cases legitimately define different variant sets, and an
  intersection would empty the axis as soon as one case is unusual. Each case is
  then paired only with the selected variants it actually defines, so selecting
  `tetris-v1` (which has `conductor`) alongside `cli-textstats-v1` (which does
  not) queues `conductor` for the first case only. **The stated run count already
  accounts for this**, which is why it can be lower than the product of the axes.
  A case that defines *none* of the selected variants is rejected rather than
  silently dropped — the batch would not be the one you asked for.
- Leaving **Executor models** empty means "do not vary the model" — the harness
  default is used for every run.

Planner model, max steps, dry run and the **Skills** ablation are held constant
across the batch. That is the point: those are the variables you are controlling.

The submit button states the cost before you pay it — **Queue 24 runs** — and
refuses to submit past `HARNESS_LAB_MAX_BATCH_RUNS` (default 50). The count is
worth reading: each unit is a Docker container spending model budget.

Validation is **all-or-nothing**. Every combination is preflighted before anything
is created, so a rejection means nothing was queued and there is no half-executed
batch to unpick. Preflight is deduplicated by executor and harness, so a 24-run
batch over two models pays two context probes, not twenty-four.

On success the form reports the `batch_id`. Paste it into the run list's search
box to narrow the table to exactly that batch; runs belonging to one carry a
**batch** badge.

`resume` is not available in batch mode — it resumes one specific workspace, which
means nothing when you are asking for N fresh trials.

The **run list** identifies each run by its **case title** (the same descriptive
name you see when importing a SWE-bench Pro instance), with the attempt id
(`K-009`), the short instance id, and the `F2P n / P2P m` test counts underneath.
Hovering the run cell shows a tooltip with the exact run id, the case id, the
test counts, and the full CLI command — so the row stays scannable and nothing
is lost.

It also shows, per run, its **harness** and **executor model** (parsed from the
command, for fast visual scanning), state, score, **created** age, and an
**Improvements** column.

Beside the state, a **checks failed** / **checks passed** badge reports the
automated verdict when one exists ([ADR-0021](adr/0021-benchmark-artifact-integrity.md)).
It matters most for manual-score cases: a run whose produced code does not even
start otherwise looks identical to one waiting for your review. No badge means
the checks have not run or the case has no objective criterion.

A failed run also says *why*, in one word, with the full reason on hover:

| Badge | Meaning | Counts as an attempt? |
|---|---|---|
| **lab error** | Never started — runner image missing, Docker unreachable, bad config | No |
| **worker restarted** | Killed mid-flight by a restart; retry resumes it | No |
| **endpoint down** | `run_max_stream_errors` provider requests failed back-to-back — or the run ended, however cleanly, with every model request it ever made having failed | No |
| **no response** | The provider accepted the request and answered nothing for `run_model_wait_seconds` | No |
| **stalled** | Ran, then went quiet for `run_stall_seconds` with no model request in flight | Yes |
| **timed out** | Exceeded `run_timeout_seconds` | Yes |
| **agent error** | The agent's own output ended in an error | Yes |

The first four carry a **no result** note and are **excluded from the leaderboard**:
nothing about the model or the harness can be read from them, and counting them
would inflate a condition's attempt total with runs the model was never given the
chance to get wrong. Retry them instead. The classification lives in
`src/harness_lab_app/run_failures.py`, so the list, the API and the leaderboard
share one definition.

- **Created** shows the run's age (`5h ago`); its tooltip carries the exact
  creation timestamp plus the last execution window — start → end and how long
  it took. Timestamps come from the API as naive UTC and are rendered in your
  local timezone (`frontend/src/dates.ts`).
- **Search / Harness / State** filter the list; **per page** (10/25/50/100) plus
  the pager at the bottom keep long histories navigable. Every column header
  except *Actions* is a **sort** toggle (click again to flip direction); the
  default is newest-created first.
- Filters and sorting are client-side over the runs already loaded — no extra
  API round-trip, and the counter in the panel header shows `N of M runs` while
  a filter is active.

The **Improvements** column tells you where the run stands in the improvement
cycle — derived from `data/runs/<id>/improvements.json`:

- **yes** — synthesis ran and proposed at least one change (go save/review them);
- **no** — synthesis ran and proposed nothing (clean run / outcome gate closed);
- **pending** — synthesis hasn't run yet (open the run and generate it).

Create the run and you land on its **detail page**, which opens with the
**Condition** — the experiment and arm this run belongs to, the case, the
repository and the resolved commit, the harness, the governance source, the model
and the step budget, and the validation gate it will be judged by. A log is only
interpretable as a member of a design, so that comes first and the logs, artifacts,
validation & score panel, attribution, LLM audit and in-platform sandboxes (web
terminal / desktop) follow underneath.

A run created outside an experiment says so — **ad-hoc run, not part of an
experiment** — rather than being drawn as though it were an arm of something.

The **attribution** panel's governance coverage now names its own denominator: which
repository's governance surface the fraction is over, under which `harness_source`,
with its fingerprint. The **LLM audit** carries the same surface as a collapsible
file list. A `native` run is judged against the target repository's own governance
and an `injected` one against the harness the lab actually put there — never against
rules the run did not receive
([ADR-0036](adr/0036-audit-judges-a-run-against-its-own-governance.md)). Audits
recorded before that say so instead of showing a surface they never had.

Inside either sandbox, a run whose output is a web page opens with `open-html`:

```bash
open-html index.html   # desktop: a Chromium window; terminal: /work/page.png
```

On the **desktop** sandbox it opens a Chromium window next to the xterm; on the
**terminal** sandbox there is no display, so it renders the page headless to
`/work/page.png` (set `SHOT_PATH` to change that) — download it from the
artifacts panel or read it from `data/runs/<id>/sandbox-terminal/`.

#### When a run stops making progress

Four budgets end a run that will not end itself, and they catch different things:

- **Run timeout** (`run_timeout_seconds`, default 7200) — the total budget. It
  catches a run that is too *slow*.
- **Stall timeout** (`run_stall_seconds`, default 600) — time since the last line
  of output **while no model request is in flight**. It catches a run that is
  *stuck*, which the total budget structurally cannot: a hung run and a healthy one
  look identical from outside, so silence needs its own clock.
- **Model wait** (`run_model_wait_seconds`, default 2400) — the same silence, but
  while the provider owes the run a first token. It gets its own, much larger
  budget because a local model says nothing while it processes the prompt.
- **Endpoint errors** (`run_max_stream_errors`, default 5) — consecutive failed
  provider requests with no progress between them. opencode retries a failing
  stream forever, so without this a run spends its entire budget on an endpoint it
  cannot reach.

One failure shape slips past all four budgets: the run *ends on its own* before
any threshold trips. Run `…qutebrowser…-K-003` made two provider requests, both
rejected in two seconds by a gateway configuration error (`max_tokens` above the
model's completion cap) — fewer than `run_max_stream_errors` — and the harness
script then carried on to scoring, archived an empty diff and exited 0. Recorded
as completed, it would have gone on the leaderboard as the model attempting the
case and resolving nothing. The lab therefore checks one more thing at the end
of every run: if it made model requests and **not one of them was answered**, the
run is marked failed as **endpoint down** regardless of its exit code, and does
not count as an attempt.

A stalled run is marked **failed** with the reason and the last thing it said
before going quiet — normally the tool call it is wedged inside. Run
`tetris-v1-K-034` is why this exists: the agent shelled out to a pygame process
that caught SIGTERM and never died, the surviving grandchild held the stdout pipe
open, and opencode blocked reading it. Forty-seven minutes of nothing, state
"running", and only the two-hour ceiling would ever have ended it.

Which clock applies is read off opencode's own log, and getting that wrong is not
academic: with one 600s clock for both silences, every 35B run died at its first
turn while still processing the prompt and was filed as the agent wedging the
harness ([ADR-0022](adr/0022-watchdog-splits-model-wait-from-agent-stall.md)). A
model-side failure is never the agent's fault, so **no response** and **endpoint
down** do not count as attempts, while **stalled** does.

The tell, if you want to confirm by hand: `ls -l data/runs/<id>/opencode.log` (has
it moved?) and `docker top harness-run-<id> -eo pid,etime,stat,pcpu,args` (a child
whose elapsed time is absurd for what it is — a `timeout 2` alive for 47 minutes).
Traffic on your model host is the other signal, and it separates the two silences:
a stuck agent is blocked in a tool call, a prefilling model has your GPU pinned.

When a budget ends a run, the harness stops the container itself, so the exit code
that follows is its own SIGKILL (137) rather than anything the agent did. The run's
last event therefore leads with the reason and keeps the code as context — `Run
timed out after 7200s (exit code 137)` — instead of the bare `Run exited with 137`
that read like a crash the lab never had.

Run detail reports **Queued for** and **Execution** separately for the same reason.

#### Continue — push a stopped run further

Once a run has stopped, its detail page offers **Continue**: write a
follow-up prompt and the lab queues a *child* run whose workspace is seeded
from this one's final state (`<parent>-C-001`). The parent is not modified,
so its score and provenance keep describing what actually produced them.

A continuation is a human intervention, so continued runs are hidden from
the **Benchmarks** page by default — tick *include continuations* to see them. Full
walkthrough: [Continuing a run](guide-run-continuation.md),
[ADR-0026](adr/0026-run-continuation-as-child-runs.md).

#### Scores — the three kinds of claim, drawn apart

Recorded automatically when a run reaches a terminal state, and refreshed by a
re-validate. **Score this run** backfills a run that predates automatic scoring.


**Score this run** records its metrics against the versioned registry
([ADR-0034](adr/0034-score-registry.md)). Each row shows `name@version` and its
**provenance**:

- **fact** — a command exited. Reproducible without judgement.
- **observation** — parsed from the run's artifacts. True of the record, not
  necessarily of the world.
- **judgement** — a model's opinion. Defensible, not checkable.

They are drawn differently on purpose. `test_pass_rate = 1.0` and
`skill_adherence = 0.81` look identical once both are numbers in a table, and the
second borrows the authority of the first.

#### Send traces — read the run turn by turn

When Langfuse is configured, the run header gains **Send traces** and **View in
Langfuse**. The export is automatic at the end of a run; the button is for
historical runs and for re-sending after a Langfuse outage. Re-exporting updates
the same session instead of duplicating it, so it is always safe to press.

Nothing appears here when tracing is off, which is the default. Full
walkthrough: [Langfuse observability](guide-langfuse-observability.md),
[ADR-0027](adr/0027-langfuse-traces-rebuilt-from-run-artifacts.md).
The worker runs at concurrency 1, so a batch queues behind itself and a run can sit
for hours before its container starts; a single created-to-ended duration cannot
tell a slow model from a busy queue, which is exactly the question you are asking
when a run looks slow.

The clocks are visible read-only under Settings → Runtime facts. Any of them can be
disabled with `0` (`HARNESS_LAB_RUN_STALL_SECONDS`,
`HARNESS_LAB_RUN_MODEL_WAIT_SECONDS`, `HARNESS_LAB_RUN_MAX_STREAM_ERRORS`); see
[local-model-runtime-config.md](local-model-runtime-config.md) for how to pick
`run_model_wait_seconds` for a slow model.

### Cases
The catalog of tasks. Four ways to get realistic cases:

- **Draft it with AI** — describe the case in one sentence and the assistant
  shapes the whole thing: case type, a free id, name, description, the
  instruction the agent receives, the per-step plan for a `multi_step` case, the
  step budget, and the validation commands. Every field lands editable, so the
  draft is a starting point rather than a decision, and any command the lab
  could not run is listed with the reason instead of being dropped in silence.
  It uses the same transport as every other LLM call — your gateway, or the
  Claude Code CLI when that is all that is configured
  ([ADR-0043](adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)).
  Leave **Model** on *Transport default* unless you want a specific one.

- **tetris-v1** — the seeded pilot (build a game; smoke floor + a 0-10
  rubric the LLM judge grades against, overwritable by hand).
- **SWE-bench** — import a real GitHub issue+fix from SWE-bench Lite/Verified via
  the *Import SWE-bench* box or `make swebench-import INSTANCE=<id>`. These
  **auto-score objectively** (resolved → 10). Only a validated subset runs in the
  generic runtime — see [swebench-curated-set.md](swebench-curated-set.md).
- **Objective agentic cases** — dataset-free `build_from_scratch` tasks with
  exit-code validation, e.g. `benchmarks/cli-textstats/`
  (`make case-import TASK=…`). Great for trying a new model on a self-contained
  task with an objective floor.

**Write the validation commands, whichever way you got there.** The New case
form takes them one per line, and a case without any cannot score itself: every
run of it stops in `awaiting_manual_score` waiting for a person
([ADR-0038](adr/0038-every-run-leaves-with-a-score.md)).

#### Managing a case from the browser

Selecting a case opens the **case editor** below the list. Everything a case is
made of is editable there, and nothing about the create → edit → run loop needs
a shell.

- **Case ID is fixed.** It is the key every run in the history joins on, so
  renaming it would orphan past results. Use **Clone** to start a variant.
- **Prompt variants** are the strategies you want to compare — one prompt each,
  named. The run form picks one by name, and the leaderboard groups by it.
- **Step prompts** apply to `multi_step` cases: step 1 uses the prompt variant,
  and each entry drives the step with the same number. Leave the list empty and
  the runner falls back to its generic continuation prompt.
- **Validation commands** run in the sandbox after the agent finishes; a
  non-zero exit fails the objective score. `{run_id}` and `{workspace}` are
  substituted.
- **SWE-bench cases are read-only.** Their prompts and test lists *are* the
  benchmark; editing them would silently invalidate every comparison already
  recorded. Clone one to build a variant you can change.

A case created here lives in the database only, which is all the runner needs:
the worker stages the case as `data/runs/<run-id>/case_spec.json` before the
container starts. That is what the `not_synced` badge means — not a problem, and
not something to fix before running.

**Sync to YAML** is the export path, not a prerequisite. It writes
`benchmarks/<case-id>/task.yaml` plus one prompt file per variant, so the case
can be committed to the repo and driven from the CLI lane. After a sync, later
edits show as `modified_since_sync` until you export again.

### Experiments
A question, declared arms, and the runs that answer it
([ADR-0033](adr/0033-experiment-above-run.md)). The difference from a batch is that
an experiment makes a claim, so the lab checks the design against the declared mode
and **refuses one that cannot answer it** — a comparison varying both the harness and
the repository produces a number attributable to neither.

The detail view opens with a **validity card whenever the comparison has a problem**,
above the verdict and never inside it
([ADR-0046](adr/0046-an-experiment-reports-validity-before-it-reports-a-verdict.md)).
It answers a question the verdict cannot: *could this comparison have measured the
thing it names?* Five things put a card there:

- **The treatment never happened** — an arm carrying skills opened none of them in any
  run. It measured the skills' availability, not the skills.
- **These runs measured the connection** — half or more of an arm's runs died in the
  harness (`harness_error`) or failed outright, or the arm has no scored runs at all.
- **No headroom** — the control passed every repetition, so only "no change" or a
  regression was ever representable.
- **The arm mixes treated and untreated runs** — it invoked its skills in some and not
  others.
- **The design was objected to when it was created** — a warning the wizard showed you
  and you created the experiment anyway.

When any of these is severe, the headline becomes **Comparison not valid** and the
sentence the lab would have led with moves down to *Measured: …*. Nothing is hidden:
the deltas, highlights and case matrix are all still there. They stop being the first
thing read, which is the whole change.

Under it, the **verdict**: whether the treatment improved things, on
which metrics, at what cost, and how much evidence stands behind it — "12 cases × 5
repetitions" — plus the case movements as `fixed / broken / unchanged`. The wording
is deliberately cautious. The lab runs no hypothesis test, so it never says
*significant*; it says exploratory, useful comparison, stronger evidence, within
observed variance, or improvement observed.

Under that it keeps outcome, efficiency and behaviour in separate rows, shows every
mean with its spread, colours a delta by the metric's **declared direction** (a drop
in cost is a win; a drop in pass rate is not), and flags a delta that sits inside
run-to-run variance. Below it, the **case matrix** shows where a treatment helped and
where it did not — an average of "+8 points" can be "fixed four cases, broke two".

Two caveats are raised automatically because they are the ones that read as
results when they are not:

- **Beyond noise is left blank when there is no noise to compare against.** One
  repetition per arm has no spread, and repetitions that all land on the same
  value make the noise floor exactly `0.0` — which would wave *any* non-zero
  delta through as "beyond noise". Coarse metrics at low repetition counts hit
  this constantly, so the answer there is *unknown*, not *yes*.
- **Arms that stopped for different reasons carry a warning.** Every run records
  a `stop_reason` (`completion_signal`, `budget_exhausted`, `no_progress`,
  `swe_resolved`, `harness_error`). An arm that ends on a completion signal and
  an arm that burns its whole step budget are not two measurements of the same
  quantity: the gap between them is partly the stop criterion, not the work, and
  effort and cost deltas must be read with that in mind.

`agent_steps` counts **harness restarts, not work** — one invocation that ran
fifty turns counts 1. `agent_turns` and `agent_tool_calls` are the work metrics;
compare those when you want to know how much the agent actually did.

**Run this experiment** launches the declared design: you supply only what every arm
shares (model, harness, step budget), and anything the design varies is overridden
per arm afterwards — so a value typed there cannot leak into the comparison as a
confound.

Low repetition counts are labelled wherever they appear. One trial is an exploratory
result, and the screen says so rather than letting it look like a finding.

Two lifecycle actions keep the working set honest, and they are deliberately
different claims:

- **Abandon** hides the experiment from the default list (a *Show abandoned*
  toggle brings it back) and from the attention feed. Its runs **stay in the
  corpus** — abandonment is "stop showing me this", never "this never happened".
- **Delete** erases the measurement: the experiment, its conditions, its runs,
  their configs, artifacts **and scores** leave every dashboard and aggregate.
  An experiment with runs is only deletable together with them (half a deletion
  would strand orphan measurements — the very bias a cleanup is trying to
  remove), and one with pending or running arms must be cancelled first.

### Findings
What the runs showed, what to do about it, and whether that was tested
([ADR-0035](adr/0035-findings-and-manual-maturity-assessment.md)). A finding carries
its evidence, a recommendation, the intervention that would implement it, and the
experiment that settled it. A finding citing no runs is labelled an observation; a
finding an experiment **refuted** keeps its record.

**Draft experiment** turns a finding into a design to review — not into a created
experiment, because the harness axis the intervention changes is a choice a sentence
does not determine.

The **Suggested** tab is the review queue for patterns the lab noticed in the run
corpus: a case that regressed, a case that has never passed, harness items the
auditor keeps judging unused. Each carries its evidence and an editable
recommendation, and each needs a name before **Accept** or **Reject** enables — a
finding nobody owns is an assertion nobody has to defend. A rejection is recorded
too, so the same pattern is not raised again next week.

### Repositories
The repositories a case can be run against. By default a run works in the lab's own
workspace; registering a target lets the same case run inside another repo — your
team's service, a client's codebase — cloned fresh per run.

The property that makes it a measurement rather than a demo: the ref you pick is
resolved to a **commit SHA before the run starts**, and that commit is what the run
records and what its `condition_hash` covers. A run pinned to `main` stays on the
commit it used, so cloning it later reproduces the experiment instead of picking up
whatever was pushed since ([ADR-0029](adr/0029-target-repositories-pinned-per-run.md)).

**Check** resolves the default ref on demand, so an unreachable repository or a
typo'd branch is reported here rather than as a failed run.

A target's **validation commands** replace the case's when a run has one — the repo
knows how it is tested better than a portable case does. Full walkthrough:
[Target repositories](guide-target-repositories.md).

Once two active repositories are registered the page header offers **Test the
harness across these repositories**, the entry to a `cross_repo` experiment. It
lives here rather than on the home page because this is where the thing it needs
is created; with fewer than two, the header says so instead.

### Skills
Two tiers: the **governed catalog** (read-only, shipped with the harness) and
**staged skills** — experimental skills you trial per-run via the ablation panel
and promote when they earn their keep. The page includes a how-to for the A/B loop.

Staged skills are whole trees, not single files. Get one in three ways:

- **Import a skill** — a GitHub URL (repo, subdirectory or `blob` link), a raw
  `SKILL.md` URL, or an archive URL, optionally pinned to a ref;
- **Upload** a `.md`, `.zip` or `.tar.gz`;
- **New staged skill** — write the `SKILL.md` inline.

Import and upload return a **preview** first (file list, detected name and
description, warnings, total size) and write nothing until you confirm; replacing an
existing staged skill is a separate opt-in. **Edit** opens a file browser: pick a
file on the left, edit it on the right, add or delete files there too. Binary assets
are stored and materialized but not editable in the browser.

Everything lives in the database; `data/skills_staging/` is a rebuildable
materialization, so the git working tree stays clean while you experiment
([ADR-0017](adr/0017-multi-file-staged-skills-and-remote-import.md)). Editing any
file — including a helper script — changes the skill's `condition_hash`, so arms
stay distinguishable.

### Improvements
Cross-run worklist for the improvement cycle: after a run's proposals are
synthesized, the **Save** button on the run detail snapshots them here (one
row per proposal, with run date and template version for comparing iterations).

Each row shows the **harness** that produced the run (claude, opencode, …), and
the toolbar can **filter by harness** — useful to work claude-only or
opencode-only batches, since the harness decides which files a fix should touch.
Select any subset — across runs and cases — and **Generate combined issue**
consolidates them, via one LLM pass plus the sanitizer pipeline, into a single
prefilled ml-python-base issue.

The generated issue opens with a deterministic **Target harness surface** block,
computed from the selection's harnesses (never by the LLM):

- all proposals from **one harness** → that harness's adapter (e.g. claude →
  `CLAUDE.md` + `.claude/`; opencode → `AGENTS.md` + `opencode.json`);
- **mixed harnesses** → the shared governed `.github/` layer (synced to every
  adapter);
- if any run used a **self-hosted model** (`gateway/…` executor), a note reminds
  that weaker models benefit from more explicit, front-loaded governance.

The same directive steers the LLM's `affected_artifacts`, so the issue lands in
the surface that actually influenced the runs. Details in
[improvement-cycle.md](improvement-cycle.md).

### Advanced Compare
Side-by-side runs, and an ad-hoc one: **no causal claim is implied unless the
conditions satisfy an experiment design**. The runs lined up here were not
necessarily matched on repository, model or repetition count, so a difference can
come from anything that differed between them. The screen says so at the top. To
make a claim, run an experiment. Below the built-in rows, every **registry score** either run
recorded appears with its provenance, and the winning cell follows each metric's
*declared direction* rather than the sign — a lower cost wins, a lower pass rate does
not.

Line up runs of the same case across models or harness conditions:

- Pick 2–4 runs; the **metrics table** shows case, models, prompt, objective
  result, final/composite score, tokens, cost, time, steps, and interventions —
  with the best cell per row highlighted.
- The aggregated ranking by condition (% resolved, harness version, official
  reference) lives on the **Advanced → Benchmarks** leaderboard.

Typical use: run the same case with a new model and a previous one, then open
Compare to see the objective and cost deltas.

### Health
What the lab can reach, and where its generated state has drifted
([ADR-0018](adr/0018-health-and-drift-panel.md)). One row per check, worst first,
refreshing every 30 seconds:

- **AI gateway / Redis / Celery worker** — is the stack actually wired up. No worker
  reply means runs will sit in `pending`.
- **Docker runtime** — reported *by the worker*, because the API has no docker socket
  by design ([ADR-0002](adr/0002-celery-docker-per-run-execution.md)). Covers the socket
  itself and whether the runner image has been built.
- **Generated skill views** and **governed git tree** — the drift signals. A run that
  died mid-ablation leaves the tracked tree modified; this is where you see it.
- **Staged-skill materialization** and **model routing overlay** — the `data/`
  derivations. They tell you when a model you enabled is not routable yet, or when
  `data/skills_staging/` disagrees with the database.
- **Run queue** — runs active for over six hours, which usually means a worker died
  under them.
- **Langfuse tracing** — whether run traces are reaching Langfuse. Tracing switched
  off reads as `ok`: not using it is a choice, not a fault. It only warns when the
  lab *asked* for tracing and is not getting it — missing keys, or a host that does
  not answer. See [Langfuse observability](guide-langfuse-observability.md).

A failing row carries its own **repair button**. There are exactly five repairs —
re-sync skill views, rebuild the staging tree, refresh the model catalog, reconcile
stuck runs, build the runner image — and nothing else can be triggered from the
browser. Repairs are never automatic: `skills_sync` rewrites tracked files and
reconciling fails live runs, so both wait for you to ask.

**Build runner image** covers the one condition that used to send you to a
terminal on a fresh machine. The build runs on the worker (the only process with
the socket) against its own `/app` mount of the repo, takes a few minutes, and
does not hold the request open — re-check the panel when it finishes. Until the
image exists, creating a run is refused at preflight rather than queued and
killed milliseconds later.

### Settings
The backoffice: everything you would otherwise change with a shell on the server.

- **Gateway connection** — base URL and token, with **Test connection** probing the
  pair without saving it. Saved values take effect on the next request; no restart.
  Secrets are shown masked and are never sent back to the browser in the clear —
  leave a secret field blank to keep the stored value.
- **LM Studio (direct, no gateway)** — what the host at `LMSTUDIO_BASE_URL` is
  serving right now, read from its own API: model id, state, loaded context and
  maximum window. Nothing is adopted or stored; **Refresh from LM Studio** re-reads
  the server, so loading or ejecting a model there shows up here on the next click.
  Set the URL under *Local runtimes*, including the `/v1` segment — a run using one
  of these models is refused up front if the host is unreachable, the name is not
  served, the model is not loaded, or its window is under 32k. That strictness is
  deliberate: unlike the gateway probes there is no ambiguity to protect.
- **Model catalog** — **Refresh from gateway** adopts whatever LiteLLM serves right
  now. Each model can be renamed, enabled/disabled, or made the default. *Enabled*
  models are routed to opencode through a config overlay at run launch, so a model
  you enable here works immediately while `opencode.json` stays untouched
  ([ADR-0016](adr/0016-opencode-config-overlay-for-model-routing.md)). A model the
  gateway stopped serving is marked *not served* rather than deleted, so its label
  survives a provider being down. Ids the gateway routes to the same backend model
  are marked *merged into …* and appear once in the run picker;
  **Check context** measures the window a backend is really serving and flags
  anything under 32k, which run creation also refuses
  ([ADR-0020](adr/0020-context-window-preflight-and-alias-merge.md)).

  The catalog table and both run-form pickers are grouped by serving backend in a
  fixed order — **LM Studio (local) → Cloud → Ollama (rarely used)** — because the
  gateway serves ~40 aliases and a flat alphabetical list interleaved the local
  backend this lab runs on with `ollama-*` routes that are usually down. Within a
  group, models you can launch come before ones nothing can route, and models you
  have named come before ones still carrying their bare gateway id, so the default
  executor sits at the top of the menu rather than buried in it. Backends are
  matched by id prefix; anything unprefixed falls into Cloud, so a new backend
  shows up in the menu instead of disappearing from it.
- **Model defaults / Local runtimes / Harness credentials / Tracing** — the
  allowlisted slice of `.env` ([ADR-0014](adr/0014-web-editable-env-allowlist.md)).
  Every write is atomic and keeps a rolling backup in `data/app/env-backups/`.
  *Tracing* is where Langfuse is switched on: `LANGFUSE_ENABLED` plus both keys,
  and optionally `LANGFUSE_PROJECT_URL` so run pages can deep-link to a session
  ([guide](guide-langfuse-observability.md)).
- **Runtime facts** — `HARNESS_LAB_*` settings, read-only: they are cached at
  process start and changing them needs a restart. This includes
  `max_batch_runs` (default 50), the ceiling the run form reads to size a batch —
  the form reads it rather than mirroring it, so the two cannot disagree.

The password is deliberately **not** on this page: it is set on the server with
`make set-password`, so a stolen session cannot lock you out of your own lab
([ADR-0015](adr/0015-single-user-session-auth.md)). Until you set one, the lab runs
in *open mode* — every screen carries a banner, and anyone who can reach the address
can drive it. Once set, the app opens on a password prompt and the sidebar gains a
**Sign out** button; sessions last 7 days and `make set-password ROTATE=1`
invalidates all of them at once.

## Continuous-improvement cycle

The lab feeds improvements back to your harness repos: after a run's LLM audit,
`make improve RUN=<id>` synthesizes GitHub issue proposals from the "needs
improvement" signals (skills, governance docs, subagents), and `CREATE=1` files
them in `ml-python-base` / `harness-engineering-guide` on your approval. See
[improvement-cycle.md](improvement-cycle.md).

## Testing a new model — worked example

Fastest path, no gateway: load the model in LM Studio with an explicit context
length, press **Refresh from LM Studio** on the Settings page, and pick it from the
**LM Studio (direct, no gateway)** group in the run form. Use the gateway path below
when you want the run's traces and costs in Langfuse.

1. Register the model in your LiteLLM gateway (your ai-gateway).
2. Settings → **Refresh from gateway**. The model is adopted, enabled and routable
   immediately; `opencode.json` is not touched. (Once it earns its keep, run
   `make sync-gateway-models` to curate it into the tracked map for the CLI lane —
   that is a promotion, not a prerequisite.)
3. Advanced → Runs → New run → pick a case (e.g. `pytest-dev__pytest-10051` or
   `cli-textstats-v1`), set **Executor model** to the new model, Create run.
4. Repeat with the previous model (same case + prompt).
5. Compare → select both runs → read the objective/score/cost deltas.
