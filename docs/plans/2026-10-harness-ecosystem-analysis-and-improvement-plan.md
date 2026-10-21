# Harness ecosystem: analysis and improvement plan (2026-10)

> **Status (2026-10-20):** proposal, not started. Read-only analysis of this lab, the
> `ml-python-base` template, `ai-gateway`, `gnomon`, the Harness Engineering Guide,
> `talos`, `xl-skills-collection` and the `em-xl` company brain (AI-pod meetings), plus
> current industry and research practice. Supersedes nothing: it builds on
> `2026-09-harness-measurement-roadmap.md` (phases 0, 1, 2 and 3.1 implemented) and
> keeps its deferred items. Every repository claim below was checked with a command;
> evidence is cited as `path:line`.

## 1. Bottom line

The lab is a careful instrument that has barely been used to measure anything, and the
little it measured is partly wrong.

- It answers *how* to compare harnesses better than most in-house setups: validity
  before verdict (ADR-0046), designs it refuses (ADR-0059), checks that could not run
  are not failures (ADR-0048/0062). That design is worth keeping.
- But: 125 archived runs over 13 active days (2026-08-26 → 2026-09-14), none since;
  73 % of Claude runs on `claude-haiku-4-5`; no run on Opus 5.5, Sonnet 5.5 or
  Fable 5.1; zero subagent calls; template skills invoked about ten times in total.
- The improvement loop (audit → issue → template release → re-measure) has **never
  closed**: `template_ref` is empty on 125/125 runs, `.harness-version.yaml` was never
  written, and the template remote the loop targets does not exist.
- The only effect the data shows is "the harness costs more" — and that reading is
  contaminated by a stop-rule bug that ended bare runs early (§3.1).
- Meanwhile the field moved: research in 2026 finds context files and skills help far
  less than assumed (§4), so the question worth paying for is no longer "does the
  harness help?" but **"which component helps, for which model, at what cost — and
  what can a team now do that it could not before?"**

The plan below fixes the instruments first (two weeks), then moves the lab from
"bespoke runner + toy cases" to "standard runner + task corpus with headroom +
statistics with power", and reframes organisational measurement around team
**quality, speed and capability** — never individual rankings.

## 2. What exists (ecosystem map)

| Repo | Role | State (verified) |
|---|---|---|
| `sdlc-ml-python-harness-lab` (this) | Evaluation plane: runs, audits, experiments | 63 ADRs, ~38.7k LOC src / ~33k LOC tests, ~1,997 tests; last commit 2026-09-23 |
| `ml-python-base` | Governed harness template (skills projection to 5 tools) | v1.0.0 (2026-09-12) at `marcosdh1987/ml-python-base`; org mirror `xmartlabs/sdlc-ml-python-template` numbers the same content v2.0.0 |
| `ai-gateway` | LiteLLM gateway + Engineering Delivery Observatory (EDO) | Active (last commit 2026-10-19); 1,132 unit tests pass |
| `harness-engineering-guide` | Public methodology (Standardize → Measure → Improve) | Last commit 2026-09-13; weekly template sync failing since 2026-09-13 |
| `talos` | Ticket → PR pipeline; "judgement is the model's, certification is code's" | Adversarial benchmark (29 scenarios), flip check |
| `xl-skills-collection` | The org's shared skills repo (AI-pod decision 2026-09-03) | Claude plugin marketplace, 2 kits, no `evals/` |
| `gnomon` | Per-builder transcript profiler (AQ / gstack) | Out of scope for measurement (§6.4) |
| `em-xl` | Company brain (AI-pod notes, decisions, capabilities) | Tracks template v0.7.0 and the old personal lab — stale |

## 3. Findings

### 3.1 The instruments corrupt the data

