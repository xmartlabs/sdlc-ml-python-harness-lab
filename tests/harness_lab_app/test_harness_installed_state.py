"""Tests for Phase 4 read-only installed-state and sync preview.

Covers strict release-ref validation, full-SHA resolution, sync-protocol reads,
installed-manifest round-trips, legacy `.template-version` migration, and the
read-only governance/forbidden-path classification. Git-touching functions run
against miniature temporary repositories to stay honest.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_lab_app.harness_lifecycle import errors, manifests, releases, sync
from harness_lab_app.harness_lifecycle.models import (
    InstalledManifest,
    InstalledRef,
    SyncInfo,
)

FIXED = datetime(2026, 7, 24, 10, 0, 0, tzinfo=UTC)

_GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(root: Path, *args: str) -> str:
    env = dict(_GIT_ENV)
    env["HOME"] = str(root)
    out = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_REGISTRY = """\
schema_version = 1

[template_sync]
protocol = 1
governance_paths = [".github/architecture.md", ".github/skills", "adapters/templates"]
platform_paths = ["Makefile", "adapters/registry.toml"]
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "adapters/registry.toml", _REGISTRY)
    _write(root, ".github/architecture.md", "# Architecture\nv1\n")
    _write(root, "Makefile", "check:\n\t@echo ok\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "tag", "-a", "v0.2.0", "-m", "r")
    return root


# --------------------------------------------------------------------------- #
# Strict release-ref validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ref", ["v0.2.0", "v1.0.0", "v10.20.30"])
def test_is_release_ref_true(ref: str) -> None:
    assert releases.is_release_ref(ref)


@pytest.mark.parametrize(
    "ref", ["main", "0.2.0", "v0.2", "template/main", "v0.2.0-rc1", ""]
)
def test_is_release_ref_false(ref: str) -> None:
    assert not releases.is_release_ref(ref)


def test_validate_release_ref_rejects_main() -> None:
    with pytest.raises(errors.LifecycleError) as exc:
        releases.validate_release_ref("main")
    assert exc.value.exit_code == errors.EXIT_INVALID


def test_validate_release_ref_accepts_tag() -> None:
    releases.validate_release_ref("v0.2.0")  # no raise


@pytest.mark.parametrize("ref", ["v0.3.0", "v0.3.0-rc.1", "v1.2.3-alpha.2"])
def test_is_previewable_ref_accepts_final_and_prerelease(ref: str) -> None:
    assert releases.is_previewable_ref(ref)


@pytest.mark.parametrize("ref", ["main", "template/main", "v0.3", "0.3.0", ""])
def test_is_previewable_ref_rejects_non_tags(ref: str) -> None:
    assert not releases.is_previewable_ref(ref)


def test_previewable_accepts_rc_but_release_stays_strict() -> None:
    # A release candidate is measurable (preview/candidate) but never adoptable:
    # strict is_release_ref (used for adoption and "latest") still rejects it.
    releases.validate_previewable_ref("v0.3.0-rc.1")  # no raise
    assert not releases.is_release_ref("v0.3.0-rc.1")


# --------------------------------------------------------------------------- #
# Full-SHA resolution and tag listing
# --------------------------------------------------------------------------- #


def test_resolve_ref_commit_full_sha(repo: Path) -> None:
    sha = releases.resolve_ref_commit(repo, "v0.2.0")
    assert len(sha) == 40
    assert sha == _git(repo, "rev-parse", "v0.2.0^{commit}")


def test_resolve_ref_commit_missing(repo: Path) -> None:
    with pytest.raises(errors.LifecycleError) as exc:
        releases.resolve_ref_commit(repo, "v9.9.9")
    assert exc.value.exit_code == errors.EXIT_MISSING


def test_list_and_latest_release_tags(repo: Path) -> None:
    _git(repo, "tag", "-a", "v0.3.0", "-m", "r")
    tags = releases.list_release_tags(repo)
    assert tags[0] == "v0.3.0"
    assert set(tags) == {"v0.2.0", "v0.3.0"}
    assert releases.latest_release_tag(repo) == "v0.3.0"


def test_read_sync_protocol_at_ref(repo: Path) -> None:
    assert releases.read_sync_protocol_at_ref(repo, "v0.2.0") == 1


# --------------------------------------------------------------------------- #
# Installed manifest round-trip + legacy migration
# --------------------------------------------------------------------------- #


def _installed() -> InstalledManifest:
    return InstalledManifest(
        installed=InstalledRef(
            repository="marcosdh1987/ml-python-base",
            ref="v0.2.0",
            commit="a" * 40,
            channel="governance",
            adopted_at=FIXED,
        ),
        sync=SyncInfo(
            protocol=1, tools=["claude", "opencode"], paths=[".github/skills"]
        ),
    )


def test_installed_manifest_roundtrip(tmp_path: Path) -> None:
    m = _installed()
    path = manifests.write_installed_manifest(tmp_path, m)
    assert path == tmp_path / ".harness-version.yaml"
    assert manifests.read_installed_manifest(tmp_path) == m


def test_read_installed_manifest_absent(tmp_path: Path) -> None:
    assert manifests.read_installed_manifest(tmp_path) is None


def test_parse_legacy_template_version() -> None:
    text = (
        "# Last governance sync from the upstream template (make template-sync).\n"
        "ref: v0.2.0\ncommit: abc1234\ndate: 2026-07-20\ntool: all\n"
        "paths:\n  - .github/skills\n  - adapters/templates\n"
    )
    legacy = manifests.parse_legacy_template_version(text)
    assert legacy["ref"] == "v0.2.0"
    assert legacy["commit"] == "abc1234"
    assert legacy["paths"] == [".github/skills", "adapters/templates"]


# --------------------------------------------------------------------------- #
# Governance paths + forbidden-path classification (read-only preview)
# --------------------------------------------------------------------------- #


def test_governance_paths_from_registry(repo: Path) -> None:
    paths = sync.governance_paths(repo)
    assert ".github/architecture.md" in paths
    assert "adapters/templates" in paths


def test_governance_paths_default_when_absent(tmp_path: Path) -> None:
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters/registry.toml").write_text(
        "schema_version = 1\n", encoding="utf-8"
    )
    paths = sync.governance_paths(tmp_path)
    assert ".github/skills" in paths  # falls back to the default allowlist


