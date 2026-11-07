# ADR-0073: A measurement names its real instrument and its reach

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** Marcos Soto
- **Related:** [ADR-0034](0034-score-registry.md),
  [ADR-0043](0043-one-llm-transport-gateway-first-claude-cli-second.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0054](0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md),
  [ADR-0064](0064-an-experiment-shows-its-uncertainty.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md),
  plan [`2026-11-measurement-honesty-follow-ups.md`](../plans/2026-11-measurement-honesty-follow-ups.md)

## Context

An external review of the lab at commit `31d5f0e` (Federico Trinidad, "Harness Lab bajo
prueba", 2026-11-06) tested `statistics.py` and `factorial.py` by Monte Carlo against
known answers and read the audit and judge code. We reproduced the numerical claims with
the lab's own unmodified functions (150–200 simulations per row) and checked every code
claim at its line. Five findings were cheap to fix and wrong today:

1. **The recorded instrument was the requested one.** `_claude_cli_chat` passes no
   temperature (the CLI has no flag for it) and drops a non-Claude model name, letting
   the CLI choose its default. The lab still recorded the requested model and `0.2`.
   Two "identical" audits could come from different configurations.
2. **No warning for few cases.** With 4 cases × 24 repetitions and an effect that hits
   some cases and not others, the success-delta interval covered the population effect
   ~83% of the time (reproduced: 83%; report: 84%), against the 95% it is read as.
   The bootstrap barely sees case-to-case variation with four clusters, and Newcombe's
   floor treats every run as independent.
3. **Plugin harnesses were invisible.** `governance_paths` named no root under
   `plugins/`, so auditing `xmartlabs/agent-skills` showed 2 skills and 0 agents, and
   five repeated audits never mentioned the 11 under `plugins/`.
4. **Two sentences overclaimed.** `plan_repetitions` said it "errs on the side of more
   runs"; it under-plans (80% promised, 66–72% even effect, 32–66% uneven).
   `factorial` said "more cases narrow it faster than more repetitions" without
   condition; at an even effect 4×12 and 16×3 are equally wide (69 vs 68 points).
5. **Judges pooled across judge models.** Aggregation grouped by `name@version` only,
   although the registry itself says a judge change changes the measure.

## Decision

We will make every measurement name the instrument that actually produced it and the
reach its evidence supports:

1. **`llm_transport.chat_reply` returns a `ChatReply`** with the requested and the
   effective model and temperature. The effective model on the CLI is read from the
   reply's own `modelUsage` (the model with the most output tokens). The effective
   temperature on the CLI is `None` (unknown), never the requested value. `chat()`
   keeps returning `str`. The DeepEval judge records `effective_model`, `temperature`
   and `transport` in each score's provenance. The repository audit files itself under
   the model that answered (`"<served> (requested <asked>)"` when they differ) and keeps
   every pass's configuration in `evidence["llm"]`.
2. **A `few_cases` validity issue** (`degraded`, listed last) fires when a comparison has
   1–4 paired cases (`MIN_PAIRED_CASES = 5`, the threshold `tribunal compare` also
   uses). It bounds the reading ("these cases"), it does not invalidate the comparison.
   Zero paired cases is a different gap and stays quiet.
3. **Plugin components are harness.** `is_skill_path` / `is_agent_path` accept
   `plugins/<p>/skills/<s>/SKILL.md` and `plugins/<p>/agents/**.md`. They are named
   `<plugin>:<name>`, as Claude Code invokes them. The readiness classifier, the audit
   evidence and the governance surface use them, and the runner's governance mask
   hides the lab's own `plugins/` so the surface and the mask still agree.
   `harness_checks`' private copy of the pattern is gone. Plugin skills and agents are
   on-demand catalogue, so `reading_surface` keeps them out of the read-coverage
   denominator, as it does `.claude/skills/`.
4. **The two sentences now say what holds.** The planner is documented as a floor, and
   the factorial reading (and its panel copy, EN/ES) states the condition under which
   cases beat repetitions.
5. **Judgements aggregate per judge model:** the model that answered
   (`effective_model`) when it was recorded, otherwise the configured `model`. Facts
   are never split. Each aggregate carries `judge_model`. The overview headline takes
   the newest version and, within it, the series with the most runs.

## Consequences

- A CLI-only lab now records `temperature: null` for every judged score. That is a
  truthful "unknown", and readers must not treat it as zero.
- Older scores have no `effective_model`. Aggregation groups them by the configured
  `model`, which they do carry. Nothing is backfilled. A CLI-only lab's history
  therefore splits into two series at this change, which is correct: the earlier
  label was a request, not a measurement.
- Small experiments (most of today's, with one or two cases) show a "Read with care"
  `few_cases` box. Headlines are unchanged, because a `degraded` issue does not
  replace them.
- The governance fingerprint of a repository with `plugins/` changes, because the
  surface now includes its plugins. Comparisons across that change are comparisons
  across surfaces.
- `rubric_score` still records its model only inside free-text notes, and the run
  auditor (`bench_audit.py`) still records the requested model. On the gateway the
  request is still taken as the effective configuration, although LiteLLM aliases
  models and can drop parameters. All three are follow-ups.

## Alternatives considered

- **Stop using the CLI for judges.** Rejected: it is the only transport for
  subscription-only labs (ADR-0043). Recording what it did is enough.
- **Return a tuple from `chat()`.** Rejected: eight callers would change for a fact
  only judges and auditors record.
- **Invalidate below five cases.** Rejected: the numbers are true for the cases that
  ran. ADR-0046 reserves `invalid` for comparisons that could not measure what they
  name.
- **Require `.claude-plugin/plugin.json` for plugin agents.** Rejected: path predicates
  see one path at a time. The `plugins/<p>/agents/` slot is specific enough, as
  `SKILL.md` is for a bare `skills/`.