| # | Defect | Evidence | Effect |
|---|---|---|---|
| I1 | **False `no_progress` stop.** `workspace_fingerprint` runs `git` in a workspace with no `.git`; git resolves the lab repo, where `/workspace/` is ignored, so the fingerprint never changes | `scripts/bench_run.py:959`, `.gitignore:63`; 36 runs stopped `no_progress` at step 2 with no `.git` | Build-from-scratch arms are cut at step 2. The "harness costs 4× more tokens" reading compares arms that stopped for different reasons |
| I2 | **Cost lost on merge.** Empty remote usage overwrites the values parsed from Claude's own events | `scripts/bench_run.py:3207-3219`; 37 Claude runs have null/0 cost while their `result` events report cost > 0 | Every cost and cost-per-success figure is biased |
| I3 | **No intervals reach the UI.** `confidence_interval()` is only called by its test | `src/harness_lab_app/statistics.py:271`; `tests/harness_lab_app/test_statistics.py` | "Validation" (N≥5) is shown without uncertainty; at N=5 a pass rate's 95 % CI is ~±40 pp |
| I4 | **The harness fingerprint misses most of the harness.** It hashes 3 governance docs, `OPENCODE.md`, `LOCAL_AGENT.md`, `.claude/settings.json` and the skills lock — not `CLAUDE.md`, `AGENTS.md`, internal skills, hook scripts, `memory/` or the `Makefile` | `src/harness_lab_app/provenance.py:33-43` | Two different harnesses can share one `condition_hash` |
| I5 | **Unpinned agent CLIs.** Runs used Claude Code 2.1.197; 2.1.285 is installed now. 14 built-in skills (`batch`, `simplify`, `verify`, …) exist in every arm, including "bare" | `system/init` events in `data/runs/*/steps/*.events.jsonl` | Version drift is a hidden factor; "bare" is not bare |
| I6 | **Codex is a black box.** `codex --quiet --full-auto` returns only the final text | `scripts/bench_run.py:1596` | 0 Codex runs; no attribution possible |
| I7 | **A benchmark agent wrote into the lab's own memory** | `memory/context.md:253-254` (committed in PR #22) | Isolation failure: runs can modify the instrument |
| I8 | **Evidence is not durable.** The DB keeps 12 of 125 runs (79 % free pages, no backups); 113 runs have no score anywhere | `data/app/harness_lab.db` | Past experiments cannot be re-analysed |

### 3.2 The loop that should improve the template never closed

