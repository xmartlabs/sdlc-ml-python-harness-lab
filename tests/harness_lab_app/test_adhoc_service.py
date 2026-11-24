"""Ad hoc tasks end to end, short of a live model (ADR-0095).

The runner is replaced by what it leaves behind — an archived workspace with a
provisioning commit and a usage.json — so everything the lab does around a run
is exercised on real git repositories: copying the folder, pinning it, the
clean patch, the metrics that outlive the files, the decision, applying to the
operator's folder, and the purges.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, select

from harness_lab_app import (
    adhoc_apply,
    adhoc_service,
    case_service,
    checkout_service,
    config,
    local_sources_service,
    run_service,
    storage_service,
    target_service,
)
from harness_lab_app.adhoc_service import (
    AdhocConflictError,
    AdhocError,
    AdhocService,
    is_adhoc_id,
)
from harness_lab_app.models import (
    AdhocRun,
    Case,
    CaseType,
    HarnessSource,
    Run,
    RunConfig,
    RunState,
    TargetRepository,
    utc_now,
)
from harness_lab_app.storage_service import StorageService

SETTINGS_MODULES = (
    config,
    adhoc_service,
    adhoc_apply,
    storage_service,
    local_sources_service,
    checkout_service,
    case_service,
    run_service,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def lab(monkeypatch, tmp_path: Path):
    """A lab root plus a local sources root holding one real repository."""
    sources = tmp_path / "code"
    repo = sources / "shop"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("def total(items):\n    return sum(items)\n")
    (repo / "README.md").write_text("# shop\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    lab_root = tmp_path / "lab"
    lab_root.mkdir()
    settings = config.Settings(
        repo_root=lab_root,
        host_repo_root="/Users/dev/lab",
        local_sources_root=str(sources),
        host_local_sources_root="/Users/dev/code",
        execution_backend="subprocess",
        adhoc_retention_days=7,
    )
    for module in SETTINGS_MODULES:
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    # Preflight probes nothing in tests, and the claude CLI check reads a binary.
    monkeypatch.setattr(run_service.RunService, "preflight", lambda *_args: None)
    return {"settings": settings, "repo": repo, "lab": lab_root}


def _task(session: Session, lab, prompt: str = "Handle an empty cart.") -> object:
    return AdhocService(session).create_task("shop", prompt, title="Empty cart")


def _simulate_run(session: Session, lab, run_id: str, agent_edit: bool = True):
    """What the runner leaves behind: archive with provisioning commit + usage."""
    task_id = (
        session.exec(select(AdhocRun).where(AdhocRun.run_id == run_id)).one().task_id
    )
    checkout = lab["settings"].checkouts_dir / task_id
    run_dir = lab["settings"].data_runs_dir / run_id
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", str(checkout), str(workspace)],
        check=True,
        capture_output=True,
    )
    (workspace / "setup.log").write_text("installed deps\n")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", f"target: provisioned {task_id} @ abc")
    if agent_edit:
        (workspace / "app.py").write_text(
            "def total(items):\n    return sum(items or [])\n"
        )
        (workspace / "test_app.py").write_text(
            "from app import total\n\ndef test_empty():\n    assert total(None) == 0\n"
        )
    (run_dir / "usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 1200,
                "output_tokens": 300,
                "cache_read_tokens": 5000,
                "cost_usd": 0.0,
                "cost_source": "self_hosted",
                "time_minutes": 2.5,
                "turns": 4,
                "tool_calls": 9,
            }
        )
    )
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()
    run.state = RunState.awaiting_manual_score
    run.ended_at = utc_now()
    session.add(run)
    session.commit()


def _launch(session: Session, task) -> str:
    record = AdhocService(session).launch_run(
        task.task_id, harness="opencode", executor_model="lmstudio/qwen3-coder"
    )
    return record["run_id"]


# ---------------------------------------------------------------------------


def test_creating_a_task_copies_and_pins_without_touching_the_folder(session, lab):
    before = _git(lab["repo"], "status", "--porcelain")
    head = _git(lab["repo"], "rev-parse", "HEAD")

    task = _task(session, lab)

    assert is_adhoc_id(task.task_id)
    assert task.base_commit == head
    assert task.host_path == "/Users/dev/code/shop"
    copy = lab["settings"].checkouts_dir / task.task_id
    assert _git(copy, "rev-parse", "HEAD") == head
    target = session.exec(
        select(TargetRepository).where(TargetRepository.target_id == task.task_id)
    ).one()
    assert target.pinned_commit == head
    assert target.checkout_path == str(copy)
    case = session.exec(select(Case).where(Case.case_id == task.case_id)).one()
    assert case.type == CaseType.adhoc
    assert case.prompt_variants == {"task": "Handle an empty cart."}
    assert _git(lab["repo"], "status", "--porcelain") == before


def test_a_task_needs_a_prompt_and_a_repository(session, lab):
    with pytest.raises(AdhocError):
        AdhocService(session).create_task("shop", "   ")
    (lab["repo"].parent / "notes").mkdir()
    with pytest.raises(AdhocError, match="not a git repository"):
        AdhocService(session).create_task("notes", "do it")


def test_a_path_outside_the_sources_root_is_refused(session, lab):
    with pytest.raises(local_sources_service.LocalSourcesError):
        AdhocService(session).create_task("../lab", "do it")


def test_ad_hoc_work_stays_out_of_benchmark_views(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)

    assert task.case_id not in {
        case.case_id for case in case_service.CaseService(session).list_cases()
    }
    assert task.target_id not in {
        t.target_id for t in target_service.TargetService(session).list_targets()
    }
    assert run_id not in {r.run_id for r in run_service.RunService(session).list_runs()}
    assert run_id in {
        r.run_id for r in run_service.RunService(session).list_runs(include_adhoc=True)
    }


def test_every_run_of_a_task_starts_from_the_same_commit_in_native(session, lab):
    task = _task(session, lab)
    (lab["repo"] / "README.md").write_text("# shop v2\n")
    _git(lab["repo"], "commit", "-qam", "operator moved on")

    first = _launch(session, task)
    second = AdhocService(session).launch_run(task.task_id, harness="claude")["run_id"]

    configs = [
        session.get(
            RunConfig,
            session.exec(select(Run).where(Run.run_id == rid)).one().run_config_id,
        )
        for rid in (first, second)
    ]
    assert {c.target_commit for c in configs} == {task.base_commit}
    assert {c.harness_source for c in configs} == {HarnessSource.native}
    assert first != second and all(is_adhoc_id(r) for r in (first, second))


def test_finalizing_writes_the_clean_patch_keeps_metrics_and_the_workspace(
    session, lab
):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)

    AdhocService(session).finalize_run(run_id)

    run_dir = lab["settings"].data_runs_dir / run_id
    patch = (run_dir / "change.patch").read_text()
    assert "+    return sum(items or [])" in patch
    assert "test_app.py" in patch
    assert "setup.log" not in patch
    # Kept until the operator decides: the sandbox, the audit and the skills
    # panel all read it.
    assert (run_dir / "workspace").is_dir()
    record = AdhocService(session).run_record(run_id)
    assert record["has_workspace"] is True
    assert record["files_changed"] == 2
    assert record["input_tokens"] == 1200
    assert record["billed_tokens"] == 1200 + 300 + 5000
    assert record["cost_usd"] == 0.0
    assert record["cost_source"] == "self_hosted"
    assert record["minutes"] == 2.5


def test_apply_and_undo_go_through_the_folder_lease(session, lab, monkeypatch):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    monkeypatch.setattr(adhoc_apply, "folder_lease", lambda _source: _null_context())

    check = AdhocService(session).apply_check(run_id)
    assert check["can_apply"], check["blockers"]

    _apply(session, run_id, undo=False)
    assert "items or []" in (lab["repo"] / "app.py").read_text()
    record = AdhocService(session).run_record(run_id)
    assert record["apply_state"] == "applied"
    assert record["decision"] == "approved"
    assert set(AdhocService(session).get_adhoc_run(run_id).applied_hashes) == {
        "app.py",
        "test_app.py",
    }
    assert _git(lab["repo"], "diff", "--cached", "--name-only") == ""

    with pytest.raises(AdhocConflictError):
        AdhocService(session).begin_apply(run_id)

    _apply(session, run_id, undo=True)
    assert "items or []" not in (lab["repo"] / "app.py").read_text()
    assert AdhocService(session).run_record(run_id)["apply_state"] == "undone"


def test_a_second_apply_on_the_same_folder_is_refused_while_one_runs(session, lab):
    task = _task(session, lab)
    first = _launch(session, task)
    second = AdhocService(session).launch_run(task.task_id, harness="claude")["run_id"]
    for run_id in (first, second):
        _simulate_run(session, lab, run_id)
        AdhocService(session).finalize_run(run_id)

    AdhocService(session).begin_apply(first)

    with pytest.raises(AdhocConflictError, match="being applied"):
        AdhocService(session).begin_apply(second)


def test_a_refused_apply_leaves_the_folder_and_records_why(session, lab, monkeypatch):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    (lab["repo"] / "app.py").write_text("my work in progress\n")
    monkeypatch.setattr(adhoc_apply, "folder_lease", lambda _source: _null_context())

    _apply(session, run_id, undo=False)

    record = AdhocService(session).run_record(run_id)
    assert record["apply_state"] == "failed"
    assert "uncommitted" in record["apply_error"]
    assert (lab["repo"] / "app.py").read_text() == "my work in progress\n"


def test_rejecting_purges_files_and_keeps_the_measurement(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)

    record = AdhocService(session).decide(run_id, "rejected", reason="style")

    assert record["decision"] == "rejected"
    assert record["files_purged"] is True
    assert record["input_tokens"] == 1200
    assert not (lab["settings"].data_runs_dir / run_id).exists()
    assert session.exec(select(Run).where(Run.run_id == run_id)).one().purged_at


def test_an_applied_change_cannot_be_discarded(session, lab, monkeypatch):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    monkeypatch.setattr(adhoc_apply, "folder_lease", lambda _source: _null_context())
    _apply(session, run_id, undo=False)

    with pytest.raises(AdhocConflictError, match="Undo"):
        AdhocService(session).decide(run_id, "discarded")


def test_the_prompt_is_frozen_once_a_run_exists(session, lab):
    task = _task(session, lab)
    AdhocService(session).update_task(task.task_id, prompt="Better wording")
    _launch(session, task)

    with pytest.raises(AdhocConflictError):
        AdhocService(session).update_task(task.task_id, prompt="Changed again")


def test_deleting_a_task_removes_its_copy_runs_and_rows(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)

    AdhocService(session).delete_task(task.task_id)

    assert not (lab["settings"].checkouts_dir / task.task_id).exists()
    assert session.exec(select(AdhocRun)).first() is None
    assert session.exec(select(Run).where(Run.run_id == run_id)).first() is None
    assert lab["repo"].is_dir()


def test_the_detail_reports_how_far_the_folder_moved(session, lab):
    task = _task(session, lab)
    (lab["repo"] / "README.md").write_text("# moved\n")
    _git(lab["repo"], "commit", "-qam", "moved")

    detail = AdhocService(session).task_detail(task.task_id)

    assert detail["source"]["head_moved_by"] == 1


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_expired_adhoc_files_are_purged_and_metrics_stay(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    row = session.exec(select(AdhocRun).where(AdhocRun.run_id == run_id)).one()
    row.created_at = row.created_at.replace(year=row.created_at.year - 1)
    session.add(row)
    session.commit()

    result = StorageService(session).purge("adhoc_expired")

    assert result["runs"] == 1
    assert result["freed_bytes"] > 0
    assert AdhocService(session).run_record(run_id)["input_tokens"] == 1200


def test_benchmark_light_mode_drops_only_the_workspace(session, lab):
    runs = lab["settings"].data_runs_dir
    run_dir = runs / "tetris-v1-K-001"
    (run_dir / "workspace").mkdir(parents=True)
    (run_dir / "workspace" / "main.py").write_text("x" * 10_000)
    (run_dir / "sandbox-terminal").mkdir()
    (run_dir / "sandbox-terminal" / "main.py").write_text("x" * 5000)
    (run_dir / "diff.patch").write_text("+x\n")
    (run_dir / "usage.json").write_text("{}")
    config_row = RunConfig(case_id="tetris-v1", prompt_variant="harness")
    session.add(config_row)
    session.commit()
    session.add(
        Run(
            run_id="tetris-v1-K-001",
            run_config_id=config_row.id or 0,
            state=RunState.completed,
            ended_at=utc_now().replace(year=2020),
        )
    )
    session.commit()

    report = StorageService(session).report()
    assert report["benchmark_light_candidates"]["runs"] == 1

    result = StorageService(session).purge("benchmark_workspaces", older_than_days=14)

    assert result["runs"] == 1
    assert result["freed_bytes"] >= 15_000
    assert not (run_dir / "workspace").exists()
    assert not (run_dir / "sandbox-terminal").exists()
    assert (run_dir / "diff.patch").exists() and (run_dir / "usage.json").exists()
    assert session.exec(select(Run)).one().purged_at is not None


def test_a_recent_or_running_benchmark_run_is_never_light_purged(session, lab):
    runs = lab["settings"].data_runs_dir
    config_row = RunConfig(case_id="tetris-v1", prompt_variant="harness")
    session.add(config_row)
    session.commit()
    for run_id, state, ended in (
        ("tetris-v1-K-002", RunState.running, None),
        ("tetris-v1-K-003", RunState.completed, utc_now()),
    ):
        (runs / run_id / "workspace").mkdir(parents=True)
        session.add(
            Run(
                run_id=run_id,
                run_config_id=config_row.id or 0,
                state=state,
                ended_at=ended,
            )
        )
    session.commit()

    result = StorageService(session).purge("benchmark_workspaces", older_than_days=14)

    assert result["runs"] == 0
    assert (runs / "tetris-v1-K-002" / "workspace").exists()


def test_an_unknown_purge_scope_is_refused(session, lab):
    with pytest.raises(storage_service.StorageError):
        StorageService(session).purge("everything")


# ---------------------------------------------------------------------------


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _apply(session: Session, run_id: str, undo: bool) -> None:
    """The worker task's body, on the subprocess backend (no docker)."""
    service = AdhocService(session)
    service.begin_apply(run_id, undo=undo)
    row = service.get_adhoc_run(run_id)
    task = service.get_task(row.task_id)
    expected = dict(row.applied_hashes or {}) if undo else None
    with adhoc_apply.folder_lease(task.source_path):
        result = adhoc_apply.apply_locally(
            "undo" if undo else "apply",
            task.source_path,
            run_id,
            task.base_commit,
            expected,
        )
    ok = bool(result.get("can_apply"))
    service.finish_apply(
        run_id,
        undo=undo,
        ok=ok,
        error="; ".join(result.get("blockers") or []),
        applied_hashes=dict(result.get("applied_hashes") or {}),
    )


