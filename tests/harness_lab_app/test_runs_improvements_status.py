"""The Runs list flags each run's improvement-synthesis status (yes/no/pending)."""

from __future__ import annotations

import json

import pytest

from harness_lab_app import api as api_module
from harness_lab_app.api import _improvements_status, create_app
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    class _Settings:
        data_runs_dir = tmp_path

    monkeypatch.setattr(api_module, "get_settings", lambda: _Settings())
    return tmp_path


def _write(runs_dir, run_id: str, proposals: list[dict]) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "improvements.json").write_text(json.dumps({"proposals": proposals}))


def test_improvements_status_reads_the_artifact(runs_dir):
    _write(runs_dir, "has-some", [{"title": "x"}])
    _write(runs_dir, "has-none", [])
    assert _improvements_status(runs_dir, "has-some") == "yes"
    assert _improvements_status(runs_dir, "has-none") == "no"
    assert _improvements_status(runs_dir, "never-run") == "pending"


def test_improvements_status_degrades_on_bad_json(runs_dir):
    run_dir = runs_dir / "broken"
    run_dir.mkdir(parents=True)
    (run_dir / "improvements.json").write_text("{ not json")
    assert _improvements_status(runs_dir, "broken") == "pending"


def _seed_run(session, run_id: str) -> None:
    RunService(session).create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model="stub-executor",
            ),
        )
    )


def _endpoint(app, path: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_list_runs_carries_improvements_field(session, runs_dir):
    _seed_run(session, "run-yes")
    _seed_run(session, "run-pending")
    _write(runs_dir, "run-yes", [{"title": "x"}])
    listing = _endpoint(create_app(), "/api/runs")(session=session)
    by_id = {row["run_id"]: row["improvements"] for row in listing}
    assert by_id["run-yes"] == "yes"
    assert by_id["run-pending"] == "pending"