- **The lab runs a June harness.** `.github/{standards,architecture,orchestration,sdlc}.md`
  are byte-identical to template v0.2.0/v0.2.1. None of v0.3–v1.0.0 (whose rules came
  from the lab's own issues #33–#47) was adopted, so none was re-measured.
- **The loop's destination does not exist.** The `template` remote and
  `config.py:100` / `scripts/lab_improve.py:52` default to `xmartlabs/sdlc-ml-python-base`
  → "Repository not found".
- **Two version lines for one template.** `marcosdh1987/ml-python-base` v1.0.0 equals
  `xmartlabs/sdlc-ml-python-template` v2.0.0 (341/358 files identical).
- **No provenance in releases.** The v1.0.0 manifest records the annotated tag object
  (`1aa07e98…`) instead of the commit (`a13a7ba…`): `scripts/harness_release.py:936`
  runs `git rev-parse <ref>` without `^{commit}`.
- **0 findings, 0 saved proposals, 0 suite or ladder experiments** in the DB; the
  regression gates (`harness_lifecycle/gates.py:61`) are only called from tests.

### 3.3 The template enforces little and sometimes reports false green

- **`make test` swallows failures.** `pytest … || echo "⚠️  No tests found to run"`
  exits 0 on failing tests and prints "✅ Tests completed!" — after running
  `make format` and `make fix`, which mutate the tree (`ml-python-base/Makefile:148-156`;
  same in this lab's `Makefile:145-153`). Eight template files tell agents to verify
  with `make test` (`CLAUDE.md`, `AGENTS.md`, `OPENCODE.md`, `orchestration.md`,
  `docs/agentic-workflow.md` and three skills).
- **The Stop hook never reaches the model.** `.claude/hooks/stop_nudge.sh` prints to
  stdout and exits 0, so the reminder is shown to the user only. In 203 lab sessions
  only `SessionStart` ever appears as a hook event.
- **Most workflow rules are prose.** Architecture layering, absolute imports, "docs
  with every change" (CI accepts any file under `docs/`), memory/ADR capture — none is
  checked by a hook, linter rule or structural test. Only OpenCode gets a per-edit
  check (`.opencode/plugin/verify-gate.ts`).
- **Always-loaded context keeps growing.** The seven governance docs grew 55 % from
  v0.2.0 to v1.0.0; Claude Code loads ~4.3k tokens up front and the skill catalog
  appears twice (generated block + native listing).
- **Activation is near zero.** Across 103 Claude runs: Bash 3,639 calls, Read 1,515,
  Skill 44 (31 of them a skill under test), Agent/subagent 0.
- **Strengths to keep:** single-source projection to five tools with a drift gate,
  locked dependencies, licence/provenance checks for vendored skills, guarded releases
  with immutable tags, compiled git-action permissions.

### 3.4 The repository audit reads; it does not test

- Two single-sample LLM passes over truncated content
  (`repository_audit_service.py:64-82`). Nothing is executed except the target's own
  suite: hooks are never fired, gates are never tried against a planted violation.
- The talos audit proposed 7 cases, all "Comprobar que…" reading checks; the follow-up
  experiment ran the lab's Tetris/CLI cases on talos at N=1 and came back `no_headroom`.
- No batch/org-wide audit, and no diff of a target against `ml-python-base`.
- **Policy conflict:** all three stored audits sent internal repositories (`talos`,
  `xl-skills-collection`) to `gpt-5.4-mini`. The interim AI policy marks "internal code
  and IP" as BLOCKED for external providers (`em-xl/02-organization/ai-policy.md:43`,
  DEC-002).

### 3.5 Gateway, guide and organisation

- **Gateway** (`ai-gateway/config.yaml`): global exact-match Redis cache (`:585`) can
  replay completions across repetitions on `/chat/completions` traffic; the native
  `langfuse` callback (`:567`) is on without `turn_off_message_logging`, contradicting
  the documented metadata-only promise; `forward_client_headers_to_llm_api: true`
  (`:622`); Opus 5.5 cards have no price pin (`:125-135`). Web-lane runs share one
  long-lived key, and the lab copies the whole host `.env` into every run container
  (`executors.py:286-292`); the runner image has no `USER` (root).
- **Guide:** the flagship Mode A/B/C table is garbled by an automated dash cleanup
  (`docs/en/evaluation/the-three-questions.md:15-18`); 26 links point to the old
  private lab; half of 30 cited URLs fail; sandbox pages describe isolation the lab
  does not have; no page covers deterministic enforcement, entropy management,
  security threat models or human factors.
- **Organisation (em-xl, AI-pod notes):**
  - The lab workshop promised on 09-07, 09-14, 09-21 and 09-28 has no record of
    happening.
  - The Q3 retro (09-23) questioned the pod's purpose and asked for Q4 objectives.
  - The managers' Q4 notes (09-30) list "Medir impacto AI", "Formalizar Talos" and
    "Implementar MCP de seguridad".
  - On 10-14, cybersecurity asked to disable gnomon's rankings.
  - DEC-013 already rejects tier labels because they "make developers compete".
  - The brain still tracks template v0.7.0 and the old personal lab, and its
    `11-ml-governance` evaluation standards are empty placeholders.

## 4. What the field does now (verified sources)

1. **Harness engineering = everything around the model, tuned per model.** OpenAI's
   team shipped a product with "0 lines of manually-written code" by investing in
   environments, short-map AGENTS.md, custom linters whose errors teach the fix, and
   recurring clean-up agents [1]. Böckeler frames the harness as guides (feedforward)
   and sensors (feedback), each computational or inferential [2].
2. **Harness parts encode assumptions that go stale.** "Every component in a harness
   encodes an assumption about what the model can't do on its own" — re-ablate when
   the model changes [3]. Claude Code now ships `/doctor prompt-audit` for exactly this.
3. **Context files and skills help less than believed.** Context files "do not
   generally improve task success rates" and add >20 % cost [4]; 39 of 49 skills give
   zero gain, average +1.2 % [5]. Value concentrates in non-standard, project-specific
   knowledge.
4. **Enforce, don't advise.** Teams move rules from prose into hooks, linters and
   structural tests; CLAUDE.md is "context, not enforced configuration" [2][6].
5. **Verification needs independence.** Agents grade their own work poorly; separate
   evaluators, browser checks and mutation testing are standard [3][7]. About half of
   test-passing SWE-bench Verified PRs would not be merged by maintainers [8].
6. **Evals are statistics.** Pair by task, report pass@k and pass^k, cluster errors by
   task, plan sample size [9][10]. Infrastructure alone moved Terminal-Bench 2.0 by
   6 pp; gaps under 3 pp deserve skepticism [11].
7. **Long sessions break governance.** Compaction raised constraint violations from
   0 % to 30 % [12]; progress files and initializer agents keep long runs restartable [7].
8. **Measure teams, never rank people.** DORA: "company-wide aggregates are
   meaningless, and individual scores are harmful" [13]. Anthropic reports 27 % of
   Claude-assisted work "wouldn't have been done otherwise" (self-reported) [14], while
   an RCT shows AI use impairs learning of a new library unless used for explanation
   [15] — capability gains need learning-preserving practice.
9. **Reuse standard runners.** Harbor (Apache-2.0, v0.24.0) drives Claude Code, Codex,
   OpenCode and others in containers with multi-trial runs and a common trajectory
   format [16]; `claude plugin eval` runs graded cases against a no-plugin baseline,
   3 runs by default, with cost ceilings and CI exit codes [17].

## 5. Improvement plan

Principles carried through every phase: a validity issue is stated before the verdict;
an opinion is never the success metric; nothing is auto-adopted; no per-person scores
anywhere; internal code goes only to approved providers within DEC-002's scope.

### Phase 0 — Make the instruments honest (weeks 1-2)

| Step | Change | Done when |
|---|---|---|
| 0.1 | Fix I1: fingerprint the workspace tree directly (or `git init` it at provision) | A no-`.git` workspace that changes files does not stop `no_progress` (new test) |
| 0.2 | Fix I2: merge usage field by field, never overwrite with empty values; re-derive cost for archived runs from their `result` events | The 37 affected runs regain a cost |
| 0.3 | Fix I3: expose Wilson/bootstrap intervals per arm and a paired, case-clustered bootstrap for the delta in the experiment summary and UI | Every arm shows an interval |
| 0.4 | Fix I4/I5: add `CLAUDE.md`, `AGENTS.md`, internal skills, hook scripts, `memory/`, `Makefile` and the agent CLI version + runner image digest to the condition hash; pin CLI versions in `Dockerfile.runner` | Changing any of them changes `condition_hash` |
| 0.5 | Fix I7/I8: mount the lab read-only in run containers except the run's workspace; schedule `make backup`; export each finished run's scores to `data/runs/<id>/scores.json` | A run cannot write lab files; runs survive a DB reset |
| 0.6 | Point the template remote and `template_repository` at the canonical repo; pick one version line with the org mirror; run `make harness-adopt REF=v1.0.0` | `make harness-status` reports v1.0.0 and runs carry `template_ref` |
| 0.7 | Security baseline for runs: non-root `USER`, a run-scoped env instead of the whole `.env`, short-lived per-run gateway keys with `no-cache` | `SECURITY.md` updated to match |
| 0.8 | Record the I1 contamination in `known-limitations.md`; mark pre-fix cost/effort comparisons as unreliable | The `audit-talos` verdict carries the note |

### Phase 1 — Fix the template's verification, then measure it (weeks 2-4, in `ml-python-base`)

No experiment needed for these fixes:

- `make test` propagates pytest's exit code and stops mutating the tree (keep
  `format`/`fix` separate).
