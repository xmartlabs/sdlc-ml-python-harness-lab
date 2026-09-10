# ADR-0058: The repository audit maps harness components to claims and derives cases and conditions from them

- **Status:** Proposed
- **Date:** 2026-09-10
- **Deciders:** Marcos Soto
- **Related:** [ADR-0035](0035-findings-and-manual-maturity-assessment.md),
  [ADR-0043](0043-one-llm-transport-gateway-first-claude-cli-second.md),
  [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md),
  [ADR-0054](0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md),
  [ADR-0056](0056-cases-are-matched-against-runner-capabilities.md),
  [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md)

## Context

The first repository audited end to end with ADR-0054 through ADR-0057 in place was
**talos** (`303ffc6a`), a Shell harness whose stated thesis is that the model may
invoke a deterministic certification layer but never author its outputs. The audit
summarised that thesis correctly, and then proposed five cases none of which measured
it: run the test suite, run the benchmarks, run the model scenarios, check that
`AGENTS.md` exists, check that the README documents the pipeline. The first became
experiment `audit-talos-4`, both arms scored 0, and $4 of agent time produced no
evidence about the harness in either direction.

Reading the run back showed why, and none of it was a bug in the gates:

- **The auditor was asked the wrong question.** `use_cases` asks for *tasks an agent
  could do in this repository*. That question fits a project that carries a harness
  for developing itself; it does not fit a repository that *is* a harness for other
  projects. talos is the second kind, and "run its tests" is a smoke test of talos's
  own development, not a test of its claims.
- **The auditor could not see the mechanisms.** It received 700 characters of each
  `SKILL.md` (the frontmatter), the *paths* of hooks but not their contents, 2 500
  characters of README, and nothing of `scripts/talos-install.sh`,
  `settings.template.json` or `docs/ARCHITECTURE.md`. Its own `gaps` said so: *"la
  evidencia no muestra el contenido completo de los hooks, así que su efectividad no
  puede verificarse aquí"*.
- **Nothing downstream of the model asks whether a case measures anything.**
  `normalize_hint`, `repair_hint_command`, `classify_case` and `runtime_for_command`
  decide whether a hint *looks like* a command and whether the runner has *bash*.
  The gate check dry-runs the command and passed a suite that was already red at the
  pin — 39 failures in `lib/evidence.sh` and `test_seal.sh` before any agent touched
  it — because "fails on clean" was read as "there is work to do".
- **The harness's shape was invisible.** talos installs itself into a target project
  (`talos-install.sh` copies `settings.json`, hooks and permissions in). A raw checkout
  has its sixteen skills under `skills/`, not `.claude/skills/`, and no settings file:
  the "native" arm was a bare Claude Code run inside talos's source tree, and the
  "injected" arm overwrote talos's `CLAUDE.md` with ml-python-base's while leaving its
  `AGENTS.md` in place. Neither arm was the configuration the experiment named.

Meanwhile the lab already knows how to do the thing the audit failed to set up. An
experiment condition can disable a target's skills (`skills_disabled`), stage extra
ones (`skills_extra`) and ask for one to be invoked (`skills_invoke`); the experiment
audit already reports which skills were invoked and which governance documents were
read. What is missing is the step that says *which* skill to test, *against* which
counterpart, with *what* task and observable. That step belongs in the repository
audit, and it fits inside the transport the lab has (ADR-0043): no tools, no agent,
no new service.

## Decision

**The audit's unit of analysis is a harness component, and its output is a map from
components to claims.** `use_cases` is replaced by `component_map`: one row per
`skill | hook | rule | agent | command` the evidence shows, with

- `claim` — what the component says it does, in one sentence, quoting its own text;
- `trigger` — when it is meant to fire;
- `observable` — the artifact or behaviour that would show it fired and helped;
- `lab_counterpart` — the name of the lab-harness component that does the same job,
  or empty; the model chooses from a catalogue the evidence carries, and the lab
  rejects a name that is not in it;
- `needs` — external requirements the claim depends on, from a closed vocabulary
  (`jira`, `github`, `docker`, `network`, `target_repo`, `browser`, `none`).

**Evaluability is computed, not declared.** The model never writes `evaluable_here`.
The lab derives it from `needs` against a capability table that extends
`runner_capabilities` (ADR-0056) with services, in the same spirit: a fact the lab
can check closes the door; the model's prose does not reopen it.

**The harness has a shape, and the audit says which.** `shape` is `in_place`,
`installable` or `both`, with `install_entry` (a path the lab verifies exists) and
`unit_of_work` (one phrase: *a Jira ticket → a reviewable PR*). An installable harness
whose installer the lab cannot run against a target project is reported as such —
its claims are marked not measurable here — instead of being run bare and called
"native".

