# ADR-0081: Reasoning effort is a selectable part of the arm

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Marcos Soto
- **Related:** [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0069](0069-a-model-is-judged-by-the-tier-it-does-reliably.md),
  [ADR-0073](0073-a-measurement-names-its-real-instrument-and-its-reach.md)

## Context

Every claude run went out as `claude -p --model <id>` with no effort flag, so each
model ran at whatever effort the CLI picked by default. That default is not the same
across the catalog. Claude Opus 5.5 and Haiku 5.5 default to `medium`, the rest
default to `high`, and Claude Code itself may pick another level. The lab therefore
could not:

- run a longer, more thorough test (`xhigh` / `max`) on a hard case;
- run a cheap pass (`low`) for a smoke test or a subagent-sized task;
- ask whether a model at lower effort matches a bigger model at higher effort, which
  is the first cost lever before switching models.

Claude Code 2.1.285+ accepts `--effort <low|medium|high|xhigh|max>` (the runner image
pins 2.1.309). Haiku 4.5 has no effort control. Every other model in the native
catalog accepts all five levels.

## Decision

1. **`executor_effort` is a field of the run config.** `RunConfigCreate`, the
   `runconfig` table (an idempotent SQLite compat migration backfills `""`), the
   batch axis `executor_efforts`, and the experiment condition fields all carry it.
   An empty string means "the CLI default". It is the historical arm, not a missing
   value.
2. **The catalog says which model takes which level.** Each row in
   `native_models.NATIVE_MODELS` declares `effort_levels`. `GET /api/models/native`
   serves the rows to the UI. The empty list (Haiku 4.5, codex) means the model has no
   effort control.
3. **The level reaches the CLI only when set.** `BenchRunAdapter` emits
   `--executor-effort`, and `bench_run.build_claude_cmd` turns it into
   `--effort <level>`. With no effort, every command stays byte-identical to the
   command before this change. A CLI resume (`RESUME=1`) restores the level from
   the run's own `metadata.json` when the flag is unset, and refuses a different
   one, because part of the run already ran at the recorded level. A web retry
   already passes the stored level.
4. **Preflight refuses what the run cannot honour, dry runs included.** It refuses
   effort on a non-claude harness, without a native Claude model (gateway-routed,
   empty), and at a level the model does not accept. A model id the catalog does not
   know is not refused, and the CLI decides. `bench_run.py` also exits when it gets
   `--executor-effort` on a non-claude harness. An effort that never reaches the CLI
   would still be recorded and hashed, filing the run under a condition it did not
   run under.
5. **Effort enters `condition_hash` only when non-empty.** This is the same rule as
   the ablation, target and harness-source fields. Every historical arm keeps its
   hash, and the same model at another effort is another arm.
6. **The record says "requested".** `metadata.json` carries `executor_effort`, and
   the condition manifest carries `executor_effort_requested`. Claude Code's
   stream-json `init` event does not report the level it applied (only
   `per_turn_effort_active`), so the lab records what it asked for and names it that
   way (ADR-0073).
7. **Effort follows the model's design rules.** It is a setting of the model, not
   of the harness. Mode `harness_evaluation` refuses conditions that vary
   `executor_effort`, as it refuses a varying `executor_model`, because a
   harness/effort confound cannot be attributed. Holding effort fixed across the arms
   is fine. Compare levels in `subject_validation` (per-cell pass rates, which warns
   when several of harness/model/effort vary) or `discovery`. The relaunch lock
   includes `executor_effort`, so one experiment cannot file two effort levels under
   one name. The relaunch from the locked panel sends the stored level back.
   Experiment launch preflight de-duplicates with the batch's `_preflight_key`,
   which includes effort, so an arm at another level is always checked on its own.
8. **The UI offers it only where it applies.** `EffortPicker` renders only for the
   claude harness with a model that accepts effort. It drops back to the default when
   the model changes to one that does not accept the chosen level. It is offered on
   the single-run form, Experiments and New evaluation, and per arm in Subject
   validation, which is where levels are compared from the UI (the arm label gains
   `@<level>`). New evaluation holds one level across its arms, and does not offer
   it when the design varies the harness across arms. A batch runs at the default, so the
   batch form hides the select; vary effort through an experiment.

## Consequences

- Longer, more thorough tests are one select away. An effort comparison is an
  ordinary `subject_validation` or `discovery` experiment, with `executor_effort` as
  the varying condition field.
- Runs made before this change and new runs at the default share arms. Runs at an
  explicit level never pool with them, even when the explicit level happens to equal
  the model's default. "Default" and "high" are different requests, and the CLI may
  resolve the default differently between versions.
- Adding a model means declaring its `effort_levels`. A test holds every catalog row
  to that.
- The offline fallback in `frontend/src/harnesses.ts` mirrors the levels. It is used
  only when the API is unreachable.
