#!/usr/bin/env python3
"""Shared SWE-bench provisioning + test helpers.

Reused by the runner (provision a repo into the workspace before the agent runs)
and by validation (install + run the FAIL_TO_PASS / PASS_TO_PASS subset). The
runtime is generic — clone the repo, `uv pip install -e .`, run pytest — rather
than the official per-instance Docker images, so it stays light and self-hostable
(fine for pure-Python repos like flask/requests/pylint; heavy or non-3.11
instances are out of scope for now).

Run the golden-patch self-test (no model tokens spent) with:

  uv run python scripts/swebench_common.py --selftest pallets__flask-4992
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# The dataset client + env-spec map live in the app package (single source of
# truth, also used by the API import endpoint); re-exported here so runner-side
# callers keep importing them from swebench_common.
from harness_lab_app.swebench_client import (
    env_spec,
    fetch_instance,
    repo_url_from,
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True
    )


def clone_checkout(dest: Path, repo_url: str, base_commit: str) -> None:
    """Clone repo_url into dest and check out base_commit."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone = _run(["git", "clone", "--quiet", repo_url, str(dest)])
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr.strip()[-500:]}")
    checkout = _run(["git", "checkout", "--quiet", base_commit], cwd=dest)
    if checkout.returncode != 0:
        raise RuntimeError(
            f"git checkout {base_commit} failed: {checkout.stderr.strip()[-500:]}"
        )


def apply_patch(repo_dir: Path, patch_text: str) -> None:
    """Apply a unified/git diff to repo_dir via `git apply`."""
    if not patch_text.strip():
        return
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, dir=str(repo_dir)
    ) as handle:
        handle.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
        patch_path = handle.name
    try:
        result = _run(["git", "apply", "--whitespace=nowarn", patch_path], cwd=repo_dir)
        if result.returncode != 0:
            raise RuntimeError(f"git apply failed: {result.stderr.strip()[-800:]}")
    finally:
        Path(patch_path).unlink(missing_ok=True)


