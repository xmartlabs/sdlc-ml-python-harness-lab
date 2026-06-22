"""Tests for scripts/init_project.py against a miniature template repo."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts/init_project.py"

TEMPLATE_REPO_LINE = "TEMPLATE_REPO ?= git@github.com:marcosdh1987/ml-python-base.git\n"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_project", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init_project = _load_script()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(root),
        },
    )


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    """A miniature clone reproducing every rename-relevant pattern."""
    root = tmp_path / "repo"
    pkg = root / "src/ml_python_base/skills_sync"
    pkg.mkdir(parents=True)
    (root / "src/ml_python_base/__init__.py").write_text(
        'GREETING = "Hello from ml-python-base!"\n', encoding="utf-8"
    )
    (pkg / "__init__.py").write_text(
        "from ml_python_base.skills_sync import cli\n", encoding="utf-8"
    )
    (pkg / "cli.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__/cli.cpython-311.pyc").write_bytes(b"stale")

    tests_dir = root / "tests/skills_sync"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_cli.py").write_text(
        "from ml_python_base.skills_sync.cli import x\n", encoding="utf-8"
    )

    (root / "pyproject.toml").write_text(
        '[project]\nname = "ml-python-base"\n\n'
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/ml_python_base"]\n\n'
        "[tool.ruff.lint.isort]\n"
        'known-first-party = ["ml_python_base"]\n',
        encoding="utf-8",
    )
    (root / "Makefile").write_text(
        TEMPLATE_REPO_LINE
        + "SKILLS_SYNC = uv run python -m ml_python_base.skills_sync\n",
        encoding="utf-8",
    )
    (root / ".env.example").write_text(
        "OLLAMA_BASE_URL=http://localhost:11434/v1\n", encoding="utf-8"
    )

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _run(root: Path, *extra: str) -> int:
    return init_project.main(["--name", "demo_project", "--root", str(root), *extra])


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


class TestNameValidation:
    @pytest.mark.parametrize(
        "bad", ["My-Project", "1abc", "class", "src", "ml_python_base", ""]
    )
    def test_rejects_invalid_names(self, bad: str) -> None:
        with pytest.raises(init_project.InitError):
            init_project.validate_name(bad)

    def test_accepts_snake_case(self) -> None:
        assert init_project.validate_name("churn_predictor2") == "churn_predictor2"


def test_refuses_dirty_tree(mini_repo: Path) -> None:
    (mini_repo / "WIP.txt").write_text("wip", encoding="utf-8")
    assert _run(mini_repo) == 1
    assert (mini_repo / "src/ml_python_base").is_dir()


def test_force_overrides_dirty_tree(mini_repo: Path) -> None:
    (mini_repo / "WIP.txt").write_text("wip", encoding="utf-8")
    assert _run(mini_repo, "--force") == 0
    assert (mini_repo / "src/demo_project").is_dir()


def test_refuses_when_already_initialized(mini_repo: Path) -> None:
    assert _run(mini_repo) == 0
    _git(mini_repo, "add", "-A")
    _git(mini_repo, "commit", "-q", "-m", "init")
    assert _run(mini_repo) == 1


def test_dry_run_writes_nothing(mini_repo: Path) -> None:
    before = _snapshot(mini_repo)
    assert _run(mini_repo, "--dry-run") == 0
    assert _snapshot(mini_repo) == before
    assert (mini_repo / "src/ml_python_base").is_dir()


def test_full_run_renames_and_rewrites(mini_repo: Path) -> None:
    assert _run(mini_repo) == 0

    assert not (mini_repo / "src/ml_python_base").exists()
    pkg_init = mini_repo / "src/demo_project/skills_sync/__init__.py"
    assert (
        pkg_init.read_text(encoding="utf-8")
        == "from demo_project.skills_sync import cli\n"
    )
    assert not list((mini_repo / "src").rglob("__pycache__"))

    test_file = mini_repo / "tests/skills_sync/test_cli.py"
    assert "demo_project.skills_sync" in test_file.read_text(encoding="utf-8")

    pyproject = (mini_repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "demo-project"' in pyproject
    assert 'packages = ["src/demo_project"]' in pyproject
    assert 'known-first-party = ["demo_project"]' in pyproject
    assert "ml_python_base" not in pyproject

    makefile = (mini_repo / "Makefile").read_text(encoding="utf-8")
    assert TEMPLATE_REPO_LINE in makefile  # upstream URL preserved
    assert "python -m demo_project.skills_sync" in makefile

    env = mini_repo / ".env"
    assert env.read_text(encoding="utf-8") == (mini_repo / ".env.example").read_text(
        encoding="utf-8"
    )


def test_keeps_existing_env(mini_repo: Path) -> None:
    (mini_repo / ".env").write_text("CUSTOM=1\n", encoding="utf-8")
    _git(mini_repo, "add", "-A")
    _git(mini_repo, "commit", "-q", "-m", "env")
    assert _run(mini_repo) == 0
    assert (mini_repo / ".env").read_text(encoding="utf-8") == "CUSTOM=1\n"


def test_init_mechanism_tests_are_never_rewritten(mini_repo: Path) -> None:
    own_test = mini_repo / "tests/init_project/test_init_project.py"
    own_test.parent.mkdir(parents=True)
    own_test.write_text("OLD = 'ml_python_base'\n", encoding="utf-8")
    _git(mini_repo, "add", "-A")
    _git(mini_repo, "commit", "-q", "-m", "own test")
    assert _run(mini_repo) == 0
    assert own_test.read_text(encoding="utf-8") == "OLD = 'ml_python_base'\n"


def test_rewrite_text_preserves_upstream_lines() -> None:
    text, replaced, skipped = init_project.rewrite_text(
        TEMPLATE_REPO_LINE + "import ml_python_base\n", "demo", "demo"
    )
    assert TEMPLATE_REPO_LINE in text
    assert "import demo\n" in text
    assert replaced == 1
    assert skipped == 1
