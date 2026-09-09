"""CLI-level tests for the proposal command.

The LLM gateway is injected as a fake so the wiring — evidence extraction, ID
allocation, sanitized preview, and the DRY_RUN filesystem invariant — is exercised
without a network call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app.harness_lifecycle import cli

_DRAFT = {
    "title": "Add a retry budget to systematic debugging",
    "observation": {
        "failure_mode": "Agent repeated an equivalent failing command.",
        "expected_behavior": "Reframe the hypothesis after the retry budget.",
        "evidence_summary": "Seen in two independent runs.",
    },
    "affected_artifacts": [".github/skills/systematic_debugging.md"],
    "suggested_change": "Add an explicit retry budget and hypothesis reset rule.",
    "suggested_cases": ["systematic-debugging-v1"],
    "success_criteria": [
        {"metric": "repeated_equivalent_command_max", "operator": "lte", "value": 2}
    ],
    "confidence": "high",
}


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    run_id = "systematic-debugging-v1-K-003"
    run_dir = tmp_path / "data" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit.md").write_text(
        "# Audit\nRepeated a failing command.\n", encoding="utf-8"
    )
    (run_dir / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "condition_hash": "sha256:abc"}), encoding="utf-8"
    )
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path / "data" / "runs")
    monkeypatch.setattr(
        cli.drafting, "load_gateway", lambda: (lambda *_args: json.dumps(_DRAFT))
    )
    return run_id


def _proposal_files(root: Path) -> list[Path]:
    base = root / "data" / "harness-proposals"
    return list(base.rglob("HEP-*.yaml")) if base.exists() else []


def test_dry_run_writes_nothing(tmp_path: Path, wired: str) -> None:
    rc = cli.main(["proposal", "--run", wired, "--dry-run"])
    assert rc == cli.EXIT_OK
    assert _proposal_files(tmp_path) == []


def test_proposal_writes_versioned_yaml(tmp_path: Path, wired: str) -> None:
    rc = cli.main(["proposal", "--run", wired])
    assert rc == cli.EXIT_OK
    files = _proposal_files(tmp_path)
    assert len(files) == 1
    assert files[0].stem.startswith("HEP-")


def test_proposal_missing_audit_is_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path / "data" / "runs")
    rc = cli.main(["proposal", "--run", "does-not-exist"])
    assert rc == cli.EXIT_PRECONDITION


def test_proposal_issue_not_found_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    rc = cli.main(["proposal-issue", "--proposal", "HEP-2026-999"])
    assert rc == cli.EXIT_MISSING


# --------------------------------------------------------------------------
# adopt (ADR-0052)
# --------------------------------------------------------------------------


def test_adopt_writes_the_installed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.harness-version.yaml` is what makes a run attributable to a release."""
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli.releases, "template_remote_configured", lambda _root: True)
    monkeypatch.setattr(cli.releases, "fetch_template", lambda _root: None)
    monkeypatch.setattr(
        cli.releases, "resolve_ref_commit", lambda _root, _ref: "a" * 40
    )
    monkeypatch.setattr(cli.sync, "read_registry_protocol", lambda _root: 1)
    monkeypatch.setattr(cli, "template_repository", lambda: "xmartlabs/ml-python-base")

    code = cli.main(["adopt", "--ref", "v0.4.0"])

    assert code == 0
    manifest = cli.manifests.read_installed_manifest(tmp_path)
    assert manifest is not None
    assert manifest.installed.ref == "v0.4.0"
    assert manifest.installed.commit == "a" * 40
    # The runner reads the same file with a regex, no yaml dependency.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import bench_run

    assert bench_run.read_template_ref(tmp_path) == "v0.4.0"


def test_adopt_refuses_a_non_release_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)

    code = cli.main(["adopt", "--ref", "main"])

    assert code != 0
    assert cli.manifests.read_installed_manifest(tmp_path) is None


def test_adopt_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli.releases, "template_remote_configured", lambda _root: True)
    monkeypatch.setattr(cli.releases, "fetch_template", lambda _root: None)
    monkeypatch.setattr(
        cli.releases, "resolve_ref_commit", lambda _root, _ref: "b" * 40
    )
    monkeypatch.setattr(cli.sync, "read_registry_protocol", lambda _root: 1)
    monkeypatch.setattr(cli, "template_repository", lambda: "xmartlabs/ml-python-base")

    assert cli.main(["adopt", "--ref", "v0.4.0", "--dry-run"]) == 0
    assert not (tmp_path / ".harness-version.yaml").exists()
