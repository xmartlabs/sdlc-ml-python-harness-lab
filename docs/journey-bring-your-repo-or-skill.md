# Bring your repo or skill

The lab's own harness is one data point. The interesting question is usually about
**yours**: is your repository's harness any good, and does the skill you wrote make
the agent better?

## Audit a repository's harness

### In thirty seconds, from the shell

```bash
make harness-check REPO=../your-project
```

No stack, no model, no network. It reads the committed tree and runs eight checks
that code can decide — is there a rules file, does a verification target exist and
can it fail, do skills have the frontmatter their tool needs, is the always-loaded
context small enough, are hooks and MCP servers pinned, is the harness version
declared. Each check prints **pass**, **warn** or **fail** with the fix. It exits 1
on a failure, so it can gate a CI job.

### In full, in the web app

**Evaluate → Audit a repository** — four steps.

1. **Point at the repository.** **Audit this folder** registers it, copies it and
   pins the commit in one press. Every later run uses that exact commit, so results
   stay comparable week to week.
2. **Harness audit.** **Generate the audit** runs the same eight checks, then asks a
   model to map the harness — which skills, rules, agents and hooks exist, what each
   claims, and whether the lab could test the claim. The report says *complete*,
   *partial* or *none*, lists the gaps, and proposes cases that would exercise the
   harness. Where the wizard goes next is decided by code, not by the model: a
   repository with no harness, or a runtime the runner cannot execute, is told so.
3. **One case.** Draft a case from a proposal, from the repository's history, by
   hand, or pick an existing one ([create a case](journey-create-a-case.md)).
4. **Run an experiment.** Either *this repository as it is* (a pass rate), or
   *its own harness vs the lab's injected into it* — which says whether the
   template would do better here than what the team wrote.

**Run the gate check** on step 2 is worth the minute: it runs the repository's test
command with no agent at all. A gate that fails on a clean checkout would fail every
run, and you would be measuring the repository, not the agent.

### Coming back

The repository keeps its own page: **Evidence → Repositories → Open**. Its tabs hold
the checkout, the readiness report, its cases, its experiments and the last audit
report. Two presets there launch the common designs in one click:

- **Native vs the lab harness** — the repository's own governance against the lab's
  projected into it.
- **Native vs native + skills** — the repository as it is, against the same with
  skills you staged added (and, if you tick it, the agent told to use them).

## Measure your skill

A skill is a markdown file that tells an agent how to do one kind of thing. Whether
it *helps* is an empirical question with three common answers: it helps, it is
ignored, or it makes things worse by eating context. Measure it.

**1. Get it into the lab.** **Evidence → Skills → Import** takes a GitHub URL, a
`skills.sh` page or an `npx skills add` command; **Upload** takes a folder; **New**
writes one from scratch. It lands as a **staged** skill — visible to experiments,
not yet part of the governance ([skill validation](guide-skill-validation.md)).

**2. Measure it.** Three ways, from least to most effort:

| Question | Where | Design |
|---|---|---|
| Does it help on the lab's cases? | **Evaluate → New evaluation**, factor **Add skills** | harness vs harness + your skill |
| Does it help in my repository? | your repository's page, **Native vs native + skills** | your governance vs your governance + your skill |
| Does my tool work, driven by its skill? | **Evaluate → Validate your tool** | a pass rate per arm, for a tool you built (an agent behind an API, an MCP server) |

Tick **Enforce skill use** in a second experiment: a skill that helps only when the
agent is told to use it has a description problem, not a content problem.

**3. Read it** ([read the results](journey-read-results.md)). If the treatment
never activated, the banner says so — that means the agent never opened the skill.

**4. Keep it or drop it.** **Promote** adds it to the lab's governance; **Archive**
drops it. To carry it into your own project instead, see
[take it home](journey-take-it-home.md).

## Notes

- A run against your repository executes the agent's code with the repository
  mounted. Read [SECURITY.md](../SECURITY.md) before pointing the lab at anything
  sensitive.
- The workbench presets launch with `opencode`. To use `claude` or another model,
  build the same design in **Evaluate → New evaluation** with your repository as
  the venue.
- The full reference for registered repositories, readiness and gates:
  [target repositories](guide-target-repositories.md) and
  [what the lab can run](guide-runner-capabilities.md).

**Next:** [take it home](journey-take-it-home.md).