@pytest.mark.parametrize(
    "path,forbidden",
    [
        ("src/x.py", True),
        ("tests/y.py", True),
        ("Makefile", True),
        ("adapters/registry.toml", True),
        ("scripts/z.py", True),
        (".github/skills/a.md", False),
        ("adapters/templates/t.j2", False),
    ],
)
def test_is_forbidden(path: str, forbidden: bool) -> None:
    assert sync.is_forbidden(path) is forbidden


def test_classify_preview_buckets() -> None:
    gov = [".github/skills", ".github/architecture.md", "adapters/templates"]
    preview = sync.classify_preview(
        [
            ("M", ".github/architecture.md"),
            ("A", ".github/skills/new.md"),
            ("M", "Makefile"),
            ("M", "adapters/registry.toml"),
            ("M", "README.md"),
        ],
        gov,
    )
    assert ".github/architecture.md" in preview.governance
    assert ".github/skills/new.md" in preview.governance
    assert "Makefile" in preview.forbidden
    assert "adapters/registry.toml" in preview.forbidden
    assert "README.md" in preview.other
    assert preview.has_blockers is True


def test_classify_preview_clean_governance_only() -> None:
    gov = [".github/skills"]
    preview = sync.classify_preview([("A", ".github/skills/new.md")], gov)
    assert preview.governance == (".github/skills/new.md",)
    assert preview.forbidden == ()
    assert preview.has_blockers is False


def test_changed_paths_between(repo: Path) -> None:
    _write(repo, ".github/architecture.md", "# Architecture\nv2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "edit")
    _git(repo, "tag", "-a", "v0.3.0", "-m", "r")
    entries = sync.changed_paths_between(repo, "v0.2.0", "v0.3.0")
    assert ("M", ".github/architecture.md") in entries


# --------------------------------------------------------------------------- #
# CLI wiring (read-only, no template remote required)
# --------------------------------------------------------------------------- #


def test_cli_sync_preview_rejects_non_release_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.harness_lifecycle import cli

    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    assert cli.main(["sync-preview", "--ref", "main"]) == errors.EXIT_INVALID


def test_cli_status_not_installed_no_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.harness_lifecycle import cli

    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(releases, "template_remote_configured", lambda *_a, **_k: False)
    assert cli.main(["status"]) == errors.EXIT_OK