def test_the_runner_spec_provisions_the_lab_copy_in_native(session, lab, monkeypatch):
    """The DB-blind runner only sees case_spec.json: it must clone the copy."""
    from harness_lab_app import tasks

    monkeypatch.setattr(tasks, "get_settings", lambda: lab["settings"])
    monkeypatch.setattr(
        target_service, "get_settings", lambda: lab["settings"], raising=False
    )
    task = _task(session, lab)
    run_id = _launch(session, task)
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()

    tasks._write_case_spec(
        session,
        run_id,
        task.case_id,
        "task",
        target=tasks._target_spec(session, run),
        harness_source="native",
    )

    spec = json.loads(
        (lab["settings"].data_runs_dir / run_id / "case_spec.json").read_text()
    )
    assert spec["source"] == "git_target"
    assert spec["harness_source"] == "native"
    assert spec["prompt"] == "Handle an empty cart."
    assert spec["target"]["repo_url"] == str(
        lab["settings"].checkouts_dir / task.task_id
    )
    assert spec["target"]["commit"] == task.base_commit


def test_api_routes_answer_with_the_contract_shapes(session, lab, monkeypatch):
    from harness_lab_app import adhoc_api

    monkeypatch.setattr(adhoc_api, "_enqueue_run", lambda *_args: None)
    created = adhoc_api.create_adhoc_task(
        adhoc_api.AdhocTaskCreate(source="shop", prompt="Fix it"), session=session
    )
    assert created["task_id"].startswith("adhoc-")
    launched = adhoc_api.launch_adhoc_run(
        created["task_id"],
        adhoc_api.AdhocRunLaunch(harness="opencode", executor_model="lmstudio/q"),
        session=session,
    )
    assert launched["state"] == "pending"
    assert launched["decision"] == "pending"
    detail = adhoc_api.get_adhoc_task(created["task_id"], session=session)
    assert {"task", "runs", "source"} <= set(detail)
    listing = adhoc_api.list_adhoc_tasks(session=session)
    assert listing[0]["runs"] == 1 and listing[0]["live_runs"] == 1
    with pytest.raises(adhoc_api.HTTPException) as missing:
        adhoc_api.get_adhoc_task("adhoc-nope-001", session=session)
    assert missing.value.status_code == 404
    with pytest.raises(adhoc_api.HTTPException) as live:
        adhoc_api.delete_adhoc_task(created["task_id"], session=session)
    assert live.value.status_code == 409
    report = adhoc_api.storage_report(session=session)
    assert {"total_bytes", "categories", "adhoc_expired"} <= set(report)


