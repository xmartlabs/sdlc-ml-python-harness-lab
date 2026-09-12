"""Target repositories: URL safety, ref pinning, and what a pin must survive.

The properties under test are the ones ADR-0029 exists to guarantee — a run
carries the *commit* it used, and every path that reproduces a run (clone,
retry, continuation, batch repetition) reproduces that commit rather than
re-reading a moving ref.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import Case, CaseType, TargetStatus
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import (
    CaseCreate,
    RunBatchCreate,
    RunConfigCreate,
    RunCreate,
    TargetRepositoryCreate,
    TargetRepositoryUpdate,
)
from harness_lab_app.target_service import (
    TargetError,
    TargetService,
    resolve_ref,
    validate_repository_url,
)
from harness_lab_app.validation import build_validation_spec

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402

_SHA = "a" * 40
_OTHER_SHA = "b" * 40


def _target(**over) -> TargetRepositoryCreate:
    base = {
        "target_id": "payments",
        "name": "Payments API",
        "repository_url": "https://github.com/acme/payments.git",
        "default_ref": "main",
    }
    base.update(over)
    return TargetRepositoryCreate(**base)


def _ls_remote(monkeypatch, stdout: str, returncode: int = 0) -> list[list[str]]:
    """Stub `git ls-remote`, recording the argv it was called with."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# --------------------------------------------------------------------------
# URL validation is a security boundary, not a convenience
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/payments.git",
        "http://git.internal/acme/payments",
        "ssh://git@github.com/acme/payments.git",
        "git://example.com/acme/payments",
        "git@github.com:acme/payments.git",
    ],
)
def test_accepts_the_url_shapes_git_actually_takes(url):
    assert validate_repository_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "--upload-pack=touch /tmp/pwned",
        "-o ProxyCommand=evil",
        "/etc/passwd",
        "",
        "   ",
    ],
)
def test_rejects_urls_git_would_read_as_options_or_paths(url):
    with pytest.raises(TargetError):
        validate_repository_url(url)


# --------------------------------------------------------------------------
# Ref resolution
# --------------------------------------------------------------------------


def test_a_full_sha_resolves_to_itself_without_asking_the_remote(monkeypatch):
    calls = _ls_remote(monkeypatch, "")
    assert resolve_ref("https://example.com/a.git", _SHA) == _SHA
    assert calls == []


def test_an_annotated_tag_resolves_to_the_commit_it_points_at(monkeypatch):
    # The tag object's own sha is not the commit; the peeled `^{}` entry is.
    _ls_remote(
        monkeypatch,
        f"{_OTHER_SHA}\trefs/tags/v1.0\n{_SHA}\trefs/tags/v1.0^{{}}\n",
    )
    assert resolve_ref("https://example.com/a.git", "v1.0") == _SHA


def test_a_branch_wins_over_a_same_named_tag(monkeypatch):
    _ls_remote(
        monkeypatch,
        f"{_SHA}\trefs/heads/release\n{_OTHER_SHA}\trefs/tags/release\n",
    )
    assert resolve_ref("https://example.com/a.git", "release") == _SHA


def test_an_abbreviated_sha_says_why_it_cannot_be_resolved(monkeypatch):
    _ls_remote(monkeypatch, "")
    with pytest.raises(TargetError, match="abbreviated"):
        resolve_ref("https://example.com/a.git", "abc1234")


def test_a_failing_ls_remote_surfaces_gits_own_message(monkeypatch):
    _ls_remote(monkeypatch, "", returncode=128)
    with pytest.raises(TargetError, match="boom"):
        resolve_ref("https://example.com/a.git", "main")


# --------------------------------------------------------------------------
# Service CRUD
# --------------------------------------------------------------------------


def test_create_get_and_reject_duplicate(session):
    service = TargetService(session)
    created = service.create_target(_target())
    assert created.target_id == "payments"
    assert service.get_target("payments").name == "Payments API"
    with pytest.raises(TargetError, match="already exists"):
        service.create_target(_target(name="Other"))


def test_update_is_partial_and_revalidates_the_url(session):
    service = TargetService(session)
    service.create_target(_target())
    updated = service.update_target("payments", TargetRepositoryUpdate(team="payments"))
    assert updated.team == "payments"
    assert updated.name == "Payments API"  # untouched
    with pytest.raises(TargetError):
        service.update_target(
            "payments", TargetRepositoryUpdate(repository_url="--upload-pack=x")
        )


