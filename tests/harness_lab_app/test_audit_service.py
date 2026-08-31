from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.audit_service import AuditService


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_get_audit_none_when_absent():
    with _session() as session:
        assert AuditService(session).get_audit("nope") is None


def test_mark_running_creates_row():
    with _session() as session:
        service = AuditService(session)
        audit = service.mark_running("run-1", "gpt-5.5")
        assert audit.status == "running"
        assert audit.auditor_model == "gpt-5.5"
        assert service.get_audit("run-1") is not None


def test_store_result_sets_done_and_markdown():
    with _session() as session:
        service = AuditService(session)
        service.mark_running("run-2", "gpt-5.5")
        audit = service.store_result("run-2", "## Uso de skills\nOK")
        assert audit.status == "done"
        assert "Uso de skills" in audit.markdown
        assert audit.error is None
        # No findings passed → column keeps its default empty dict.
        assert audit.findings_json == {}


def test_store_result_persists_findings():
    findings = {
        "items": [
            {
                "name": "verify_changes",
                "kind": "skill",
                "used_evidence": "make check en step 3",
                "verdict": "useful",
                "note": "Se corrió el gate",
            }
        ]
    }
    with _session() as session:
        service = AuditService(session)
        service.mark_running("run-f", "gpt-5.5")
        audit = service.store_result("run-f", "body", findings=findings)
        assert audit.findings_json["items"][0]["verdict"] == "useful"


def test_read_audit_findings_tolerates_absence_and_garbage(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness_lab_app import audit_service as module

    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    with _session() as session:
        service = AuditService(session)
        # Absent file → None (store_result then keeps the default).
        assert service.read_audit_findings("run-x") is None
        run_dir = tmp_path / "run-x"
        run_dir.mkdir()
        (run_dir / "audit_findings.json").write_text("not json {")
        garbage = service.read_audit_findings("run-x")
        assert garbage is not None
        assert garbage["items"] == []
        assert "parse_error" in garbage
        (run_dir / "audit_findings.json").write_text('{"items": [{"name": "a"}]}')
        assert service.read_audit_findings("run-x") == {"items": [{"name": "a"}]}


def test_store_error_sets_failed():
    with _session() as session:
        service = AuditService(session)
        service.mark_running("run-3")
        audit = service.store_error("run-3", "gateway 500")
        assert audit.status == "failed"
        assert audit.error == "gateway 500"


def test_mark_running_is_idempotent_upsert():
    with _session() as session:
        service = AuditService(session)
        first = service.mark_running("run-4")
        service.store_result("run-4", "body")
        # Re-running an audit reuses the same row (unique run_id), resets status.
        again = service.mark_running("run-4", "claude")
        assert first.id == again.id
        assert again.status == "running"
        assert again.auditor_model == "claude"


def test_store_result_persists_the_governance_surface():
    """What the audit judged against travels with the verdict.

    A stored `skill_adherence` of 0.4 is unreadable without knowing whose rules
    the auditor held the run to.
    """
    governance = {
        "source": "target_workspace",
        "harness_source": "native",
        "rules_file": "AGENTS.md",
        "files": ["AGENTS.md"],
        "file_count": 1,
        "fingerprint": "abc123",
        "truncated": False,
        "warning": "",
    }
    with _session() as session:
        service = AuditService(session)
        service.mark_running("run-g")
        audit = service.store_result("run-g", "body", governance=governance)
        assert audit.governance_json["harness_source"] == "native"
        assert audit.governance_json["fingerprint"] == "abc123"
        # An audit stored without one keeps the empty default rather than
        # inheriting a neighbouring run's surface.
        service.mark_running("run-h")
        assert service.store_result("run-h", "body").governance_json == {}


def test_read_audit_governance_absent_is_none(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness_lab_app import audit_service as module

    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    with _session() as session:
        service = AuditService(session)
        # An audit from before the surface was recorded genuinely does not know
        # what it judged; None says that, {} would not.
        assert service.read_audit_governance("run-y") is None
        run_dir = tmp_path / "run-y"
        run_dir.mkdir()
        (run_dir / "audit_governance.json").write_text("not json {")
        assert service.read_audit_governance("run-y") is None
        (run_dir / "audit_governance.json").write_text('{"source": "lab_root"}')
        assert service.read_audit_governance("run-y") == {"source": "lab_root"}