def test_the_router_is_mounted_and_owns_the_contract_routes():
    """``include_router`` wraps rather than flattens on this FastAPI version
    (see test_local_api), so the router itself is checked, plus its mounting."""
    import inspect

    from harness_lab_app import api
    from harness_lab_app.adhoc_api import adhoc_router

    paths = {getattr(route, "path", "") for route in adhoc_router.routes}
    assert {
        "/api/adhoc/source",
        "/api/adhoc/tasks",
        "/api/adhoc/tasks/{task_id}",
        "/api/adhoc/tasks/{task_id}/runs",
        "/api/adhoc/pr-reviews",
        "/api/adhoc/runs/{run_id}",
        "/api/adhoc/runs/{run_id}/patch",
        "/api/adhoc/runs/{run_id}/apply-check",
        "/api/adhoc/runs/{run_id}/apply",
        "/api/adhoc/runs/{run_id}/undo",
        "/api/adhoc/runs/{run_id}/decision",
        "/api/storage",
        "/api/storage/purge",
    } <= paths
    assert "app.include_router(adhoc_router)" in inspect.getsource(api.create_app)


def test_undo_after_an_edit_is_refused_and_the_change_stays_applied(
    session, lab, monkeypatch
):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    monkeypatch.setattr(adhoc_apply, "folder_lease", lambda _source: _null_context())
    _apply(session, run_id, undo=False)
    (lab["repo"] / "test_app.py").write_text("# my edit\n")

    _apply(session, run_id, undo=True)

    record = AdhocService(session).run_record(run_id)
    assert record["apply_state"] == "applied"
    assert "changed since" in record["apply_error"]
    assert (lab["repo"] / "test_app.py").read_text() == "# my edit\n"