- The Stop hook emits a blocking decision (JSON `decision: block` / exit 2) when
  `src/` or `tests/` changed and no test passed since the last edit.
- Add ruff `TID252` for absolute imports and an import-boundary test for the layering.
- `harness_release.py:936` uses `^{commit}`.
- Fix the stale `src/agent_rag/prompts/` paths.

Then run these single-factor experiments, each with N≥5 per arm on ≥2 model tiers and
a declared non-inferiority margin:

1. **Short map vs long manual:** an adapter of at most 100 lines that indexes the
   governance, compared with today's always-read docs. Primary metric: billed tokens;
   guardrail: task pass rate.
2. **Blocking Stop hook vs nudge.** Primary metric: unverified "done" claims; also
   pass rate.
3. **Deterministic skill routing** (a `UserPromptSubmit` hook that injects the
   router's pick) vs catalog prose. Primary metric: correct-skill rate; also pass rate.
4. **Skill evals with `claude plugin eval`:** package the template's skills (and each
   `xl-skills-collection` kit) as plugins with an `evals/` suite. Keep only the skills
   whose with/without delta is above zero.

### Phase 2 — A corpus with headroom and a standard runner (weeks 3-8)

- **Adopt Harbor as the execution backend** behind the existing Celery task; keep the
  lab's domain model, validity checks, audits and UI. Map each case to a Harbor task
  (instruction, environment, tests, oracle solution). This also brings in Codex
  (`codex exec --json`), Gemini CLI and pinned agent versions.