def test_delete_archives_so_historical_runs_keep_a_readable_target(session):
    service = TargetService(session)
    service.create_target(_target())
    service.delete_target("payments")

    assert service.get_target("payments").status == TargetStatus.archived
    assert service.list_targets() == []
    assert len(service.list_targets(include_archived=True)) == 1


# --------------------------------------------------------------------------
# Run creation pins a commit
# --------------------------------------------------------------------------


def _seed_case(session) -> Case:
    return CaseService(session).create_case(
        CaseCreate(
            case_id="add-endpoint",
            name="Add endpoint",
            type=CaseType.repo_task,
            prompt_variants={"default": "Add an endpoint with tests"},
            validation_commands=["pytest {workspace}"],
            scoring_rubric="10 when the endpoint exists and its tests pass.",
        )
    )


def _config(**over) -> RunConfigCreate:
    base = {
        "case_id": "add-endpoint",
        "prompt_variant": "default",
        "executor_model": "stub-model",
        "target_repo_id": "payments",
    }
    base.update(over)
    return RunConfigCreate(**base)


@pytest.fixture
def lab(session, monkeypatch):
    """A session with a case, a target, and every network call stubbed out."""
    _seed_case(session)
    TargetService(session).create_target(_target())
    service = RunService(session)
    monkeypatch.setattr(service, "_check_runner_image", lambda: None)
    monkeypatch.setattr(service, "_publish", lambda _run_id: None)
    monkeypatch.setattr(
        "harness_lab_app.run_service.resolve_ref", lambda _url, _ref: _SHA
    )
    return service


def test_create_run_pins_the_resolved_commit(lab, session):
    run = lab.create_run(RunCreate(config=_config()))
    config = lab.get_run_config(run.run_config_id)

    assert config.target_repo_id == "payments"
    assert config.target_commit == _SHA
    # The ref stays empty because the request did not name one: the run form
    # falls through to the repository's default_ref at provisioning time.
    assert config.target_ref == ""


def test_an_unknown_target_fails_before_anything_is_persisted(lab):
    with pytest.raises(RunPreflightError, match="Unknown target repository"):
        lab.create_run(RunCreate(config=_config(target_repo_id="ghost")))


def test_an_archived_target_cannot_start_a_new_run(lab, session):
    TargetService(session).delete_target("payments")
    with pytest.raises(RunPreflightError, match="archived"):
        lab.create_run(RunCreate(config=_config()))


def test_a_ref_that_cannot_be_resolved_fails_at_creation(lab, monkeypatch):
    def boom(url, ref):
        raise TargetError("no such ref")

    monkeypatch.setattr("harness_lab_app.run_service.resolve_ref", boom)
    with pytest.raises(RunPreflightError, match="no such ref"):
        lab.create_run(RunCreate(config=_config()))


def test_a_run_without_a_target_records_no_pin(lab):
    run = lab.create_run(RunCreate(config=_config(target_repo_id="")))
    config = lab.get_run_config(run.run_config_id)
    assert (config.target_repo_id, config.target_ref, config.target_commit) == (
        "",
        "",
        "",
    )


def test_cloning_a_run_reuses_its_commit_instead_of_re_resolving(lab, monkeypatch):
    """The trap ADR-0029 names: 'same arm, new trial' must not follow `main`."""
    original = lab.create_run(RunCreate(config=_config()))

    def moved(url, ref):  # someone pushed between the two runs
        return _OTHER_SHA

    monkeypatch.setattr("harness_lab_app.run_service.resolve_ref", moved)
    clone = lab.clone_run(original.run_id)

    assert lab.get_run_config(clone.run_config_id).target_commit == _SHA


def test_a_continuation_stays_on_its_parents_commit(lab, monkeypatch, tmp_path):
    parent = lab.create_run(RunCreate(config=_config()))
    lab.mark_running(parent.run_id)
    lab.mark_finished(parent.run_id, exit_code=0)
    workspace = tmp_path / parent.run_id
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        "harness_lab_app.run_service.continuation_source_dir",
        lambda _run_id: workspace,
    )
    monkeypatch.setattr(
        "harness_lab_app.run_service.resolve_ref", lambda _url, _ref: _OTHER_SHA
    )

    child = lab.continue_run(parent.run_id, "keep going")

    assert lab.get_run_config(child.run_config_id).target_commit == _SHA


# --------------------------------------------------------------------------
# Batch: the Mode B axis
# --------------------------------------------------------------------------