def test_the_runner_writes_the_patch_against_the_recorded_sha(
    session, lab, monkeypatch
):
    """bench_run.write_adhoc_change_patch: the path real runs take."""
    import sys

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    sys.modules.pop("bench_run", None)
    bench_run = __import__("bench_run")
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    run_dir = lab["settings"].data_runs_dir / run_id
    workspace = run_dir / "workspace"
    provisioned = _git(workspace, "rev-parse", "HEAD~0")
    (run_dir / "provisioned_commit.txt").write_text(provisioned + "\n")
    (run_dir / "case_spec.json").write_text(
        json.dumps({"target": {"commit": task.base_commit}})
    )
    # The agent amends the provisioning commit with its own work.
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "--amend", "--no-edit")

    bench_run.write_adhoc_change_patch(run_dir, workspace, run_id)

    patch = (run_dir / "change.patch").read_bytes()
    assert b"items or []" in patch
    assert b"setup.log" not in patch


def test_a_malformed_task_id_is_refused_before_any_path_is_built(session, lab):
    for bad in ("../../etc", "adhoc-x/../../y", "tetris-v1", "adhoc-"):
        with pytest.raises(adhoc_service.AdhocNotFoundError):
            AdhocService(session).get_task(bad)
        with pytest.raises(adhoc_service.AdhocNotFoundError):
            AdhocService(session).delete_task(bad)


