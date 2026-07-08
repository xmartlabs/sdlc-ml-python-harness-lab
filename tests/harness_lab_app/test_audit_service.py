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
