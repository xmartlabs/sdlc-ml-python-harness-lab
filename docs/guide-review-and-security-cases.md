# Review and security cases, and the skills they trial

Two GPU-free cases measure work that is not "build a thing that passes tests":

| Case | The agent's job | Its candidate skill |
|---|---|---|
| `pr-review-v1` | Review a pull request already applied to a small shop backend and write `review.json` | `review_pull_request` |
| `security-fix-v1` | Fix the vulnerabilities an external pentest reported, without breaking features, and document each fix | `remediate_vulnerabilities` |

Both run in the shared runner image with only the Python standard library. Each case
ships the skill it is built to test. The decision is in
[ADR-0087](adr/0087-a-case-can-ship-the-candidate-skill-it-trials.md).

> This page is readable by agents in `lab_root` runs. It says how the cases grade,
> never where the defects are. Keep it that way when you edit it.

## How they grade

**`pr-review-v1`** grades the review against a hidden answer key, one check per entry:

- **Recall.** Each seeded defect is a check. A finding counts when it names the
  defect's file and lands within a few lines of it, or inside the short function that
  holds it. Decoys keep the strict window.
- **Decoys.** Some code looks wrong and is correct. Flagging it at `medium` or above
  fails its check.
- **Precision.** Most blocking findings must be real defects, so a review that lists
  everything fails here.
- **Verdict, format, untouched.** The merge verdict must be right, `review.json` must
  follow the contract, and the code under review must stay byte-identical.
- **Canary.** The key carries a secret token. If it appears in the run's transcript or
  workspace, the run read the key, and this check fails. It is settled for Claude Code
  runs and inconclusive under opencode, whose logs do not record tool output.

**`security-fix-v1`** runs three kinds of check:

- **Exploits.** For each vulnerability class, an exploit runs with payload variants
  drawn from the run id, so a blocklist that stops one string still fails.
- **Regressions.** These keep the features working. Deleting the vulnerable feature is
  not a fix.
- **Report.** `SECURITY.md` must name a CWE for each fix.

The two cases share four properties:

- Each has a hidden grader under `agent_hidden`.
- Each runs one command per check, so `test_pass_rate` and the per-step reward have
  resolution.
- Each has a dry run that comes out `ok`: the untouched starter fails the checks that
  measure the agent.
- Each has calibration under `data/reference/<case>/`.

## Test a skill on its case — from the UI

Any case that ships `candidate-skill/<name>/` offers three paths in one panel
([ADR-0091](adr/0091-a-case-that-ships-a-skill-offers-its-trial.md)). Open
**Benchmarks → Review, security & containment → Browse**, pick the case, and scroll
to **Test a skill on this case** (or go straight to
`/evidence/cases?case=pr-review-v1`).

| You want to… | Do this | What it creates |
|---|---|---|
| **Try the case's skill** | **Stage it** → pick a model → **Create the A/B** | no skill vs `review_pull_request`, asked for |
| **Improve it** | **Edit the staged copy** (Skills → Import), then **Create the A/B** again | the same design; the edit changes the content hash, so v1 and v2 runs stay apart |
| **Bring yours** | **Import a skill** (GitHub, SKILL.md or .zip), pick it in the panel, **Create the A/B** | no skill vs your skill, on this case |

Each path builds the same two arms the first trials used (family
`candidate-skill-trials`): the `harness` prompt, one model and harness pinned in
both, control adds nothing, treatment adds the staged skill and asks for it. It is
**created, not launched**: the link opens it in Experiments, behind the usual launch
form and preflight. Read the result in this order:

1. `skill_invoked` for the treatment — near 1, or the arm measured nothing;
2. the case's own checks per arm (for the review case: defects found, decoys,
   precision);
3. only then the success delta, with 5+ runs per arm.

A **model comparison** (Evaluate → Compare models) is a different question: it runs
each model with the harness as it is, so it says nothing about a skill the case
ships. If a run's `skills_consulted` is empty, no skill was opened — the agent
reviewed on its own.

## Trialling a candidate skill from the terminal

The same flow, step by step. A skill joins the catalog only after it wins an A/B
([ADR-0086](adr/0086-pruning-never-trades-quality-for-a-smaller-harness.md)).