def test_a_new_task_carries_the_generic_rubric_for_the_judge(session, lab):
    task = _task(session, lab)

    case = session.exec(select(Case).where(Case.case_id == task.case_id)).one()

    assert case.scoring_rubric == adhoc_service.ADHOC_RUBRIC


def test_asking_for_the_judge_readies_a_finished_run(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)

    record = AdhocService(session).request_judge(run_id)

    assert record["state"] == "awaiting_manual_score"
    assert record["judge_score"] is None


def test_the_judge_is_refused_while_the_run_is_live(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)

    with pytest.raises(AdhocConflictError, match="still going"):
        AdhocService(session).request_judge(run_id)


def test_a_judge_score_shows_on_the_record(session, lab):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()
    run.state = RunState.completed
    run.score = 8
    run.notes = "LLM-judged against the rubric (model): does what was asked."
    session.add(run)
    session.commit()

    record = AdhocService(session).run_record(run_id)

    assert record["judge_score"] == 8
    assert "does what was asked" in record["judge_notes"]


def test_an_applied_run_past_retention_keeps_only_its_patch(session, lab, monkeypatch):
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    AdhocService(session).finalize_run(run_id)
    monkeypatch.setattr(adhoc_apply, "folder_lease", lambda _source: _null_context())
    _apply(session, run_id, undo=False)
    row = session.exec(select(AdhocRun).where(AdhocRun.run_id == run_id)).one()
    row.created_at = row.created_at.replace(year=row.created_at.year - 1)
    session.add(row)
    session.commit()

    result = StorageService(session).purge("adhoc_expired")

    run_dir = lab["settings"].data_runs_dir / run_id
    assert result["runs"] == 1
    assert [p.name for p in run_dir.iterdir()] == ["change.patch"]
    # Undo still works on a purged applied run.
    _apply(session, run_id, undo=True)
    assert AdhocService(session).run_record(run_id)["apply_state"] == "undone"


