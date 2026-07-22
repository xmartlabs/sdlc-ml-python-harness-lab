#!/usr/bin/env python3
"""Shared SWE-bench Pro provisioning + containerized test helpers.

Pro instances ship a prebuilt per-instance Docker image (repo at /app, test env
baked in) instead of a pip-installable environment, so this lane provisions the
workspace by *copying the repo out of the image* and validates by *running the
official run_script inside it* — the pip+pytest helpers in swebench_common.py
never apply. Docker is required wherever these helpers run (worker container or
host, never the DB-blind runner). See docs/design-swe-bench-pro-lane.md.

Run the golden-patch self-test (no model tokens spent) with:

  uv run python scripts/swebench_pro_common.py --selftest instance_<id>
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from harness_lab_app.swebench_pro_client import normalize_instance

# Pro images are published amd64-only; pinning the platform makes the pull
# explicit under emulation (Apple Silicon dev) and is a no-op on the amd64
# bench server.
PLATFORM = os.environ.get("SWEBENCH_PRO_PLATFORM", "linux/amd64")
# Containerized suite timeout — generous: qutebrowser-style suites plus QEMU
# emulation on dev machines are slow.
DEFAULT_TEST_TIMEOUT = int(os.environ.get("SWEBENCH_PRO_TEST_TIMEOUT", "3600"))

_GIT_IDENTITY = ["-c", "user.email=bench@lab.local", "-c", "user.name=bench-lab"]


def _host_path(path: Path) -> str:
    """Translate a repo path to the HOST path for docker -v bind mounts.

    Inside the worker container the repo lives at /app but the docker daemon is
    the host's (docker-out-of-docker), so bind sources must use host paths —
    same trick as the sandbox launcher. HARNESS_LAB_HOST_REPO_ROOT is set on
    the worker service only; everywhere else the resolved path is already a
    host path.
    """
    resolved = path.resolve()
    host_root = os.environ.get("HARNESS_LAB_HOST_REPO_ROOT")
    if host_root:
        try:
            return str(Path(host_root) / resolved.relative_to("/app"))
        except ValueError:
            pass
    return str(resolved)


def _run(
    cmd: list[str], cwd: Path | None = None, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def docker_available() -> bool:
    try:
        return (
            _run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def pull_image(image: str) -> None:
    result = _run(["docker", "pull", "--platform", PLATFORM, image])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker pull {image} failed: {result.stderr.strip()[-500:]}"
        )


def provision(workspace: Path, spec: dict) -> None:
    """Provision workspace from the instance image (repo + test files, committed).

    1. Copy /app (incl. .git and full history) out of the instance image.
    2. Run ``before_repo_set_cmd`` — the official reset-to-base + checkout of
       the test files from a commit in the repo's own history (Pro's equivalent
       of applying test_patch).
    3. Commit the provisioned state, so ``finalize_workspace``'s
       ``git diff HEAD`` isolates the agent's edits (same trick as ADR-0009).
    """
    image = spec["dockerhub_image"]
    pull_image(image)
    created = _run(["docker", "create", "--platform", PLATFORM, image])
    if created.returncode != 0:
        raise RuntimeError(f"docker create failed: {created.stderr.strip()[-500:]}")
    cid = created.stdout.strip()
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        copied = _run(["docker", "cp", f"{cid}:/app/.", str(workspace)])
        if copied.returncode != 0:
            raise RuntimeError(f"docker cp failed: {copied.stderr.strip()[-500:]}")
    finally:
        _run(["docker", "rm", "-f", cid])

    script = spec.get("before_repo_set_cmd", "").strip()
    if script:
        result = _run(["bash", "-ec", script], cwd=workspace)
        if result.returncode != 0:
            raise RuntimeError(
                f"before_repo_set_cmd failed: {result.stderr.strip()[-800:]}"
            )

    add = _run(["git", *_GIT_IDENTITY, "add", "-A"], cwd=workspace)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr.strip()[-500:]}")
    commit = _run(
        [
            "git",
            *_GIT_IDENTITY,
            "commit",
            "--allow-empty",
            "-m",
            "swebench-pro: provisioned",
        ],
        cwd=workspace,
    )
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr.strip()[-500:]}")


def evaluate_containerized(
    workspace: Path, spec: dict, timeout: int = DEFAULT_TEST_TIMEOUT
) -> dict:
    """Run the official run_script + parser inside the instance image.

    Mounts the (agent-edited or archived) workspace over the image's /app and a
    staging dir with run_script.sh/parser.py at /eval; the image's baked-in env
    (PYTEST_ADDOPTS, Qt offscreen vars, …) applies as-is. Verdict mirrors the
    official eval: resolved ⇔ every F2P and P2P test name is PASSED in the
    parser output. Returns the same shape as ``swebench_common.evaluate``.
    """
    f2p = spec.get("fail_to_pass", [])
    p2p = spec.get("pass_to_pass", [])
    selected = ",".join(spec.get("selected_test_files_to_run", []))

    # Stage under the repo tree (not the system tmpdir): from the worker the
    # daemon is the host's, and only repo paths exist on both sides of the
    # bind mount (see _host_path).
    stage_root = _REPO_ROOT / "data" / "tmp"
    stage_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="swe-pro-eval-", dir=stage_root))
    try:
        (stage / "run_script.sh").write_text(spec["run_script"])
        (stage / "parser.py").write_text(spec["parser"])
        # run_script may exit nonzero on test failures (set -e); the parser must
        # still run, hence ';' not '&&'.
        inner = (
            f"cd /app && bash /eval/run_script.sh {selected!r} "
            f"> /eval/stdout.log 2> /eval/stderr.log; "
            f"python /eval/parser.py /eval/stdout.log /eval/stderr.log /eval/output.json"
        )
        result = _run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                PLATFORM,
                # The Pro images default to a bash entrypoint (upstream issue
                # #6: "you should not manually invoke bash"); passing bash as
                # CMD would run `bash bash …`. Overriding the entrypoint keeps
                # the invocation deterministic either way.
                "--entrypoint",
                "bash",
                "-v",
                f"{_host_path(workspace)}:/app",
                "-v",
                f"{_host_path(stage)}:/eval",
                spec["dockerhub_image"],
                "-lc",
                inner,
            ],
            timeout=timeout,
        )
        stdout_log = (
            (stage / "stdout.log").read_text(errors="replace")
            if (stage / "stdout.log").exists()
            else ""
        )
        stderr_log = (
            (stage / "stderr.log").read_text(errors="replace")
            if (stage / "stderr.log").exists()
            else ""
        )
        raw = (stdout_log + "\n" + stderr_log)[-4000:]

        output_path = stage / "output.json"
        if not output_path.exists():
            # Surface the container's own stdout/stderr too — a failed docker
            # invocation (bad entrypoint, missing mount) never reaches the
            # redirected /eval logs.
            container_out = (result.stdout + "\n" + result.stderr).strip()
            return {
                "resolved": False,
                "f2p_passed": 0,
                "f2p_total": len(f2p),
                "p2p_passed": 0,
                "p2p_total": len(p2p),
                "per_test": {},
                "raw": (raw + "\n" + container_out)[-4000:],
                "error": "parser produced no output.json",
            }
        parsed = json.loads(output_path.read_text())
        passed = {
            t["name"] for t in parsed.get("tests", []) if t.get("status") == "PASSED"
        }
        per_test = {
            t["name"]: t.get("status") == "PASSED" for t in parsed.get("tests", [])
        }
        f2p_ok = sum(1 for t in f2p if t in passed)
        p2p_ok = sum(1 for t in p2p if t in passed)
        resolved = f2p_ok == len(f2p) and p2p_ok == len(p2p)
        return {
            "resolved": resolved,
            "f2p_passed": f2p_ok,
            "f2p_total": len(f2p),
            "p2p_passed": p2p_ok,
            "p2p_total": len(p2p),
            "per_test": per_test,
            "raw": raw,
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def apply_patch(repo_dir: Path, patch_text: str) -> None:
    """Apply a unified/git diff via `git apply` (self-test helper)."""
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


def _report(label: str, verdict: dict) -> None:
    print(
        f"    [{label}] resolved={verdict['resolved']} "
        f"F2P={verdict['f2p_passed']}/{verdict['f2p_total']} "
        f"P2P={verdict['p2p_passed']}/{verdict['p2p_total']}"
    )
    if verdict.get("error"):
        print(f"    [{label}] ⚠️  {verdict['error']}")
        tail = verdict.get("raw", "").strip()[-600:]
        if tail:
            print(f"    [{label}] container tail:\n{tail}")


def _selftest(instance_id: str) -> int:
    """Prove the lane with the golden patch — no model tokens spent."""
    if not docker_available():
        print("❌ docker is required for the Pro lane self-test.")
        return 1
    print(f"→ Fetching {instance_id} from SWE-bench Pro …")
    spec = normalize_instance(instance_id)
    print(
        f"  repo={spec['repo']} base={spec['base_commit'][:10]} "
        f"F2P={len(spec['fail_to_pass'])} P2P={len(spec['pass_to_pass'])}\n"
        f"  image={spec['dockerhub_image']}"
    )

    work = Path(tempfile.mkdtemp(prefix="swe-pro-selftest-"))
    try:
        workspace = work / "workspace"
        print("→ Provisioning workspace from the instance image …")
        provision(workspace, spec)

        print("→ [1/2] BEFORE golden patch (expect NOT resolved) …")
        before = evaluate_containerized(workspace, spec)
        _report("before", before)

        print("→ Applying GOLDEN patch (the reference fix) …")
        apply_patch(workspace, spec["gold_patch"])
        print("→ [2/2] AFTER golden patch (expect RESOLVED) …")
        after = evaluate_containerized(workspace, spec)
        _report("after", after)

        ok = (not before["resolved"]) and after["resolved"]
        print(
            f"\n{'✅' if ok else '❌'} Self-test {'PASSED' if ok else 'FAILED'}: "
            "golden patch flips F2P and keeps P2P green."
        )
        if not ok:
            print(f"  before.raw tail:\n{before['raw'][-800:]}")
            print(f"  after.raw tail:\n{after['raw'][-800:]}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _provision_run(run_id: str) -> int:
    """Provision workspace/<run_id> from data/runs/<run_id>/case_spec.json.

    Worker entry point: the web lane's runner container is docker-less, so the
    worker (socket + CLI) shells out to this before launching it. The worker
    owns the managed run's freshness guarantee — a leftover workspace (e.g.
    from a crashed or retried attempt) is reset and re-provisioned, so the
    runner can trust whatever it finds and must never wipe it (it could not
    rebuild it without docker).
    """
    spec_path = _REPO_ROOT / "data" / "runs" / run_id / "case_spec.json"
    if not spec_path.exists():
        print(f"❌ No case_spec.json at {spec_path}")
        return 1
    spec = json.loads(spec_path.read_text())
    if spec.get("case_type") != "swe_pro":
        print(f"Not a swe_pro case ({spec.get('case_type')}) — nothing to do.")
        return 0
    workspace = _REPO_ROOT / "workspace" / run_id
    if workspace.exists():
        print(f"workspace/{run_id}/ exists — resetting for a fresh provision.")
        shutil.rmtree(workspace)
    print(f"→ Provisioning workspace/{run_id} from {spec.get('dockerhub_image')} …")
    provision(workspace, spec)
    print("✓ Provisioned.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        metavar="INSTANCE_ID",
        help="Prove the lane with the golden patch",
    )
    parser.add_argument(
        "--provision-run",
        metavar="RUN_ID",
        help="Provision workspace/<run_id> from its case_spec.json (worker entry)",
    )
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest(args.selftest)
    if args.provision_run:
        return _provision_run(args.provision_run)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
