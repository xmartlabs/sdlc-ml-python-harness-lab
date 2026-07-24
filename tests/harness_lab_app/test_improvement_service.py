"""ImprovementService: idempotent saves, filters, and group-issue lifecycle."""

from __future__ import annotations

import json

import pytest

from harness_lab_app import improvement_service as service_module
from harness_lab_app.improvement_service import ImprovementError, ImprovementService


def _write_improvements(tmp_path, run_id: str, proposals: list[dict]) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": run_id,
        "context": {
            "case": "tetris-v1",
            "harness": "claude",
            "executor_model": "stub-executor",
        },
        "generator_model": "stub-generator",
        "proposals": proposals,
    }
    (run_dir / "improvements.json").write_text(json.dumps(doc))


def _proposal(title: str, **overrides) -> dict:
    base = {
        "title": title,
        "body": "Problem seen in run dbg-K-001.",
        "kind": "skill",
        "target": "systematic_debugging",
        "repo_hint": "template",
        "repo": "marcosdh1987/ml-python-base",
        "created_issue_url": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def service(session, tmp_path, monkeypatch) -> ImprovementService:
    class _Settings:
        data_runs_dir = tmp_path

    monkeypatch.setattr(service_module, "get_settings", lambda: _Settings())
    return ImprovementService(session)


def test_save_from_run_snapshots_proposals_with_context(service, tmp_path):
    _write_improvements(
        tmp_path, "run-a", [_proposal("Add retry budget"), _proposal("Fix docs")]
    )
    result = service.save_from_run("run-a")
    assert result == {"run_id": "run-a", "saved": 2, "updated": 0, "total": 2}
    rows = service.list_proposals()
    assert {row.title for row in rows} == {"Add retry budget", "Fix docs"}
    row = rows[0]
    assert row.case == "tetris-v1"
    assert row.harness == "claude"
    assert row.executor_model == "stub-executor"
    assert row.generator_model == "stub-generator"
    assert row.status == "saved"


def test_save_from_run_is_idempotent(service, tmp_path):
    _write_improvements(tmp_path, "run-a", [_proposal("Add retry budget")])
    service.save_from_run("run-a")
    result = service.save_from_run("run-a")
    assert result["saved"] == 0
    assert result["updated"] == 1
    assert result["total"] == 1


def test_save_from_run_refreshes_body_but_preserves_status(service, tmp_path):
    _write_improvements(tmp_path, "run-a", [_proposal("Add retry budget")])
    service.save_from_run("run-a")
    row = service.list_proposals()[0]
    service.record_group_issue(
        [row],
        repository="marcosdh1987/ml-python-base",
        title="[HEP] combined",
        markdown="# issue",
        form_json={"title": "[HEP] combined"},
        model="stub",
    )
    _write_improvements(
        tmp_path, "run-a", [_proposal("Add retry budget", body="Edited body.")]
    )
    result = service.save_from_run("run-a")
    assert result["updated"] == 1
    row = service.list_proposals()[0]
    assert row.body == "Edited body."
    assert row.status == "issued"
    assert row.group_issue_id is not None


def test_save_from_run_raises_when_file_missing(service):
    with pytest.raises(FileNotFoundError):
        service.save_from_run("ghost-run")


def test_list_proposals_filters(service, tmp_path):
    _write_improvements(
        tmp_path,
        "run-a",
        [
            _proposal("Skill fix"),
            _proposal(
                "Process fix",
                kind="process",
                repo="marcosdh1987/harness-engineering-guide",
            ),
        ],
    )
    service.save_from_run("run-a")
    assert len(service.list_proposals(kind="process")) == 1
    assert len(service.list_proposals(repo="marcosdh1987/ml-python-base")) == 1
    assert len(service.list_proposals(case="tetris-v1")) == 2
    assert len(service.list_proposals(status="issued")) == 0


def test_record_group_issue_marks_proposals_issued(service, tmp_path):
    _write_improvements(tmp_path, "run-a", [_proposal("A"), _proposal("B")])
    _write_improvements(tmp_path, "run-b", [_proposal("C")])
    service.save_from_run("run-a")
    service.save_from_run("run-b")
    rows = service.list_proposals()
    issue = service.record_group_issue(
        rows,
        repository="marcosdh1987/ml-python-base",
        title="[HEP] combined",
        markdown="# issue",
        form_json={"title": "[HEP] combined"},
        model="stub",
    )
    assert sorted(issue.source_run_ids) == ["run-a", "run-b"]
    assert len(issue.proposal_ids) == 3
    for row in service.list_proposals():
        assert row.status == "issued"
        assert row.group_issue_id == issue.id
    assert [saved.id for saved in service.list_group_issues()] == [issue.id]


def test_get_proposals_raises_on_unknown_id(service, tmp_path):
    _write_improvements(tmp_path, "run-a", [_proposal("A")])
    service.save_from_run("run-a")
    known = service.list_proposals()[0].id
    with pytest.raises(ImprovementError, match="999"):
        service.get_proposals([known, 999])


def test_delete_proposal(service, tmp_path):
    _write_improvements(tmp_path, "run-a", [_proposal("A")])
    service.save_from_run("run-a")
    proposal_id = service.list_proposals()[0].id
    service.delete_proposal(proposal_id)
    assert service.list_proposals() == []
    with pytest.raises(ImprovementError):
        service.delete_proposal(proposal_id)