def _config_of(session: Session, run_id: str) -> RunConfig:
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()
    return session.get(RunConfig, run.run_config_id)


def test_a_harness_level_becomes_the_runs_harness_condition(session, lab, monkeypatch):
    """repo = the repository alone; minimal/full = the lab's harness projected."""
    from harness_lab_app import skills_catalog

    monkeypatch.setattr(
        skills_catalog, "profile_disabled", lambda _name: ["brainstorming", "docs"]
    )
    task = _task(session, lab)
    service = AdhocService(session)

    runs = {
        level: service.launch_run(task.task_id, harness="claude", harness_level=level)
        for level in ("repo", "minimal", "full")
    }

    repo, minimal, full = (_config_of(session, runs[k]["run_id"]) for k in runs)
    assert repo.harness_source == HarnessSource.native
    assert repo.skills_disabled == []
    assert minimal.harness_source == HarnessSource.injected
    assert minimal.skills_disabled == ["brainstorming", "docs"]
    assert full.harness_source == HarnessSource.injected
    assert full.skills_disabled == []
    # The table compares like with like, so the level is on every record.
    assert [runs[k]["harness_level"] for k in runs] == ["repo", "minimal", "full"]
    # Every level starts from the same commit.
    assert {c.target_commit for c in (repo, minimal, full)} == {task.base_commit}


def test_a_trial_skill_rides_on_any_level_and_can_be_required(session, lab):
    task = _task(session, lab)
    service = AdhocService(session)

    offered = service.launch_run(task.task_id, harness="claude", trial_skill="tdd-lite")
    required = service.launch_run(
        task.task_id, harness="claude", trial_skill="tdd-lite", invoke_trial_skill=True
    )

    assert _config_of(session, offered["run_id"]).skills_extra == ["tdd-lite"]
    assert _config_of(session, offered["run_id"]).skills_invoke == []
    assert required["trial_skills"] == ["tdd-lite"]
    assert required["skills_invoked"] == ["tdd-lite"]


def test_an_unknown_level_or_an_empty_required_skill_is_refused(session, lab):
    task = _task(session, lab)
    service = AdhocService(session)

    with pytest.raises(AdhocError, match="harness_level"):
        service.launch_run(task.task_id, harness="claude", harness_level="huge")
    with pytest.raises(AdhocError, match="skill to try"):
        service.launch_run(task.task_id, harness="claude", invoke_trial_skill=True)
    assert service.task_detail(task.task_id)["runs"] == []


def test_the_runner_leaves_the_projected_harness_out_of_the_change(
    session, lab, monkeypatch
):
    """bench_run reads harness_paths.json: the lab's skills never reach the repo."""
    import sys

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    sys.modules.pop("bench_run", None)
    bench_run = __import__("bench_run")
    task = _task(session, lab)
    run_id = _launch(session, task)
    _simulate_run(session, lab, run_id)
    run_dir = lab["settings"].data_runs_dir / run_id
    workspace = run_dir / "workspace"
    (run_dir / "provisioned_commit.txt").write_text(
        _git(workspace, "rev-parse", "HEAD") + "\n"
    )
    (run_dir / "case_spec.json").write_text(
        json.dumps({"target": {"commit": task.base_commit}})
    )
    # A skill the agent wrote into the projected view after the baseline.
    skill = workspace / ".claude" / "skills" / "verify_changes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# edited by the agent\n")
    (run_dir / bench_run.HARNESS_PATHS_FILE).write_text(
        json.dumps([".claude/skills/verify_changes"])
    )

    bench_run.write_adhoc_change_patch(run_dir, workspace, run_id)

    patch = (run_dir / "change.patch").read_bytes()
    assert b"items or []" in patch
    assert b"verify_changes" not in patch