- **Build an ~80-task corpus with headroom**, pre-gated so that the oracle passes, a
  no-op fails and a cheating agent fails:
  - ~30 Terminal-Bench tasks in the 30–70 % band for the chosen arms;
  - ~20 SWE-bench Pro hard-split or DeepSWE tasks;
  - ~15 feature/program-building tasks;
  - Tetris and the CLI case rewritten with hidden tests;
  - a private split mined from public/approved repos via `case_derivation.py`.
- **Adopt Talos's adversarial categories** for harness cases: *must-ship, must-catch,
  must-not-flatter, must-refuse*. A harness edit that loosens a check then lowers the
  score.
- **Statistics:**
  - pass@k and pass^k;
  - cost per resolved task (billed tokens including cache);
  - a sample-size planner in the experiment wizard;
  - interleaved, randomised launch order;
  - an A/A experiment to set the noise floor;
  - judge N=3 with a calibration set (deferred item 2.4).

### Phase 3 — Experiments on new capabilities (weeks 6-12)

Each is one factor, run on the new-model track (Opus 5.5, Sonnet 5.5, Fable 5.1, Haiku
4.5 as the cheap tier) and repeated on every new model release, funded from the
partnership credits and routed through the gateway so cost is captured:

| Capability | Arms | Primary metric |
|---|---|---|
| Effort level | low / medium / high / max | Resolve rate, cost per resolved task |
| Long sessions + compaction | Off / on / on with pinned constraints | Post-compaction rule violations |
| Subagents / agent teams | Single agent vs team on parallelisable vs sequential tasks | Wall time, success, cost |
| Independent evaluator | No evaluator vs separate-model evaluator with veto | Hidden-test gap, mergeability |
| Browser self-checking | No browser vs Playwright in the runner | UI acceptance tests |
| Memory across sessions | Memory on / off over a sequence of related tasks | Learning-curve slope |
| 24-hour autonomy (ML R&D ask) | Harness variants with long budgets and progress files | Partial credit, stalls, interventions |
| Component re-ablation per model | Each harness component on / off | Δ pass rate; remove components with no effect |

**Observability:**

- Enable Claude Code OpenTelemetry in the runner. It emits `skill_activated`,
  `subagent_completed`, `hook_execution_start/complete` and `compaction` events.
- Parent every trial under the lab's run span.
- Replace log scraping with one normalised event parser.

### Phase 4 — Audits that execute, in policy (weeks 6-10)

