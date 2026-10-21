"""`make harness-check` on a real (temporary) git repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import harness_check  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-C",
            str(repo),
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    return repo


def test_a_failing_check_exits_1_and_names_the_fix(tmp_path, capsys):
    repo = _repo(
        tmp_path, {"Makefile": "test:\n\tpytest || echo none\n", "AGENTS.md": "x\n"}
    )
    code = harness_check.main(["--repo", str(repo)])
    out = capsys.readouterr().out
    assert code == 1
    assert "✗ FAIL  Verification can fail" in out
    assert "fix →" in out


def test_a_clean_repo_exits_0(tmp_path, capsys):
    repo = _repo(
        tmp_path,
        {
            "Makefile": "check:\n\tpytest -q\n",
            "AGENTS.md": "# Rules\n",
            ".github/workflows/ci.yml": "jobs:\n  g:\n    steps:\n      - run: make check\n",
        },
    )
    assert harness_check.main(["--repo", str(repo)]) == 0


def test_reads_the_committed_tree_not_the_working_copy(tmp_path):
    repo = _repo(tmp_path, {"Makefile": "check:\n\tpytest -q\n", "AGENTS.md": "x\n"})
    (repo / "Makefile").write_text("check:\n\tpytest || true\n")  # uncommitted
    assert harness_check.main(["--repo", str(repo)]) == 0


def test_a_path_that_is_not_a_repo_exits_2(tmp_path, capsys):
    assert harness_check.main(["--repo", str(tmp_path)]) == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_a_folder_inside_a_repo_checks_the_whole_repo(tmp_path):
    repo = _repo(
        tmp_path, {"Makefile": "test:\n\tpytest || echo none\n", "src/app.py": ""}
    )
    assert harness_check.main(["--repo", str(repo / "src")]) == 1
