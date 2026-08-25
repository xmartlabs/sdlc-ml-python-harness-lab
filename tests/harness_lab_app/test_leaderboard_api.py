"""Leaderboard rows: per-run condition + outcome, plus the official reference."""

from __future__ import annotations

import json

import pytest

from harness_lab_app import leaderboard_service as service_module
from harness_lab_app.api import create_app
from harness_lab_app.leaderboard_service import LeaderboardService, harness_version
from harness_lab_app.models import Case, CaseType, RunState, Validation
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the service at a temp data layout (data/runs + data/reference)."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    class _Settings:
        data_runs_dir = runs_dir

    monkeypatch.setattr(service_module, "get_settings", lambda: _Settings())
    return tmp_path


def _seed_run(
    session,
    run_id: str,
    *,
    case_id: str = "tetris-v1",
    harness: str = "opencode",
    executor_model: str = "gateway/qwen",
    prompt_variant: str = "swe_harness",
    state: RunState = RunState.completed,
    score: int | None = None,
):
    run = RunService(session).create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id=case_id,
                prompt_variant=prompt_variant,
                harness=harness,
                executor_model=executor_model,
            ),
        )
    )
    run.state = state
    run.score = score
    session.add(run)
    session.commit()
    return run


def _write_metadata(data_dir, run_id: str, meta: dict) -> None:
    run_dir = data_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(json.dumps(meta))


def _endpoint(app, path: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_harness_version_precedence():
    assert harness_version({"template_ref": "v0.4.0"}) == "v0.4.0"
    assert (
        harness_version(
            {"harness_fingerprint": {"harness_hash": "sha256:7cc5a8e0ffff"}}
        )
        == "7cc5a8e0"
    )
    assert harness_version({"lab_git_commit": "284561c50afe"}) == "284561c5"
    assert harness_version({}) == ""


def test_rows_carry_condition_and_outcome(session, data_dir):
    session.add(Case(case_id="swe-1", name="swe", type=CaseType.swe_bench_instance))
    session.add(Case(case_id="tetris-v1", name="tetris", type=CaseType.multi_step))
    _seed_run(session, "swe-ok", case_id="swe-1", harness="claude", score=10)
    _seed_run(session, "swe-fail", case_id="swe-1", score=0)
    _seed_run(session, "manual", case_id="tetris-v1", score=8)
    _seed_run(session, "still-running", state=RunState.running)
    session.add(
        Validation(
            run_id="swe-ok",
            objective={"passed": True},
            cost={"cost_usd": 0.12, "total_tokens": 1000},
            effort={"steps": 3},
            composite=9.1,
        )
    )
    session.add(Validation(run_id="swe-fail", objective={"passed": False}))
    session.commit()
    _write_metadata(data_dir, "swe-ok", {"template_ref": "v0.4.0"})

    rows = {row["run_id"]: row for row in LeaderboardService(session).list_rows()}
    assert "still-running" not in rows  # non-terminal runs are not attempts
    ok = rows["swe-ok"]
    assert ok["resolved"] is True
    assert ok["case_type"] == "swe_bench_instance"
    assert ok["harness"] == "claude"
    assert ok["harness_version"] == "v0.4.0"
    assert ok["cost_usd"] == 0.12 and ok["steps"] == 3 and ok["composite"] == 9.1
    assert rows["swe-fail"]["resolved"] is False
    # Manual-score case: no objective validation → resolved stays None.
    assert rows["manual"]["resolved"] is None
    assert rows["manual"]["score"] == 8


def test_metadata_read_is_best_effort(session, data_dir):
    _seed_run(session, "no-meta")
    run_dir = data_dir / "runs" / "bad-meta"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text("{ not json")
    _seed_run(session, "bad-meta")
    rows = {row["run_id"]: row for row in LeaderboardService(session).list_rows()}
    assert rows["no-meta"]["harness_version"] == ""
    assert rows["bad-meta"]["harness_version"] == ""


def test_reference_endpoint_serves_curated_file(session, data_dir):
    reference = _endpoint(create_app(), "/api/leaderboard/reference")
    assert reference(session=session) == []  # missing file degrades to []
    ref_dir = data_dir / "reference"
    ref_dir.mkdir()
    (ref_dir / "swebench_official.json").write_text(
        json.dumps([{"agent": "mini-SWE-agent", "model": "x", "pct_resolved": 60.0}])
    )
    assert reference(session=session)[0]["agent"] == "mini-SWE-agent"


def test_leaderboard_endpoint_returns_rows(session, data_dir):
    _seed_run(session, "one", score=10)
    rows = _endpoint(create_app(), "/api/leaderboard")(session=session)
    assert rows[0]["run_id"] == "one"


def test_runs_the_lab_broke_are_not_counted_as_attempts(session, data_dir):
    """K-043/044/045: the runner image was missing, so nothing was measured.

    All three died in under a second having asked the model nothing, yet each
    counted as an attempt on the leaderboard. An attempt total that includes
    runs which never started misrepresents every condition it touches.
    """
    _seed_run(session, "real-attempt", state=RunState.failed, score=None)
    for run_id in ("no-image-1", "no-image-2"):
        run = _seed_run(session, run_id, state=RunState.failed)
        run.failure_reason = (
            'Executor error: RuntimeError("Runner image '
            "'harness-lab-runner:latest' was not found locally.\")"
        )
        session.add(run)
    restarted = _seed_run(session, "worker-restart", state=RunState.failed)
    restarted.failure_reason = (
        "Worker restarted while the run was in flight; retry to resume"
    )
    session.add(restarted)
    # A genuine agent failure stays: a crash is a result.
    crashed = _seed_run(session, "agent-crash", state=RunState.failed)
    crashed.failure_reason = "SyntaxError: closing parenthesis does not match"
    session.add(crashed)
    session.commit()

    run_ids = {row["run_id"] for row in LeaderboardService(session).list_rows()}

    assert run_ids == {"real-attempt", "agent-crash"}


def test_chain_depth_counts_the_links_and_stops_at_an_unknown_parent():
    parents = {"a": None, "a-C-001": "a", "a-C-001-C-001": "a-C-001", "orphan": "gone"}
    assert service_module.chain_depth("a", parents) == 0
    assert service_module.chain_depth("a-C-001", parents) == 1
    assert service_module.chain_depth("a-C-001-C-001", parents) == 2
    # A force-deleted parent: report the one link we can see, not a guess.
    assert service_module.chain_depth("orphan", parents) == 1


def test_chain_depth_survives_a_cycle():
    parents = {"a": "b", "b": "a"}
    assert service_module.chain_depth("a", parents) == 1


def test_rows_expose_lineage_so_continuations_can_be_excluded(session, data_dir):
    """A continued run was steered by a human; the dashboard must be able to tell."""
    session.add(Case(case_id="tetris-v1", name="tetris", type=CaseType.multi_step))
    parent = _seed_run(session, "tetris-v1-K-900", score=6)
    child = _seed_run(session, "tetris-v1-K-900-C-001", score=9)
    child.parent_run_id = parent.run_id
    session.add(child)
    session.commit()

    rows = {row["run_id"]: row for row in LeaderboardService(session).list_rows()}

    assert rows["tetris-v1-K-900"]["chain_depth"] == 0
    assert rows["tetris-v1-K-900"]["parent_run_id"] is None
    assert rows["tetris-v1-K-900-C-001"]["chain_depth"] == 1
    assert rows["tetris-v1-K-900-C-001"]["parent_run_id"] == "tetris-v1-K-900"
