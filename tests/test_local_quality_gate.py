"""Exercise Make and pre-commit as subprocesses: failures must block Git gates."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def subprocess_env() -> dict[str, str]:
    """Do not let the invoking Git hook redirect or skip the nested runner."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "PRE_COMMIT_")) and key != "SKIP"
    }


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    tools = tmp_path / ".venv" / "bin"
    tools.mkdir(parents=True)
    (tools / "activate").write_text('export PATH="$PWD/.venv/bin:$PATH"\n')
    for name in ("pytest", "ruff", "nbstripout", "uv", "pre-commit"):
        command = tools / name
        command.write_text(
            "#!/bin/sh\n"
            f'echo \'{name}\' "$@" >> "$PWD/calls"\n'
            + ('exit "${PYTEST_EXIT:-0}"\n' if name == "pytest" else "exit 0\n")
        )
        command.chmod(0o755)
    return tmp_path


def run_make(project: Path, target: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", target],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{project / '.venv/bin'}{os.pathsep}{os.environ['PATH']}",
            **env,
        },
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("exit_code", [1, 2, 5])
@pytest.mark.parametrize("target", ["test", "test-unit", "test-integration"])
def test_test_targets_refuse_failures_collection_errors_and_no_tests(
    project: Path, target: str, exit_code: int
) -> None:
    result = run_make(project, target, PYTEST_EXIT=str(exit_code))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Tests completed!" not in result.stdout


def test_test_target_runs_the_full_suite_without_formatting(project: Path) -> None:
    result = run_make(project, "test", PYTEST_EXIT="0")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (project / "calls").read_text().splitlines()
    assert len(calls) == 1, calls
    # In parallel across cores: the suite is ~25 s with xdist, ~2 min without.
    assert calls[0].startswith("pytest tests/ -n auto --cov=src ")


@pytest.mark.parametrize(
    ("target", "selection"),
    [("test-unit", "-m not integration"), ("test-integration", "-m integration")],
)
def test_named_test_targets_select_the_intended_partition(
    project: Path, target: str, selection: str
) -> None:
    result = run_make(project, target)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (project / "calls").read_text().splitlines()
    assert len(calls) == 1, calls
    assert calls[0].startswith("pytest tests/ ")
    assert selection in calls[0]


def test_setup_installs_commit_message_commit_and_push_hooks(project: Path) -> None:
    result = run_make(project, "setup-hooks")
    assert result.returncode == 0, result.stdout + result.stderr
    install = next(
        line
        for line in (project / "calls").read_text().splitlines()
        if line.startswith("pre-commit install")
    )
    for stage in ("pre-commit", "commit-msg", "pre-push"):
        assert f"--hook-type {stage}" in install