**The repository's own health at the pin is evidence.** Before the model is asked
anything, the target's `validation_commands` run through the hardened validation lane
(ADR-0056) and the result — pass/fail, counts where the output offers them — enters
the evidence as `health_at_pin`. A red suite is a fact the auditor must work with: a
case whose validation is that suite cannot be proposed as "make it green" unless the
task says so.

**Cases are theorised from the map, in a second pass.** The model receives the
annotated map and proposes cases, each declaring `exercises` (component names from
the map), `measures` (`harness_claim` when the case tests what the harness says it
does, `harness_development` when it tests working *on* the harness — both allowed,
never confused), `task`, `observable` and `validation_hint`. A case that names no
component, or a component the map marks not evaluable, is dropped with its reason.

**Experiment conditions are derived, never authored by the model.** From `exercises`
and `lab_counterpart` the lab builds the three standard arms in the shape
`experimentcondition.overrides` already takes: `native-on` (nothing changed),
`native-off` (`skills_disabled` = the exercised skills), `lab-counterpart`
(`skills_extra` = their counterparts). Hooks, rules and agents cannot be toggled
today; a case that exercises only those yields the existing `native | injected` pair
and says so.

**Evidence is chosen by relevance, not by the first N characters.** Full `SKILL.md`
bodies under a per-skill cap large enough for the body, hook script contents,
installer scripts and settings templates, and the architecture document the README
links first. Caps still exist; what they cut is the tail of a long file, not the part
that says what the thing does.

## Consequences

- talos, at the same pin, is expected to report: installable harness, unit of work
  *ticket → PR*, sixteen skills of which five (`spec`, `plan`, `implement`, `verify`,
  `qa`) are evaluable here with counterparts in ml-python-base, three (`ticket`, `pr`,
  `ci-watch`) need Jira or GitHub, suite red at the pin. Proposed cases exercise a
  claim — a ticket in prose → `SPEC.md` + `plan.json`; a planted bug → red→green
  evidence — with `talos-plan` vs `writing-plans` as derived arms. That is the report
  a person can act on: good, bad or improvable, and why.
- Two model calls per audit instead of one, both on the planner tier. Cost per audit
  rises; cost per *useless experiment* — the thing this replaces — was $4 and an
  afternoon.
- One validation-lane container run per audit, for `health_at_pin`. Bounded by the
  lane's existing timeout and output cap.
- `classify_case` keeps its job (can the command run) and gains a sibling that answers
  a different question (does the case measure a claim). Neither replaces the other.
- ADR-0054's `use_cases` shape and ADR-0056's `plan_cases` over it are superseded in
  part; audits stored before this change render as they did.
- The experiment audit closes the loop: `skills_invoked` can be read against the
  case's `exercises`, so "the skill this case wanted to measure never fired" becomes
  a reportable finding rather than a guess.
- Accepted risks: the model can still write a `claim` its source does not support —
  mitigated by feeding the full body and rendering the source path beside every row;
  `lab_counterpart` is judgement, validated only for existence, and its quality is
  observed at the experiment audit, not before.

## Alternatives considered

- **Patch `use_cases` in place** — raise the evidence caps, add `exercises` and
  `health_at_pin`, keep the shape. Rejected: it keeps the wrong question. A case
  born from "what could an agent do here" does not become a measurement of a claim
  by having a skill name attached afterwards.
- **An auditor agent with tools**, as a LangGraph service the lab calls over
  `/invoke`. Deferred, not rejected: the rubric maps naturally onto a graph and a
  standalone auditor has uses outside the lab. But it adds a service to a lab that
  should first prove the *question* is right with the transport it has. If a single-
  pass model over full evidence produces good maps, the agent can wait; if it does
  not, the failure is in evidence or rubric, not runtime, and an agent would not fix
  it.
- **Letting the model write experiment conditions directly.** Rejected: the override
  vocabulary is small and mechanical, and every field the model writes is one it can
  get wrong. Deriving conditions from `exercises` keeps the model on *what to test*
  and the lab on *how to run it*.
- **Stopping the wizard on an installable harness the lab cannot install.** Rejected:
  working on the harness is still a legitimate thing to measure. The distinction is
  carried by `measures`, and the report states plainly which kind each case is.
- **Only skills in the map.** Rejected: talos certifies with hooks and `lib/*.sh`,
  not with skills; a map that cannot name `classify.sh` cannot state its thesis.
  Non-skill components enter the map and the report; only skills yield toggleable
  arms, and the case says so.