def test_a_batch_over_repositories_resolves_once_per_repository(
    session, monkeypatch, lab
):
    TargetService(session).create_target(
        _target(target_id="inventory", repository_url="https://x.invalid/inv.git")
    )
    calls: list[str] = []

    def counting_resolve(url, ref):
        calls.append(url)
        return _SHA

    monkeypatch.setattr("harness_lab_app.run_service.resolve_ref", counting_resolve)

    batch_id, runs = lab.create_batch(
        RunBatchCreate(
            case_ids=["add-endpoint"],
            prompt_variants=["default"],
            executor_models=["stub-model"],
            target_repo_ids=["payments", "inventory"],
            repetitions=3,
        )
    )

    assert len(runs) == 6  # 2 repositories x 3 repetitions
    # Three repetitions of an arm must share a commit, so one lookup per repo.
    assert len(calls) == 2
    commits = {lab.get_run_config(r.run_config_id).target_commit for r in runs}
    assert commits == {_SHA}
    assert batch_id.startswith("batch-")


# --------------------------------------------------------------------------
# What reaches the runner
# --------------------------------------------------------------------------


def test_case_spec_carries_the_target_and_its_provisioning_discriminator(
    lab, session, tmp_path, monkeypatch
):
    from harness_lab_app import tasks

    run = lab.create_run(RunCreate(config=_config()))
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    tasks._write_case_spec(
        session,
        run.run_id,
        "add-endpoint",
        "default",
        target=tasks._target_spec(session, run),
    )

    spec = json.loads((tmp_path / run.run_id / "case_spec.json").read_text())
    assert spec["source"] == "git_target"
    assert spec["target"]["commit"] == _SHA
    assert spec["target"]["repo_url"] == "https://github.com/acme/payments.git"
    # The ref was unset on the config, so the repository's default is what the
    # runner will report — the commit is what it will check out.
    assert spec["target"]["ref"] == "main"


def test_a_local_run_stages_no_target_block(lab, session, tmp_path, monkeypatch):
    from harness_lab_app import tasks

    run = lab.create_run(RunCreate(config=_config(target_repo_id="")))
    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    assert tasks._target_spec(session, run) is None


def test_the_runner_provisions_from_the_discriminator_not_the_case_type():
    assert bench_run.spec_provisions_repo({"source": "git_target", "repo": ""})
    assert not bench_run.spec_provisions_repo({"case_type": "repo_task", "repo": ""})
    # Unchanged for the SWE lanes, which are gated on a cloneable slug.
    assert bench_run.spec_provisions_repo({"repo": "pallets/flask"})


def test_provisioning_refuses_a_target_with_no_resolved_commit(tmp_path):
    workspace = tmp_path / "ws"
    spec = {
        "source": "git_target",
        "target": {"target_id": "payments", "repo_url": "https://x/y.git"},
    }
    with pytest.raises(SystemExit, match="resolved commit"):
        bench_run.provision_workspace(workspace, spec)


# --------------------------------------------------------------------------
# Validation defers to the repository's own gates
# --------------------------------------------------------------------------


def test_the_targets_commands_replace_the_cases(session):
    case = _seed_case(session)
    spec = build_validation_spec(case, "run-1", ["make test"])

    rendered = [command[-1] for command in spec["commands"]]
    # Prefixed with the `cd`: a repository's gate is written relative to its own
    # root, and the lab's root is not it (ADR-0062).
    assert rendered == ["cd workspace/run-1 && make test"]
    assert not any("pytest" in command for command in rendered)


def test_a_target_without_commands_leaves_the_case_in_charge(session):
    case = _seed_case(session)
    spec = build_validation_spec(case, "run-1", None)
    assert [c[-1] for c in spec["commands"]] == ["pytest workspace/run-1"]


# --------------------------------------------------------------------------
# The pin is part of the arm
# --------------------------------------------------------------------------


def test_the_commit_changes_the_condition_hash_but_absence_never_does():
    from harness_lab_app.provenance import compute_condition_hash

    def digest(target=None):
        return compute_condition_hash(
            "harness-hash",
            {"executor_model": "m", "planner_model": None},
            "prompt-hash",
            "case-hash",
            target=target,
        )

    baseline = digest()
    at_sha = digest({"target_id": "payments", "commit": _SHA})
    at_other = digest({"target_id": "payments", "commit": _OTHER_SHA})

    # Two commits of one repository are two arms, not repetitions of one.
    assert at_sha != at_other
    assert at_sha != baseline
    # A run with no target must hash exactly as it did before targets existed.
    assert digest(None) == digest({}) == baseline
