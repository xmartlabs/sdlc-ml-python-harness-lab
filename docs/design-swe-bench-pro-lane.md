# Design: SWE-bench Pro lane (image-based hard cases)

- **Status:** In progress (feature/swe-bench-pro-lane)
- **Date:** 2026-07-21
- **Related:** ADR-0009 (SWE-bench case type — declared official images "future
  work"; this is that future), ADR-0002 (worker owns docker), ADR-0004
  (objective validation), `docs/design-swe-bench-cases.md`

## Why

The lab's SWE-bench lane (ADR-0009) covers Lite/Verified instances that fit the
generic pip+pytest runtime — deliberately the *easy* end of the spectrum (Lite
was filtered to single-file patches; our first runs, e.g. `pallets__flask-4992`,
are saturated by frontier models). To test **high-capability agentic coding**,
we adopt [SWE-bench Pro](https://github.com/scaleapi/SWE-bench_Pro-os)
(ScaleAI): long-horizon, multi-file, contamination-resistant tasks where
SWE-agent + frontier models resolve only ~23%.

Few but challenging: the goal is a curated set of 5–10 Python instances, not
the full 731.

## Dataset facts (verified 2026-07-21)

- HF `ScaleAI/SWE-bench_Pro`, split `test`, 731 rows: go 280, python 266,
  js 165, ts 20.
- Python subset = 3 repos: `ansible/ansible` (96), `internetarchive/openlibrary`
  (91), `qutebrowser/qutebrowser` (79).
- Per-instance columns beyond the classic schema: `requirements`, `interface`
  (both belong in the agent prompt per the official scaffold), `repo_language`,
  `before_repo_set_cmd`, `selected_test_files_to_run`, `dockerhub_tag`.
- `fail_to_pass` / `pass_to_pass` are **Python-repr strings** (not JSON —
  parse with `ast.literal_eval`). For Python repos they are pytest node ids.
- Prebuilt per-instance images: `jefzda/sweap-images:<tag>` — **amd64-only**,
  ~550–700 MB. Tag = `helper_code/image_uri.py` logic (128-char truncation +
  element-web special cases); the raw `dockerhub_tag` column is *not* always
  the final tag.
- Inside every image the subject repo is at **`/app`** with env baked in
  (e.g. `PYTEST_ADDOPTS="--tb=short -v --continue-on-collection-errors
  --reruns=3"`, Qt offscreen vars for qutebrowser).
- Per-instance `run_script.sh` (env + pytest invocation) and `parser.py`
  (stdout/stderr → `{"tests": [{name, status}]}`) live in the OSS repo under
  `run_scripts/<instance_id>/` — fetched at import time and stored on the Case.

## Decision

**A new case type `swe_pro_instance`, same run → validate → audit cycle.**
Like ADR-0009: provenance, attribution, audit, and the `swe_default` /
`swe_harness` prompt A/B are reused unchanged. What differs is *provisioning*
(docker image, not clone+pip) and *objective validation* (containerized
run_script + parser, not host pytest).

### Import → Case

`swebench_pro_client.py` fetches the instance from the HF datasets-server
(stdlib urllib, no `datasets` dep, mirroring `swebench_client.py`), fetches
`run_script.sh` + `parser.py` from the OSS repo raw URLs, and normalizes.
`CaseService.import_swebench_pro_instance` stores the payload JSON-encoded in
`metadata_json` (no schema migration), and builds both prompt variants; the
Pro prompt adds `## Requirements` and `## Interface` sections from the dataset.

### Provisioning (worker/host side — docker required)

Per ADR-0002 the runner stays DB-blind and socket-less, so Pro provisioning
happens **before** the runner launches, wherever docker lives (worker container
in the web lane, host in the CLI lane):

1. `docker pull jefzda/sweap-images:<tag>` (amd64; QEMU emulation on Apple
   Silicon dev machines — fine for smokes, the real bench runs on the amd64
   Ubuntu server).
2. `docker create` a temp container → `docker cp <ctr>:/app workspace/<run_id>/`
   (includes `.git`, full history).
3. Apply `test_patch` to the host copy and commit the provisioned state, so
   `finalize_workspace`'s `git diff HEAD` isolates the agent's edits (same
   trick as ADR-0009).
4. Remove the temp container.

The agent then edits `workspace/<run_id>/` on the host exactly like the
existing lane.

### Test execution during the agent loop

The Pro test environment only exists inside the instance image. Consequences:

- **Host backends (claude/codex CLI, and the Ubuntu server config):** the
  prompt documents a sidecar command the agent can run:
  `docker run --rm -v <abs workspace>:/app <image> bash -lc '<run_script> ...'`.
- **Containerized opencode backend:** no docker in the runner → the in-loop
  test feedback is unavailable; the agent works from code reading + the
  failing-test source (oracle mode). Per-step objective evaluation
  (`swe_step_evaluation`) is skipped for Pro when docker is unreachable and
  runs worker-side at validation instead. Documented limitation, revisit if
  it dominates results.

### Objective validation = containerized run_script

`swebench_pro_validate.py` (same CLI contract as `swebench_validate.py` —
prints `SWEBENCH: resolved=… F2P=x/n P2P=y/m`, exits 0/1, so the validation
panel and auto-score reuse `parse_objective` unchanged):

1. `docker run --rm -v <archived workspace>:/app -w /app <image> bash
   /run/run_script.sh <selected_test_files_to_run>` (script + parser staged
   into the mount; image env, including reruns/Qt vars, comes baked in).
2. `parser.py` parses stdout/stderr → per-test statuses.
3. resolved ⇔ every F2P test PASSED and no P2P test regressed.

### DB → runner channel

`tasks._write_case_spec` gains the Pro fields (`case_type: "swe_pro"`,
`dockerhub_image`, `run_script`, `parser`,
`selected_test_files_to_run`, `before_repo_set_cmd`). `bench_run.py` branches
on `case_type` in the existing case_spec lane. The **gold patch never enters
case_spec.json or `metadata_json`** — the bind mount makes that file readable
by the agent; it exists only transiently in the normalized spec for
golden-patch self-tests.

### Implementation notes (as wired)

- **Worker pre-provisioning:** `tasks._provision_pro_workspace` shells out to
  `scripts/swebench_pro_common.py --provision-run <id>` right after staging
  case_spec.json, before the runner container starts. Idempotent; failures
  mark the run failed with the captured output as RunEvents.
- **Validation routing:** `build_validation_spec` flags Pro specs `local:
  True`; `ValidationService._run_objective` then uses the subprocess executor
  (worker process, socket present) instead of launching a docker-less
  validation container.
- **Host-path translation:** docker `-v` sources from inside the worker must
  be host paths (docker-out-of-docker). `swebench_pro_common._host_path`
  rewrites `/app/...` via `HARNESS_LAB_HOST_REPO_ROOT` (same trick as the
  sandbox launcher), and eval staging lives under `data/tmp/` (gitignored) so
  it exists on both sides of the bind mount.
- **Worker image:** `Dockerfile.api` now ships git + the static docker CLI
  (client only; the socket is still mounted on the worker service alone).
- **Make targets:** `make swebench-pro-import INSTANCE=…` and
  `make swebench-pro-selftest INSTANCE=…`.

## Execution topology

- **Mac (dev):** import + unit tests always; provisioning/validation smokes
  under emulation (slow but functional; prefer ansible/openlibrary — no Qt).
- **Ubuntu server (AMD Ryzen, 40 GB, amd64):** the real bench runs — native
  image execution via the existing worker docker-out-of-docker setup.

## Phases

1. **Spike (this branch):** client + import + provision + validate, proven
   end-to-end on ONE `ansible/ansible` instance with the golden patch
   (`resolved=True` with gold, `resolved=False` without — zero model tokens,
   same proof pattern as ADR-0009).
2. **Lane completion:** bench_run/tasks wiring, web UI import, curated set doc
   (`docs/swebench-pro-curated-set.md`, 5–10 Python instances across the 3
   repos), ADR.
3. **Later / optional:** Go & JS/TS subsets (need language toolchains in the
   sidecar flow only — the runner never builds the project), difficulty
   curation from `issue_categories`, blind mode toggle.

## Rejected alternatives

- **Extending the image-free runtime to Pro repos:** ansible/openlibrary/
  qutebrowser environments are exactly the "heavy instances" ADR-0009 scoped
  out; reproducing them from pins would be brittle and unfaithful to the
  official eval.
- **Agent runs inside the instance image:** would require installing the
  opencode harness into 731 third-party images and mounting the docker socket
  or nesting; breaks the lab's clean runner/wor ker separation for little gain.
- **SWE-bench Full as the hard set:** no difficulty labels, unvalidated tests
  (false negatives), same contamination window as Lite/Verified.

## Fallback

SWE-bench Verified ships a human `difficulty` annotation: 45 instances at
"1–4 hours"/">4 hours", of which 16 are pytest-compatible with the existing
image-free lane (sphinx ×5, astropy ×3, pytest ×3, pylint ×2, xarray ×2,
scikit-learn ×1 — needs only new `_ENV_SPECS` entries). If Pro friction ever
blocks the experiment, this is the cheap hard-case source.