@pytest.mark.parametrize("exit_code", [0, 1])
def test_gate_clears_git_repository_context_before_running_checks(
    tmp_path: Path, exit_code: int
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    make = tools / "make"
    make.write_text(
        "#!/bin/sh\n"
        '[ -z "${GIT_DIR+x}${GIT_WORK_TREE+x}${GIT_INDEX_FILE+x}" ] || exit 99\n'
        'echo "$@" > "$PWD/calls"\n'
        f"exit {exit_code}\n"
    )
    make.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/check_local_quality.sh")],
        cwd=tmp_path,
        env={
            **subprocess_env(),
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "GIT_DIR": str(tmp_path / "caller.git"),
            "GIT_WORK_TREE": str(tmp_path / "caller-worktree"),
            "GIT_INDEX_FILE": str(tmp_path / "caller-index"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == ["ci"]


@pytest.mark.parametrize("stage", ["pre-commit", "pre-push"])
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("caller_context", ["clean", "git-hook"])
def test_real_hook_runner_gates_pushes_and_leaves_commits_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    exit_code: int,
    caller_context: str,
) -> None:
    if caller_context == "git-hook":
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "caller.git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "caller-worktree"))
        monkeypatch.setenv("SKIP", "local-quality-gate")
    configuration = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [
        hook
        for repo in configuration["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] == "local-quality-gate"
    ]
    assert len(hooks) == 1, "A local quality gate must protect pushes."
    hook = hooks[0]
    # The full gate runs before a push only: a commit must take seconds, and
    # nothing reaches the remote unchecked (docs/development-hooks.md).
    assert hook["stages"] == ["pre-push"]
    assert hook["always_run"] is True
    assert hook["pass_filenames"] is False
    (tmp_path / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]})
    )
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/check_local_quality.sh",
        tmp_path / "scripts/check_local_quality.sh",
    )
    env = subprocess_env()
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], env=env, check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    make = tools / "make"
    make.write_text(
        "#!/bin/sh\n"
        '[ -z "${GIT_DIR+x}${GIT_WORK_TREE+x}${GIT_INDEX_FILE+x}" ] || exit 99\n'
        'echo "$@" >> "$PWD/calls"\n'
        f"exit {exit_code}\n"
    )
    make.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pre_commit",
            "run",
            "local-quality-gate",
            "--hook-stage",
            stage,
            "--all-files",
        ],
        cwd=tmp_path,
        env={
            **env,
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "PRE_COMMIT_HOME": str(tmp_path / "cache"),
            "GIT_DIR": str(tmp_path / ".git"),
            "GIT_WORK_TREE": str(tmp_path),
            "GIT_INDEX_FILE": str(tmp_path / ".git/index"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    if stage == "pre-commit":
        # pre-commit itself reports there is no such hook at commit time.
        assert not (tmp_path / "calls").exists(), result.stdout + result.stderr
        assert "No hook with id `local-quality-gate` in stage `pre-commit`" in (
            result.stdout + result.stderr
        )
        return
    assert (tmp_path / "calls").read_text().splitlines() == ["ci"], (
        result.stdout + result.stderr
    )
    assert (result.returncode == 0) == (exit_code == 0), result.stdout + result.stderr


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (["docs/guide.md", "memory/learnings.md"], "check-sync"),
        (["docs/guide.md", "src/app.py"], "ci"),
        (["CLAUDE.md"], "ci"),
        ([".github/skills/x/SKILL.md"], "ci"),
        (["docs/diagram.png"], "ci"),
    ],
)
def test_a_docs_only_push_skips_the_suite_and_anything_else_runs_it(
    tmp_path: Path, changed: list[str], expected: str
) -> None:
    env = subprocess_env()
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(repo)], env=env, check=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    (repo / "seed").write_text("seed\n")
    subprocess.run([*git, "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run([*git, "commit", "-qm", "seed"], cwd=repo, env=env, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    for name in changed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x\n")
    subprocess.run([*git, "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run([*git, "commit", "-qm", "change"], cwd=repo, env=env, check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    make = tools / "make"
    make.write_text('#!/bin/sh\necho "$@" > "$PWD/calls"\n')
    make.chmod(0o755)

    result = subprocess.run(
        ["sh", str(ROOT / "scripts/check_local_quality.sh")],
        cwd=repo,
        env={
            **env,
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "PRE_COMMIT_FROM_REF": base,
            "PRE_COMMIT_TO_REF": "HEAD",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "calls").read_text().split() == [expected]


@pytest.mark.parametrize(
    "hook_id",
    ["trailing-whitespace", "end-of-file-fixer", "ruff", "ruff-format", "nbstripout"],
)
@pytest.mark.parametrize("stage", ["pre-commit", "pre-push"])
def test_source_editing_hooks_are_confined_to_the_commit_stage(
    tmp_path: Path, hook_id: str, stage: str
) -> None:
    configuration = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    configured_hook = next(
        hook
        for repo in configuration["repos"]
        for hook in repo["hooks"]
        if hook["id"] == hook_id
    )
    # Keep the real stage configuration and replace only the formatter I/O.
    # Always run the controlled action so untracked test input cannot hide it.
    hook = {
        "id": hook_id,
        "name": "formatter stage control",
        "language": "system",
        "entry": shlex.join([sys.executable, "mutate.py"]),
        "always_run": True,
        "pass_filenames": False,
    }
    if "stages" in configured_hook:
        hook["stages"] = configured_hook["stages"]
    (tmp_path / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]})
    )
    (tmp_path / "mutate.py").write_text(
        'from pathlib import Path\nPath("formatted").write_text("changed")\n'
    )
    env = subprocess_env()
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], env=env, check=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pre_commit",
            "run",
            "--hook-stage",
            stage,
            "--all-files",
        ],
        cwd=tmp_path,
        env={**env, "PRE_COMMIT_HOME": str(tmp_path / "cache")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "formatted").exists() == (stage == "pre-commit")