def _second_repo(lab, name: str) -> None:
    repo = lab["repo"].parent / name
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "main.py").write_text("print('hi')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


def test_deleting_unkept_tasks_spares_kept_and_in_use_ones(session, lab, monkeypatch):
    """Kept tasks stay; so does one whose change is applied (Undo needs it)."""
    for name in ("blog", "api"):
        _second_repo(lab, name)
    service = AdhocService(session)
    kept = service.create_task("shop", "Keep me.", title="kept")
    service.update_task(kept.task_id, keep=True)
    throwaway = service.create_task("blog", "Throw me away.")
    throwaway_run = _launch(session, throwaway)
    _simulate_run(session, lab, throwaway_run)
    service.finalize_run(throwaway_run)
    applied = service.create_task("api", "Applied change.")
    applied_run = _launch(session, applied)
    _simulate_run(session, lab, applied_run)
    row = service.get_adhoc_run(applied_run)
    row.apply_state = "applied"
    session.add(row)
    session.commit()

    result = service.delete_unkept()

    assert result["deleted"] == [throwaway.task_id]
    assert result["bytes_freed"] > 0
    assert [item["task_id"] for item in result["skipped"]] == [applied.task_id]
    assert "applied to your folder" in result["skipped"][0]["reason"]
    remaining = {task["task_id"] for task in service.list_tasks()}
    assert remaining == {kept.task_id, applied.task_id}
    assert not (lab["settings"].checkouts_dir / throwaway.task_id).exists()
    assert not (lab["settings"].data_runs_dir / throwaway_run).exists()
    assert service.get_task(kept.task_id).keep is True


def test_keep_is_on_the_list_and_toggles_back(session, lab):
    task = _task(session, lab)
    service = AdhocService(session)

    assert service.list_tasks()[0]["keep"] is False
    service.update_task(task.task_id, keep=True)
    assert service.list_tasks()[0]["keep"] is True
    service.update_task(task.task_id, keep=False)
    assert service.list_tasks()[0]["keep"] is False
    # Untouched by a title edit.
    service.update_task(task.task_id, title="renamed")
    assert service.get_task(task.task_id).keep is False


def test_the_delete_unkept_route_is_on_the_router():
    from harness_lab_app.adhoc_api import adhoc_router

    routes = {
        (getattr(route, "path", ""), method)
        for route in adhoc_router.routes
        for method in getattr(route, "methods", ())
    }
    assert ("/api/adhoc/tasks/delete-unkept", "POST") in routes
    assert ("/api/adhoc/tasks/{task_id}", "DELETE") in routes


# ---- ADR-0100: a skill to try from the start, its version, and PR reviews ----


def _stage(session: Session, name: str, content_hash: str) -> None:
    from harness_lab_app.models import StagedSkill

    session.add(StagedSkill(name=name, content="x", content_hash=content_hash))
    session.commit()


def test_a_task_carries_the_skill_to_try_picked_at_creation(session, lab):
    _stage(session, "review_pr", "aaa111")

    task = AdhocService(session).create_task(
        "shop", "Review it.", trial_skill="review_pr", invoke_trial_skill=True
    )

    body = AdhocService._task_dict(task)
    assert body["trial_skill"] == "review_pr"
    assert body["invoke_trial_skill"] is True


def test_a_task_refuses_a_skill_that_is_not_staged(session, lab):
    with pytest.raises(AdhocError, match="No staged skill"):
        AdhocService(session).create_task("shop", "Do it.", trial_skill="ghost")
    with pytest.raises(AdhocError, match="skill to try"):
        AdhocService(session).create_task("shop", "Do it.", invoke_trial_skill=True)


def test_each_run_records_the_version_of_the_skill_it_tried(session, lab):
    from harness_lab_app.models import StagedSkill

    _stage(session, "review_pr", "v1hash")
    task = _task(session, lab)
    service = AdhocService(session)
    first = service.launch_run(task.task_id, harness="claude", trial_skill="review_pr")
    skill = session.exec(
        select(StagedSkill).where(StagedSkill.name == "review_pr")
    ).one()
    skill.content_hash = "v2hash"  # the operator edited the staged copy
    session.add(skill)
    session.commit()
    second = service.launch_run(task.task_id, harness="claude", trial_skill="review_pr")
    plain = service.launch_run(task.task_id, harness="claude")

    assert first["trial_skill_hashes"] == {"review_pr": "v1hash"}
    assert second["trial_skill_hashes"] == {"review_pr": "v2hash"}
    assert plain["trial_skill_hashes"] == {}


def test_a_run_keeps_the_skill_version_it_actually_ran(session, lab):
    """Edited while queued: the run's own metadata.json wins over launch time."""
    _stage(session, "review_pr", "v1hash")
    task = _task(session, lab)
    service = AdhocService(session)
    run_id = service.launch_run(
        task.task_id, harness="claude", trial_skill="review_pr"
    )["run_id"]
    run_dir = lab["settings"].data_runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(
        json.dumps({"skills_extra_hashes": {"review_pr": "v2hash"}})
    )
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()
    run.state = RunState.completed
    session.add(run)
    session.commit()

    service.capture_metrics(run_id)

    assert service.run_record(run_id)["trial_skill_hashes"] == {"review_pr": "v2hash"}


def _pr_branch(repo: Path) -> tuple[str, str]:
    """A main that moved on after a PR branch left it; (merge-base, PR head)."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr-7")
    (repo / "app.py").write_text("def total(items):\n    return sum(items or [])\n")
    _git(repo, "commit", "-q", "-am", "handle None")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    (repo / "README.md").write_text("# shop\nmoved on\n")
    _git(repo, "commit", "-q", "-am", "main moved")
    return base, head


def test_a_pr_review_pins_the_pr_head_and_the_range_by_sha(session, lab):
    merge_base, head = _pr_branch(lab["repo"])
    _stage(session, "review_pr", "h")

    task = AdhocService(session).create_pr_review_task(
        "shop",
        7,
        "pr-7",
        base_ref="main",
        repo="acme/shop",
        pr_title="Handle a missing cart",
        pr_body="Fixes #3.",
        trial_skill="review_pr",
        invoke_trial_skill=True,
    )

    assert task.base_commit == head
    assert task.pr_ref == "acme/shop#7"
    assert task.pr_base_ref == merge_base
    # The run's workspace has no origin/* refs, so the range must be a SHA.
    assert f"`{merge_base}...HEAD`" in task.prompt
    assert "Handle a missing cart" in task.prompt and "Fixes #3." in task.prompt
    assert "review.json" in task.prompt
    assert task.trial_skill == "review_pr" and task.invoke_trial_skill
    # The operator's folder is only read: still on main, nothing checked out.
    assert _git(lab["repo"], "symbolic-ref", "--short", "HEAD") == "main"
    copy = lab["settings"].checkouts_dir / task.task_id
    assert _git(copy, "rev-parse", "HEAD") == head
    assert _git(copy, "cat-file", "-t", merge_base) == "commit"


def test_a_pr_review_task_does_not_warn_that_the_folder_moved(session, lab):
    _pr_branch(lab["repo"])
    task = AdhocService(session).create_pr_review_task(
        "shop", 7, "pr-7", base_ref="main"
    )

    assert (
        AdhocService(session).task_detail(task.task_id)["source"]["head_moved_by"]
        is None
    )


def test_a_pr_review_explains_a_head_or_base_it_cannot_find(session, lab):
    service = AdhocService(session)
    with pytest.raises(AdhocError, match=r"'no-such-branch'.*\+pull/7/head:pr-7"):
        service.create_pr_review_task("shop", 7, "no-such-branch", base_ref="main")
    # A missing base is named as the base, not blamed on the PR head.
    with pytest.raises(AdhocError, match=r"'origin/main'.*base branch"):
        service.create_pr_review_task("shop", 7, "main", base_ref="origin/main")
    with pytest.raises(AdhocError, match="depth"):
        service.create_pr_review_task("shop", 7, "main", depth="huge")
    with pytest.raises(AdhocError, match="branch or commit"):
        service.create_pr_review_task("shop", 7, "  ")


def test_the_repo_slug_reads_ssh_host_aliases(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "git@github-work:Acme-Corp/ivy-backend.git")

    assert adhoc_service._repo_slug(repo) == "Acme-Corp/ivy-backend"
