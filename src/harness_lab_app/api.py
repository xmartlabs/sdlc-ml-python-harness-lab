from __future__ import annotations

import contextlib

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from harness_lab_app.artifact_service import ArtifactService
from harness_lab_app.attribution_service import AttributionService
from harness_lab_app.audit_service import AuditService
from harness_lab_app.case_service import CaseService
from harness_lab_app.celery_app import (
    enqueue_audit,
    enqueue_container_cleanup,
    enqueue_run,
    enqueue_validation,
)
from harness_lab_app.config import get_settings
from harness_lab_app.db import get_engine, get_session, init_db
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.schemas import CaseCreate, RunConfigCreate, RunCreate
from harness_lab_app.validation_service import ValidationService


class LoginRequest(BaseModel):
    username: str
    password: str


class ScoreRequest(BaseModel):
    score: int
    interventions: int
    notes: str = ""
    rubric: dict[str, int] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic Harness Lab")

    @app.on_event("startup")
    def startup() -> None:
        init_db()
        # Ensure the Tetris pilot case always exists on a fresh DB so the run
        # form never falls back to a non-existent "default" prompt variant.
        # Seeding must never block startup.
        with contextlib.suppress(Exception), Session(get_engine()) as session:
            CaseService(session).seed_tetris_case()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(data: LoginRequest) -> dict[str, bool]:
        settings = get_settings()
        if (
            data.username != settings.local_username
            or data.password != settings.local_password
        ):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    def logout() -> dict[str, bool]:
        return {"authenticated": False}

    @app.get("/api/cases")
    def list_cases(session: Session = Depends(get_session)):
        return CaseService(session).list_cases()

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, session: Session = Depends(get_session)):
        try:
            return CaseService(session).get_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases")
    def create_case(data: CaseCreate, session: Session = Depends(get_session)):
        return CaseService(session).create_case(data)

    @app.patch("/api/cases/{case_id}")
    def update_case(
        case_id: str, data: CaseCreate, session: Session = Depends(get_session)
    ):
        return CaseService(session).update_case(case_id, data)

    @app.delete("/api/cases/{case_id}")
    def delete_case(case_id: str, session: Session = Depends(get_session)):
        return CaseService(session).delete_case(case_id)

    @app.post("/api/cases/{case_id}/clone")
    def clone_case(
        case_id: str,
        new_case_id: str,
        new_name: str,
        session: Session = Depends(get_session),
    ):
        return CaseService(session).clone_case(case_id, new_case_id, new_name)

    @app.post("/api/cases/{case_id}/sync")
    def sync_case(case_id: str, session: Session = Depends(get_session)):
        return {"task_path": str(CaseService(session).sync_case_to_yaml(case_id))}

    @app.post("/api/cases/seed/tetris")
    def seed_tetris(session: Session = Depends(get_session)):
        return CaseService(session).seed_tetris_case()

    @app.get("/api/cases/{case_id}/run-ids")
    def list_case_run_ids(case_id: str, session: Session = Depends(get_session)):
        return {"run_ids": CaseService(session).list_case_run_ids(case_id)}

    @app.get("/api/runs")
    def list_runs(session: Session = Depends(get_session)):
        return RunService(session).list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, session: Session = Depends(get_session)):
        try:
            return RunService(session).get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/runs/preview")
    def preview_run(
        run_id: str, config: RunConfigCreate, session: Session = Depends(get_session)
    ):
        return {"command": RunService(session).preview_command(run_id, config)}

    @app.post("/api/runs")
    def create_run(data: RunCreate, session: Session = Depends(get_session)):
        try:
            return RunService(session, enqueue=enqueue_run).create_run(data)
        except RunPreflightError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RunAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/score")
    def score_run(
        run_id: str, data: ScoreRequest, session: Session = Depends(get_session)
    ):
        # Subjective dimension of the multidimensional score; recomputes the
        # composite against any objective validation already recorded.
        ValidationService(session).apply_subjective(
            run_id, data.score, data.interventions, data.rubric, data.notes
        )
        return RunService(session).score_run(
            run_id, data.score, data.interventions, data.notes
        )

    @app.get("/api/runs/{run_id}/validation")
    def get_validation(run_id: str, session: Session = Depends(get_session)):
        validation = ValidationService(session).get_validation(run_id)
        if validation is None:
            raise HTTPException(status_code=404, detail="No validation for this run")
        return validation

    @app.get("/api/runs/{run_id}/attribution")
    def get_attribution(run_id: str, session: Session = Depends(get_session)):
        attribution = AttributionService(session).get_attribution(run_id)
        if attribution is None:
            raise HTTPException(status_code=404, detail="No attribution for this run")
        return attribution

    @app.post("/api/runs/{run_id}/attribution/index")
    def index_attribution(run_id: str, session: Session = Depends(get_session)):
        # Attribution is pure log parsing (no container), so it runs inline.
        return AttributionService(session).index_run(run_id)

    @app.get("/api/runs/{run_id}/audit")
    def get_audit(run_id: str, session: Session = Depends(get_session)):
        audit = AuditService(session).get_audit(run_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="No audit for this run")
        return audit

    @app.post("/api/runs/{run_id}/audit")
    def run_audit(
        run_id: str,
        auditor_model: str | None = None,
        session: Session = Depends(get_session),
    ):
        # The audit is an LLM call (up to minutes), so it runs in the worker and
        # the frontend polls GET /audit. Mark it running now so the UI has state.
        try:
            RunService(session).get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        AuditService(session).mark_running(run_id, auditor_model or "")
        enqueue_audit(run_id, auditor_model)
        return {"status": "queued", "run_id": run_id}

    @app.post("/api/runs/{run_id}/validate")
    def validate_run(run_id: str, session: Session = Depends(get_session)):
        # Validation executes a container, so it must run in the worker (which
        # owns the docker socket), not in the API process. Enqueue and let the
        # frontend poll GET /validation.
        try:
            RunService(session).get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        enqueue_validation(run_id)
        return {"status": "queued", "run_id": run_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, session: Session = Depends(get_session)):
        return RunService(session).cancel_run(run_id)

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str, session: Session = Depends(get_session)):
        return RunService(session, enqueue=enqueue_run).retry_run(run_id)

    @app.post("/api/runs/{run_id}/clone")
    def clone_run(run_id: str, session: Session = Depends(get_session)):
        try:
            return RunService(session, enqueue=enqueue_run).clone_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete_run(
        run_id: str, force: bool = False, session: Session = Depends(get_session)
    ):
        try:
            RunService(session, enqueue_cleanup=enqueue_container_cleanup).delete_run(
                run_id, force=force
            )
        except RunDeleteConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/logs")
    def list_logs(
        run_id: str, ascending: bool = False, session: Session = Depends(get_session)
    ):
        return RunService(session).list_events(run_id, ascending=ascending)

    @app.post("/api/runs/{run_id}/artifacts/index")
    def index_artifacts(run_id: str, session: Session = Depends(get_session)):
        return ArtifactService(session).index_run(run_id)

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str, session: Session = Depends(get_session)):
        return ArtifactService(session).list_artifacts(run_id)

    @app.get("/api/runs/{run_id}/artifacts/{artifact_name:path}")
    def read_artifact(
        run_id: str, artifact_name: str, session: Session = Depends(get_session)
    ):
        return {"content": ArtifactService(session).read_text(run_id, artifact_name)}

    return app


app = create_app()