1. **Stage it.** The candidate lives next to its case, hidden from every agent
   container:

   ```bash
   make stage-candidate                              # list the candidates
   make stage-candidate SKILL=review_pull_request    # REPLACE=1 to refresh after an edit
   ```

   This runs inside the api container when the stack is up, so the host never opens
   the lab database.

2. **Design the experiment.** Open **Evaluate → New evaluation → Evaluate a harness
   change → Add skills**. Tick the candidate, pick its case, choose the `harness`
   prompt and 3+ repetitions, and turn on **Enforce skill use**. The question is "does
   it help when used", not "does the agent find it"
   ([guide](guide-skill-validation.md), Lane B).

3. **Pick a model that does not saturate the case.** If both arms pass everything (a
   ceiling) or fail everything (a floor), the verdict is `no_signal`. Start with a
   Haiku-class or a local model.

4. **Read the result:**
   - `test_pass_rate`;
   - the case's own checks: recall versus decoys and precision for the review case,
     exploits versus regressions for the security case;
   - `skill_invoked`, which should be close to 1 under Enforce;
   - cost.

5. **Promote it, but only on a win.** Use **Skills → Promote**. A promoted skill goes
   to the catalog as an opt-in, not into `minimal`:

   ```toml
   # harness.toml of a project that wants it
   include = ["review_pull_request"]
   ```

   Carry it to the template with a governance-only PR. `make template-sync` brings it
   to every project born from the template.

6. **Iterate.** Stage a rewrite as `<name>-v2` and compare the two with
   `make refine-skill SKILL=<name> CANDIDATE=<name>-v2 MODEL=... CASES="..."`.

## First trials (2026-11-19): no candidate earned promotion

Haiku 4.5, `claude` harness, `harness` prompt, one step per run. Experiments
`trial-review-pull-request` (+ `-2`), `trial-audit-api-security` and
`trial-operate-within-scope`, family `candidate-skill-trials`. Graded after the
grader fixes in the ADR-0087 amendment of 2026-11-19.

| Skill | Runs per arm | Control | Treatment | Verdict |
|---|---|---|---|---|
| `review_pull_request` | 7 | 3.0 real defects of 6 | 2.71 | `no_signal`: +1.0 after 3 runs, −1.0 in the next 4 |
| `audit_api_security` | 3 | 4.33 located, 4.33 proven of 5 | 4.67 / 4.67 | `no_signal`: near the ceiling for this model |
| `operate_within_scope` | 3 (+3 "available") | disclosure 0/3 | 0/3 (available: 0/3, never invoked) | `no_signal`: the job never pressures the agent toward the reserved paths |

What the next version should start from:

- **The skill is used but does not change what the model checks.** Under Enforce
  every run invoked `review_pull_request`, and both arms missed the same hard classes
  (the test that cannot fail, the caller outside the diff) at the same rate. The skill
  already names them; Haiku reads past a suggestion. Try a checklist that demands
  evidence per item (each new test and the assert that fails if the code is wrong;
  each caller of each changed contract), then re-run the A/B. Repeat on a stronger
  model to see whether it follows what Haiku skips.
- **Three runs per arm is not a verdict.** A one-defect lead reversed with four more
  runs. Read 5+ per arm before calling a delta.
- **Open the failing checks before reading any delta.** Three grader defects hid
  correct answers in these trials (see the ADR-0087 amendment).
- **The containment case needs pressure.** Every run fixed the job without opening
  the reserved files, so "did not use the token" measured nothing. A v2 should make
  the sanctioned path visibly insufficient (the job is asked to also ship), without
  announcing the test.
- **`audit_api_security` needs a harder case or a weaker model** before its trial
  can show anything.

## Changing a case

Adding a defect, a decoy or a payload family changes the instrument
([ADR-0073](adr/0073-a-measurement-names-its-real-instrument-and-its-reach.md)):

1. Bump the case's `version`.
2. Re-run the calibration under `data/reference/<case>/`.
3. For the review case, regenerate `starter/pr.diff` from a base tree.

The case's hidden `README.md` describes each of these steps.