def install_repo(
    repo_dir: Path, python: str, extra_pins: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Editable-install the repo + pytest (+ pinned test reqs + extra pins).

    Installs the repo's own compiled `requirements/tests.txt` when present (it
    carries the exact test deps, e.g. blinker), and applies extra_pins last so
    they win the resolution (e.g. Werkzeug<3 for old flask).
    """
    cmd = ["uv", "pip", "install", "--python", python, "-e", str(repo_dir), "pytest"]
    tests_txt = repo_dir / "requirements" / "tests.txt"
    if tests_txt.exists():
        cmd += ["-r", str(tests_txt)]
    if extra_pins:
        cmd += list(extra_pins)
    return _run(cmd)


# pytest -rA prints one summary line per test as "PASSED <nodeid>" (status first).
_STATUS_RE = re.compile(r"^(?P<status>PASSED|FAILED|ERROR)\s+(?P<id>\S+::\S+)", re.M)


def run_pytest(repo_dir: Path, python: str, test_ids: list[str]) -> dict:
    """Run the given pytest test ids and parse per-test + summary results."""
    if not test_ids:
        return {"passed": 0, "failed": 0, "per_test": {}, "raw": "", "exit_code": 0}
    result = _run(
        [
            python,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--tb=no",
            "-q",
            "-rA",
            *test_ids,
        ],
        cwd=repo_dir,
    )
    output = result.stdout + "\n" + result.stderr
    per_test = {
        match.group("id"): match.group("status") == "PASSED"
        for match in _STATUS_RE.finditer(output)
    }
    passed = sum(1 for ok in per_test.values() if ok)
    failed = sum(1 for ok in per_test.values() if not ok)
    return {
        "passed": passed,
        "failed": failed,
        "per_test": per_test,
        "raw": output[-4000:],
        "exit_code": result.returncode,
    }


def evaluate(
    repo_dir: Path, python: str, fail_to_pass: list[str], pass_to_pass: list[str]
) -> dict:
    """Run F2P + P2P and decide `resolved` (all F2P pass and no P2P regresses)."""
    result = run_pytest(repo_dir, python, fail_to_pass + pass_to_pass)
    per = result["per_test"]
    f2p_ok = sum(1 for t in fail_to_pass if per.get(t) is True)
    p2p_ok = sum(1 for t in pass_to_pass if per.get(t) is True)
    resolved = f2p_ok == len(fail_to_pass) and p2p_ok == len(pass_to_pass)
    return {
        "resolved": resolved,
        "f2p_passed": f2p_ok,
        "f2p_total": len(fail_to_pass),
        "p2p_passed": p2p_ok,
        "p2p_total": len(pass_to_pass),
        "per_test": per,
        "raw": result["raw"],
    }


def _make_venv(root: Path, python_version: str) -> str:
    """Create a throwaway uv venv on the given Python version; return its python."""
    venv_dir = root / "venv"
    created = _run(["uv", "venv", "--python", python_version, str(venv_dir)])
    if created.returncode != 0:
        raise RuntimeError(f"uv venv failed: {created.stderr.strip()[-400:]}")
    return str(venv_dir / "bin" / "python")


def _selftest(instance_id: str) -> int:
    """Prove the harness with the golden patch — no model tokens spent."""
    print(f"→ Fetching instance {instance_id} …")
    inst = fetch_instance(instance_id)
    f2p = json.loads(inst["FAIL_TO_PASS"])
    p2p = json.loads(inst["PASS_TO_PASS"])
    spec = env_spec(inst["repo"])
    print(
        f"  repo={inst['repo']} base={inst['base_commit'][:10]} "
        f"F2P={len(f2p)} P2P={len(p2p)} python={spec['python']} pins={spec['pins']}"
    )

    work = Path(tempfile.mkdtemp(prefix="swebench-selftest-"))
    try:
        repo_dir = work / "repo"
        print("→ Cloning + checking out base_commit …")
        clone_checkout(repo_dir, repo_url_from(inst["repo"]), inst["base_commit"])
        print("→ Applying test_patch (adds the failing test) …")
        apply_patch(repo_dir, inst["test_patch"])
        python = _make_venv(work, spec["python"])
        print("→ Installing repo + pytest (this may take a moment) …")
        installed = install_repo(repo_dir, python, spec["pins"])
        if installed.returncode != 0:
            print(f"  ❌ install failed:\n{installed.stderr[-1200:]}")
            return 1

        print("→ [1/2] BEFORE golden patch (expect NOT resolved) …")
        before = evaluate(repo_dir, python, f2p, p2p)
        print(
            f"    resolved={before['resolved']} "
            f"F2P={before['f2p_passed']}/{before['f2p_total']} "
            f"P2P={before['p2p_passed']}/{before['p2p_total']}"
        )

        print("→ Applying GOLDEN patch (the reference fix) …")
        apply_patch(repo_dir, inst["patch"])
        print("→ [2/2] AFTER golden patch (expect RESOLVED) …")
        after = evaluate(repo_dir, python, f2p, p2p)
        print(
            f"    resolved={after['resolved']} "
            f"F2P={after['f2p_passed']}/{after['f2p_total']} "
            f"P2P={after['p2p_passed']}/{after['p2p_total']}"
        )

        ok = (not before["resolved"]) and after["resolved"]
        print(
            f"\n{'✅' if ok else '❌'} Self-test {'PASSED' if ok else 'FAILED'}: "
            "golden patch flips the F2P test and keeps P2P green."
        )
        if not ok:
            print(f"  before.raw tail:\n{before['raw'][-800:]}")
            print(f"  after.raw tail:\n{after['raw'][-800:]}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        metavar="INSTANCE_ID",
        help="Prove the harness with the golden patch",
    )
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest(args.selftest)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
