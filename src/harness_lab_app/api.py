from __future__ import annotations

import contextlib

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from harness_lab_app.artifact_service import ArtifactService
from harness_lab_app.case_service import CaseService
from harness_lab_app.celery_app import enqueue_run
from harness_lab_app.config import get_settings
from harness_lab_app.db import get_engine, get_session, init_db
from harness_lab_app.run_service import RunAlreadyExistsError, RunService
from harness_lab_app.schemas import CaseCreate, RunConfigCreate, RunCreate


class LoginRequest(BaseModel):
    username: str
    password: str


class ScoreRequest(BaseModel):
    score: int
    interventions: int
    notes: str = ""


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
        except RunAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/score")
    def score_run(
        run_id: str, data: ScoreRequest, session: Session = Depends(get_session)
    ):
        return RunService(session).score_run(
            run_id, data.score, data.interventions, data.notes
        )

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, session: Session = Depends(get_session)):
        return RunService(session).cancel_run(run_id)

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str, session: Session = Depends(get_session)):
        return RunService(session, enqueue=enqueue_run).retry_run(run_id)

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
