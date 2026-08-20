# ADR-0021: Guarantee that a run's artifacts survive and its verdict is visible

- **Status:** Accepted
- **Date:** 2026-08-12
- **Deciders:** Marcos Soto
- **Related:** [ADR-0009](0009-repo-task-provisioning.md),
  [ADR-0020](0020-context-window-preflight-and-alias-merge.md),
  [guide-web-ui.md](../guide-web-ui.md),
  [local-model-runtime-config.md](../local-model-runtime-config.md)

## Context

An audit of runs `tetris-v1-K-035` … `K-040` found that none of them measured what
the lab exists to measure. The failures were not model quality; they were four
defects in the lab itself, each of which silently corrupted the result.

**The agent's work was being thrown away.** Only `<repo>/workspace/` is bind-mounted
into the runner (`.:/app`, `WORKDIR /app`), yet the case prompt asked for files in the
*relative* `workspace/{run_id}/` — and shipped that `{run_id}` as a literal, because
the prompt body was concatenated without substitution. Handed a path that does not
exist, agents resolved it to `/workspace/<run>/` at the container root, outside the
mount. Measured: K-039 wrote there 116 times and archived **zero files**; K-037 wrote
there 895 times and archived a snapshot frozen 85 minutes before the run ended. K-035,
whose prompt variant happened to carry no placeholders, wrote 176 times inside the
mount and lost nothing. The correlation is exact.

**The container was not honestly headless.** `SDL_VIDEODRIVER=dummy` existed only as
prose inside the prompt, to be retyped by the agent on every invocation. Compliance
was 72% (K-036) and 40% (K-038). Each miss left a pygame loop running forever, holding
opencode's single persistent shell, until the whole run wedged — K-036 finally hung on
a `sed`, and in K-038 even the `killall python` cleanup timed out.

**`diff.patch` was always empty.** `git diff --no-index /dev/null <dir>` pairs the
basename, fails with `Could not access '<dir>/null'`, and writes zero bytes while the
error goes to an unread stderr. `bench_audit` omits its "código producido" section when
that file is empty, so the audit LLM judged every build-from-scratch run without ever
seeing the code — and the improvement proposals derive from those audits.

**The verdict existed but was invisible.** The smoke check had already run and failed
all three "successful" runs (exit 124, 1, 2 — hangs, `SyntaxError`, no file at all),
yet each sat in `awaiting_manual_score` with an empty score, indistinguishable from a
run genuinely waiting for review.

## Decision

We will make artifact survival and verdict visibility properties of the lab, not
things a model has to get right.

1. **Prompts name one absolute path.** `agent_workspace_path()` resolves the run's
   workspace and `render_prompt_body()` substitutes `{workspace}` and `{run_id}` into
   the case body. The location constraint states the absolute path and names
   `/workspace/<run>/` explicitly as discarded. Substitution uses `str.replace`, not
   `str.format`, because prompt bodies contain braces of their own.
2. **An empty archive is reported as a failure to produce anything**, not as an
   ordinary completion.
3. **The runner is headless by construction** — `SDL_VIDEODRIVER`/`SDL_AUDIODRIVER` are
   set in `Dockerfile.runner`, in `build_run_environment()`, and via `setdefault` in
   `bench_run.main()` so the host lane is covered too.
4. **`diff.patch` is generated against an empty temporary directory**, which git does
   compare correctly against a directory.
5. **`GET /api/runs` carries `objective_passed`**, and the run list renders a
   *checks failed* / *checks passed* badge beside the state.
6. **Failures are classified by who caused them**
   (`src/harness_lab_app/run_failures.py`): `lab` (never started, or killed by a
   worker restart), `stalled`, `timeout`, `agent`. The run list shows the label
   with the full reason on hover, and the leaderboard **excludes `lab` failures
   from its attempt counts** — a run that asked the model nothing is not an
   attempt. One module defines this so the list, the API and the leaderboard
   cannot disagree.

## Consequences

Runs become comparable: a result now reflects the agent's work rather than whether it
guessed the right directory or remembered an environment variable. The audit and
improvement pipeline sees the produced code, so its proposals are grounded in what was
actually built.

Historical runs are not retrofitted. Anything before this ADR that shows an empty
workspace or an empty `diff.patch` should be treated as void rather than as a low
score — in particular, improvement proposals synthesized from K-036 through K-039 were
generated blind and should be re-derived before acting on them.

Absolute paths make the prompt host-specific, so a prompt copied between machines no
longer transfers verbatim. That is the intended trade: reproducibility of the artifact
beats portability of the prompt text.

Setting `SDL_VIDEODRIVER=dummy` globally means a case can no longer open a real window
inside the runner. The in-platform desktop sandbox (`Dockerfile.sandbox`, `DISPLAY=:0`)
is a separate image and is unaffected; an operator who needs a real driver can still
export one, since both mechanisms defer to an explicit value.

## Alternatives considered

- **Teach the agent harder** (stronger prompt wording about the path and the SDL
  prefix) — rejected: it is the approach that failed. Compliance was already
  model-dependent, which biases exactly the comparison the lab exists to make.
- **Deny writes outside the mount via opencode permissions** — attractive, but runs use
  `--dangerously-skip-permissions` by design and a hard denial mid-run produces a
  confusing failure. Naming the right path costs nothing and fixes the cause; this
  remains available as defence in depth.
- **Reconstruct the code from the transcript when the archive is empty** — rejected as
  papering over the bug: the transcript is not a build, and an empty archive is real
  information about the run.
- **Block the run when the smoke check fails**, rather than badging it — rejected:
  manual scoring exists precisely to judge partial work, and the operator should decide
  what a broken build is worth.
