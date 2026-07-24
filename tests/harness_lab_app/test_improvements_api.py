"""Improvements worklist API: save, list, delete, group-issue (no LLM)."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness_lab_app import improvement_service as service_module
from harness_lab_app.api import create_app
from harness_lab_app.improvement_service import ImprovementService
from harness_lab_app.schemas import GroupIssueRequest


def _endpoint(app, path: str, method: str | None = None):
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is not None and method not in getattr(route, "methods", set()):
            continue
        return route.endpoint
    raise AssertionError(f"route not found: {path} ({method})")


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    class _Settings:
        data_runs_dir = tmp_path

    from harness_lab_app import api as api_module

    monkeypatch.setattr(service_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(api_module, "get_settings", lambda: _Settings())
    return tmp_path


def _write_improvements(runs_dir, run_id: str, titles: list[str]) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": run_id,
        "context": {"case": "c1", "harness": "claude", "executor_model": "m"},
        "generator_model": "g",
        "proposals": [
            {
                "title": title,
                "body": "b",
                "kind": "skill",
                "target": "t",
                "repo_hint": "template",
                "repo": "marcosdh1987/ml-python-base",
                "created_issue_url": None,
            }
            for title in titles
        ],
    }
    (run_dir / "improvements.json").write_text(json.dumps(doc))


def test_save_improvements_404_when_absent(session, runs_dir):
    endpoint = _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")
    with pytest.raises(HTTPException) as exc:
        endpoint("ghost", session)
    assert exc.value.status_code == 404


def test_save_improvements_persists_rows(session, runs_dir):
    _write_improvements(runs_dir, "run-a", ["A", "B"])
    endpoint = _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")
    result = endpoint("run-a", session)
    assert result["saved"] == 2
    listing = _endpoint(create_app(), "/api/improvements")
    rows = listing(session=session)
    assert {row.title for row in rows} == {"A", "B"}


def test_list_saved_proposals_filters(session, runs_dir):
    _write_improvements(runs_dir, "run-a", ["A"])
    _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")("run-a", session)
    listing = _endpoint(create_app(), "/api/improvements")
    assert listing(kind="skill", session=session)
    assert listing(kind="process", session=session) == []


def test_delete_saved_proposal(session, runs_dir):
    _write_improvements(runs_dir, "run-a", ["A"])
    _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")("run-a", session)
    row = ImprovementService(session).list_proposals()[0]
    delete = _endpoint(create_app(), "/api/improvements/{proposal_id}", "DELETE")
    delete(row.id, session)
    assert ImprovementService(session).list_proposals() == []
    with pytest.raises(HTTPException) as exc:
        delete(row.id, session)
    assert exc.value.status_code == 404


def test_create_group_issue_400_on_unknown_ids(session, runs_dir):
    endpoint = _endpoint(create_app(), "/api/improvements/group-issue")
    with pytest.raises(HTTPException) as exc:
        endpoint(GroupIssueRequest(proposal_ids=[123]), session)
    assert exc.value.status_code == 400


class _FakeForm:
    title = "[HEP] Combined proposal"

    def to_dict(self) -> dict:
        return {"title": self.title, "url": "https://github.com/x", "fields": []}


def test_create_group_issue_records_and_marks_issued(session, runs_dir, monkeypatch):
    from harness_lab_app import api as api_module

    _write_improvements(runs_dir, "run-a", ["A"])
    _write_improvements(runs_dir, "run-b", ["B"])
    save = _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")
    save("run-a", session)
    save("run-b", session)
    ids = [row.id for row in ImprovementService(session).list_proposals()]

    monkeypatch.setattr(api_module, "_prepare_gateway_env", lambda: None)
    monkeypatch.setattr(
        api_module.drafting,
        "synthesize_group_issue",
        lambda *_args, **_kwargs: ("# combined", _FakeForm()),
    )
    endpoint = _endpoint(create_app(), "/api/improvements/group-issue")
    result = endpoint(GroupIssueRequest(proposal_ids=ids), session)
    assert result["markdown"] == "# combined"
    assert sorted(result["source_run_ids"]) == ["run-a", "run-b"]
    assert sorted(result["proposal_ids"]) == sorted(ids)
    for row in ImprovementService(session).list_proposals():
        assert row.status == "issued"
        assert row.group_issue_id == result["group_issue_id"]
    issues = _endpoint(create_app(), "/api/improvements/group-issues")(session=session)
    assert len(issues) == 1


def test_create_group_issue_maps_sanitization_error_to_422(
    session, runs_dir, monkeypatch
):
    from harness_lab_app import api as api_module
    from harness_lab_app.harness_lifecycle import sanitizer

    _write_improvements(runs_dir, "run-a", ["A"])
    _endpoint(create_app(), "/api/runs/{run_id}/improvements/save")("run-a", session)
    ids = [row.id for row in ImprovementService(session).list_proposals()]

    def _leak(*args, **kwargs):
        raise sanitizer.SanitizationError("combined issue echoed a private run id")

    monkeypatch.setattr(api_module, "_prepare_gateway_env", lambda: None)
    monkeypatch.setattr(api_module.drafting, "synthesize_group_issue", _leak)
    endpoint = _endpoint(create_app(), "/api/improvements/group-issue")
    with pytest.raises(HTTPException) as exc:
        endpoint(GroupIssueRequest(proposal_ids=ids), session)
    assert exc.value.status_code == 422
    # The failed attempt must not mark anything issued.
    for row in ImprovementService(session).list_proposals():
        assert row.status == "saved"
