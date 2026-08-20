# Curated SWE-bench set (runs in the lab today)

This lab imports SWE-bench instances from both **Lite** (300) and **Verified**
(500, human-validated) via the HuggingFace datasets-server — see
`src/harness_lab_app/swebench_client.py`. The runtime is deliberately generic
(clone → `uv pip install -e .` on a pinned Python → run `FAIL_TO_PASS` /
`PASS_TO_PASS` with pytest), so not every instance runs out of the box (ADR-0009).

**Golden rule: prove an instance before trusting it.** `make swebench-selftest
INSTANCE=<id>` applies the reference (golden) patch and checks that it flips the
F2P test and keeps P2P green — **zero model tokens**. Only add an instance to a
benchmark after its self-test passes.

## Validated instances (self-test PASSED)

| Instance | Split | Repo | Version | Notes |
|---|---|---|---|---|
| `pallets__flask-4992` | Lite | pallets/flask | 2.3 | Seeded example (ADR-0009). |
| `pallets__flask-5014` | Verified | pallets/flask | 2.3 | Needs `Werkzeug<3` (in `_ENV_SPECS`). |
| `pytest-dev__pytest-10051` | Verified | pytest-dev/pytest | 7.2 | Modern pytest, clean nodeids. |

Import any of them:

```bash
make swebench-import INSTANCE=pytest-dev__pytest-10051   # or from the Cases page
```

Then create a run against the imported case; SWE-bench runs auto-score
objectively (resolved → 10, else 0) with no manual step (ADR-0009, `tasks.py`).

## What runs, and what doesn't (generic-runtime boundaries)

The self-tests above surfaced three concrete limits. Keep them in mind when
picking new instances:

1. **Python 3.11 compatibility.** The runtime installs on Python 3.11, so
   instances whose `base_commit` predates 3.11 support fail at import time.
   Example: every `psf/requests` instance in Verified is ≤ v2.9 and vendored an
   old `urllib3` that does `from collections import Mapping` (removed in 3.10+),
   so **requests is out**. Prefer instances from **2022+ releases**.
2. **Standard pytest nodeids only.** F2P/P2P ids are passed straight to pytest.
   Instances whose ids carry spaces or exotic parametrization (e.g.
   `test_marker_expr_eval_failure_handling[NOT AND SELF]`) don't match under the
   generic runner. `pytest-10081` / `pytest-10356` fail for this reason while the
   clean-nodeid `pytest-10051` passes. Filter candidates to simple ids.
3. **pytest-collectable tests only.** `sympy/sympy` specifies tests by bare
   function name (`test_homomorphism`) for its own runner, not `path::func`
   nodeids, so sympy is out. `pylint` instances tried so far fail collection
   (`found no collectors`). Both would need a repo-specific test invocation the
   generic runtime does not provide.

Net: the reliable slice today is **modern, pure-Python repos with simple pytest
nodeids** — chiefly `flask` and `pytest` (7.x). Expanding beyond that means
either curating within those repos or teaching the runner per-repo test
invocations (a `_ENV_SPECS`-style extension, future work).

## Adding a new instance (checklist)

1. Pick a candidate from a 2022+ release of a pure-Python repo with simple F2P
   ids. (Browse the dataset at
   <https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified>.)
2. `make swebench-selftest INSTANCE=<id>`.
   - PASS → import it (above) and add a row to the table.
   - `install failed` → if it's a solvable pin, add a `_ENV_SPECS` entry for the
     repo in `swebench_client.py` (`{python, pins}`) and re-run the self-test.
   - `not found` / `no collectors` → nodeid/runner limit (§2, §3); skip it.

## Objective cases without a dataset

SWE-bench gives real-world, objectively-scored tasks but is bounded by the
generic runtime above. For trending agentic tasks with **no external dataset**,
author a `build_from_scratch` case with a precise I/O contract and exit-code
`validation_commands` — see `benchmarks/cli-textstats/` (imported with `make
case-import TASK=benchmarks/cli-textstats/task.yaml`). These run in a fresh
workspace like tetris-v1 and populate the objective validation dimension
automatically, so they compare harnesses/models on a functional floor you fully
control.
