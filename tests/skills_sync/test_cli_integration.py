"""End-to-end CLI tests exercising sync and ingest against a temp repo."""

from __future__ import annotations

import os
from pathlib import Path

from ml_python_base.skills_sync.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "adapters/registry.toml"


def _seed_governed(root: Path) -> None:
    internal = root / ".github/skills"
    internal.mkdir(parents=True)
    (internal / "alpha.md").write_text(
        "---\nname: alpha\ndescription: does alpha\n---\n", encoding="utf-8"
    )
    (root / ".github/skills-external").mkdir(parents=True)


def test_sync_materializes_native_views(tmp_path: Path) -> None:
    _seed_governed(tmp_path)
    code = main(["--root", str(tmp_path), "--registry", str(REGISTRY), "sync"])
    assert code == 0

    claude = tmp_path / ".claude/skills/alpha/SKILL.md"
    assert claude.is_symlink()
    assert os.readlink(claude) == "../../../.github/skills/alpha.md"

    antigravity = tmp_path / ".agents/skills/alpha/SKILL.md"
    assert antigravity.is_file() and not antigravity.is_symlink()
    assert (tmp_path / ".agents/skills/.generated-manifest.tsv").is_file()
    assert (tmp_path / "skills-lock.json").is_file()


def test_ingest_promotes_adhoc_skill(tmp_path: Path) -> None:
    _seed_governed(tmp_path)
    adhoc = tmp_path / ".agents/skills/freshly_installed"
    adhoc.mkdir(parents=True)
    (adhoc / "SKILL.md").write_text(
        "---\nname: freshly_installed\ndescription: new\n---\n", encoding="utf-8"
    )

    code = main(["--root", str(tmp_path), "--registry", str(REGISTRY), "ingest"])
    assert code == 0
    promoted = tmp_path / ".github/skills-external/freshly_installed/SKILL.md"
    assert promoted.is_file()


def test_check_reports_drift_exit_code(tmp_path: Path) -> None:
    _seed_governed(tmp_path)
    # No native views built yet, but also no adapter files present in tmp, so the
    # only artifact that can drift is the lock file (absent => stale).
    main(["--root", str(tmp_path), "--registry", str(REGISTRY), "sync"])
    # Mutate a governed skill so the manifest/lock no longer match.
    (tmp_path / ".github/skills/alpha.md").write_text(
        "---\nname: alpha\ndescription: CHANGED\n---\nbody\n", encoding="utf-8"
    )
    code = main(["--root", str(tmp_path), "--registry", str(REGISTRY), "check"])
    assert code == 2  # EXIT_DRIFT