- **Executable audit:**
  - fire hooks with synthetic events;
  - try each gate against a planted violation (Talos's flip check);
  - install the harness into a fixture project;
  - turn the "Comprobar que…" checks into deterministic checks.
- **Conformance diff vs `ml-python-base`**: skills lock, adapters, hooks, gates and
  version drift. Add batch audits over an organisation's repositories.
- **Policy routing:**
  - internal repositories are audited with local models through the gateway;
  - external models stay limited to public repositories or approved scope (DEC-002);
  - written authorisation before any client repository.
- **Close the AI-pod commitments:** re-run the talos and xl-skills-collection audits
  within policy, and run the workshop as a guided subject-validation session on a tool
  the team built.

### Phase 5 — Organisational measurement: quality, speed, capability (weeks 8-16)

Team- and system-level only. The current AI policy needs a written no-ranking rule that
extends DEC-007/DEC-013 and covers the lab, the EDO, the gateway and the career
framework.

| Goal | Team/system signal | Where |
|---|---|---|
| Quality | CI first-pass rate, rework/revert rate, escaped defects, review cycles per PR | EDO (`ai-gateway/engineering`) |
| Speed (less wasted time) | Cycle time, time waiting on review/CI, manual interventions per task, quarterly friction survey | EDO + survey |
| Capability | Changes outside a team's main stack, migrations completed, newcomer time to first merged change, "work that would not have happened" | Lab capability cases + EDO + survey |

**Capability-expansion case family in the lab:**

- Tasks in Go, TypeScript and C#, frontend/backend crossover, and large migrations.
- Compared across harness conditions, never across people.
- Includes a learning-preserving mode, where the agent explains rather than delegates.

**Gateway changes as the data plane:**

- Extractor v2, reading the nested LiteLLM metadata.
- Experiment tags (`experiment_id`, `condition_hash`, `arm`).
- Keys per project, not per person.
- A minimum cohort size for AI-cohort views.
- Metadata-only Langfuse export.

### Phase 6 — Keep it current (continuous)

- Re-run the regression suite on every template tag and every new model release;
  enforce `evaluate_hard_gates`; post results back to the originating issue.
- Monthly `/doctor prompt-audit` and `claude plugin validate --strict` in CI.
  Validate the real skill paths: the validator skips symlinked skills.
- **Guide:**
  - Repair the damaged text and the broken sources.
  - Correct the sandbox claims.
  - Add chapters on deterministic enforcement, security and governance, entropy
    management, and human factors.
  - Re-baseline the ecosystem map.
  - Unblock the template sync.
- **Brain:** have the lab emit an ecosystem snapshot the brain ingests (repo, tag, ADR
  head, run counts), and fill `11-ml-governance` from the lab's ADRs.

## 6. Decisions needed

1. **Canonical template repository and version line** (`marcosdh1987/ml-python-base`
   vs `xmartlabs/sdlc-ml-python-template`).
2. **Runner strategy:** adopt Harbor behind the lab, or keep extending `bench_run.py`.
   Recommendation: adopt Harbor and keep the lab's evaluation layer.
3. **Audit routing under DEC-002:** local models for internal code, or a formal
   approval for an external provider.
4. **gnomon:** recommendation is to keep it out of the measurement stack and honour
   the cybersecurity request to disable rankings; at most, reuse its parsers for lab
   transcripts.

## 7. First two weeks

1. 0.1 + 0.2 + 0.8 (stop-rule and cost bugs, contamination note) — `make check` green.
2. 0.6 adopt v1.0.0 under the canonical remote.
3. Template fixes: `make test` exit code, blocking Stop hook, `^{commit}`.
4. 0.3 intervals in the summary/UI.
5. Re-run the native-vs-injected and bare-vs-harness comparisons at N=5 on Sonnet 5.5
   and Haiku 4.5 with the fixed instruments: the first trustworthy numbers.
6. Workshop with the team on a tool they built (subject validation), within policy.

## Sources

All retrieved 2026-10-20.

1. OpenAI — Harness engineering: leveraging Codex in an agent-first world — https://openai.com/index/harness-engineering/
2. B. Böckeler — Harness engineering for coding agent users — https://martinfowler.com/articles/harness-engineering.html
3. Anthropic — Harness design for long-running application development — https://www.anthropic.com/engineering/harness-design-long-running-apps
4. Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents? (arXiv 2602.11988) — https://arxiv.org/abs/2602.11988
5. SWE-Skills-Bench (arXiv 2603.15401) — https://arxiv.org/abs/2603.15401
6. Claude Code docs — Run Claude Code programmatically / hooks — https://code.claude.com/docs/en/headless
7. Anthropic — Effective harnesses for long-running agents — https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
8. METR — Many SWE-bench-passing PRs would not be merged into main — https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/
9. Anthropic — Demystifying evals for AI agents — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
10. Anthropic — A statistical approach to model evaluations — https://www.anthropic.com/research/statistical-approach-to-model-evals
11. Anthropic — Quantifying infrastructure noise in agentic coding evals — https://www.anthropic.com/engineering/infrastructure-noise
12. Governance Decay (arXiv 2606.22528) — https://arxiv.org/abs/2606.22528
13. DORA — Finding balance in the era of tokenmaxxing — https://dora.dev/insights/finding-balance-in-the-era-of-tokenmaxxing/
14. Anthropic — How AI is transforming work at Anthropic — https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic
15. How AI Impacts Skill Formation (arXiv 2601.20245) — https://arxiv.org/abs/2601.20245
16. Harbor framework — https://github.com/harbor-framework/harbor
17. Claude Code docs — Test plugins with evals — https://code.claude.com/docs/en/plugin-evals
