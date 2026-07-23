from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime

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
    enqueue_improve,
    enqueue_run,
    enqueue_sandbox_start,
    enqueue_sandbox_stop,
    enqueue_validation,
)
from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.db import get_engine, get_session, init_db
from harness_lab_app.harness_lifecycle import drafting, sanitizer
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.sandbox_service import VALID_KINDS, SandboxService
from harness_lab_app.schemas import (
    CaseCreate,
    RunConfigCreate,
    RunCreate,
    StagedSkillCreate,
    StagedSkillUpdate,
    SwebenchImportRequest,
)
from harness_lab_app.staged_skill_service import StagedSkillError, StagedSkillService
from harness_lab_app.swebench_client import SwebenchFetchError, normalize_instance
from harness_lab_app.swebench_pro_client import (
    SwebenchProFetchError,
    list_instances as pro_list_instances,
    normalize_instance as pro_normalize_instance,
)
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

    @app.get("/api/harness/skills")
    def list_harness_skills():
        from harness_lab_app.skills_catalog import list_harness_skills as _list

        return _list()

    @app.get("/api/docs")
    def list_docs():
        from harness_lab_app.docs_catalog import list_docs as _list

        return _list()

    @app.get("/api/docs/{slug}")
    def get_doc(slug: str):
        from harness_lab_app.docs_catalog import read_doc

        doc = read_doc(slug)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Unknown doc: {slug}")
        return doc

    @app.get("/api/models")
    def list_models():
        # Live model discovery from LiteLLM so the run form stays in sync with
        # the gateway (the single place the user updates). Falls back to the
        # opencode.json routing map when the gateway is unreachable.
        from pathlib import Path

        from harness_lab_app.executors import rewrite_localhost
        from harness_lab_app.gateway_models import (
            build_model_catalog,
            list_gateway_models,
            read_opencode_gateway_models,
        )

        settings = get_settings()
        base = read_env_file_value("GATEWAY_BASE_URL")
        token = read_env_file_value("GATEWAY_TOKEN")
        # Inside a container "localhost" is the container itself; rewrite it to
        # the docker host alias so the API can reach the host-side gateway.
        if base and Path("/.dockerenv").exists():
            base = rewrite_localhost(base, settings.docker_host_alias)
        live = list_gateway_models(base_url=base, token=token)
        opencode_models = read_opencode_gateway_models(settings.repo_root)
        return build_model_catalog(live, opencode_models)

    @app.get("/api/staged-skills")
    def list_staged_skills(session: Session = Depends(get_session)):
        return StagedSkillService(session).list_skills()

    @app.post("/api/staged-skills")
    def create_staged_skill(
        data: StagedSkillCreate, session: Session = Depends(get_session)
    ):
        try:
            return StagedSkillService(session).create_skill(
                data.name, data.description, data.content
            )
        except StagedSkillError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/staged-skills/{name}")
    def update_staged_skill(
        name: str, data: StagedSkillUpdate, session: Session = Depends(get_session)
    ):
        try:
            return StagedSkillService(session).update_skill(
                name,
                description=data.description,
                content=data.content,
                status=data.status,
            )
        except StagedSkillError as exc:
            status = 404 if "not found" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/staged-skills/{name}", status_code=204)
    def delete_staged_skill(name: str, session: Session = Depends(get_session)):
        try:
            StagedSkillService(session).delete_skill(name)
        except StagedSkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/staged-skills/{name}/promote")
    def promote_staged_skill(name: str, session: Session = Depends(get_session)):
        try:
            return StagedSkillService(session).promote_skill(name)
        except StagedSkillError as exc:
            status = 404 if "not found" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

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

    @app.post("/api/cases/import-swebench")
    def import_swebench(
        data: SwebenchImportRequest, session: Session = Depends(get_session)
    ):
        # The HF datasets-server fetch is a short HTTP GET, so it runs inline
        # (sync endpoints execute in the threadpool). Import is idempotent:
        # an already-imported instance returns the existing Case.
        try:
            spec = normalize_instance(data.instance_id)
        except SwebenchFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CaseService(session).import_swebench_instance(spec)

    @app.get("/api/swebench-pro/instances")
    def list_swebench_pro_instances(session: Session = Depends(get_session)):
        # Browse catalog for the import UI (Python subset, disk-cached daily —
        # the dataset is static). `imported` marks instances already in the DB.
        try:
            catalog = pro_list_instances()
        except SwebenchProFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        existing = {case.case_id for case in CaseService(session).list_cases()}
        return [
            {**entry, "imported": entry["instance_id"] in existing} for entry in catalog
        ]

    @app.post("/api/cases/import-swebench-pro")
    def import_swebench_pro(
        data: SwebenchImportRequest, session: Session = Depends(get_session)
    ):
        # SWE-bench Pro lane (docs/design-swe-bench-pro-lane.md): fetches the
        # instance plus its run_script/parser assets. Python instances only;
        # idempotent like the Lite/Verified import.
        try:
            spec = pro_normalize_instance(data.instance_id)
        except SwebenchProFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CaseService(session).import_swebench_pro_instance(spec, polish=True)

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

    @app.get("/api/runs/{run_id}/improvements")
    def get_improvements(run_id: str):
        # The artifact is file-based (data/runs/<id>/improvements.json), written
        # by the worker's improve task or the CLI (`make improve RUN=<id>`).
        path = get_settings().data_runs_dir / run_id / "improvements.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No improvements yet")
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail=f"improvements.json unreadable: {exc}"
            ) from exc

    @app.post("/api/runs/{run_id}/improve")
    def run_improve(
        run_id: str,
        model: str | None = None,
        session: Session = Depends(get_session),
    ):
        # Improvement synthesis needs a finished audit (it reads audit.md).
        try:
            RunService(session).get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit = AuditService(session).get_audit(run_id)
        if audit is None or audit.status != "done":
            raise HTTPException(
                status_code=409,
                detail="Run the LLM audit first — proposals are synthesized from it.",
            )
        enqueue_improve(run_id, model)
        return {"status": "queued", "run_id": run_id}

    @app.get("/api/runs/{run_id}/proposal-issue")
    def get_proposal_issue(run_id: str, session: Session = Depends(get_session)):
        # Synthesize a sanitized ml-python-base improvement issue from this run's
        # audit — copy-paste markdown, nothing written. Synchronous LLM call (runs
        # in FastAPI's threadpool). Needs a finished audit.
        try:
            RunService(session).get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit = AuditService(session).get_audit(run_id)
        if audit is None or audit.status != "done":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Run the LLM audit first (the 'Run audit' button) — the issue is "
                    "synthesized from it."
                ),
            )
        # bench_audit.call_gateway reads GATEWAY_BASE_URL/TOKEN from os.environ, which
        # the API process (especially in Docker) may not have. Populate them from .env,
        # rewriting localhost so a container can reach the host gateway (as /api/models does).
        import os
        from pathlib import Path

        from harness_lab_app.executors import rewrite_localhost

        base = read_env_file_value("GATEWAY_BASE_URL")
        token = read_env_file_value("GATEWAY_TOKEN")
        if not base or not token:
            raise HTTPException(
                status_code=502,
                detail=(
                    "LLM gateway not configured — set GATEWAY_BASE_URL and GATEWAY_TOKEN "
                    "in .env, then restart the app."
                ),
            )
        if Path("/.dockerenv").exists():
            base = rewrite_localhost(base, get_settings().docker_host_alias)
        os.environ["GATEWAY_BASE_URL"] = base
        os.environ["GATEWAY_TOKEN"] = token

        run_dir = get_settings().data_runs_dir / run_id
        try:
            markdown, form = drafting.synthesize_issue(
                run_dir,
                repository="marcosdh1987/ml-python-base",
                created_at=datetime.now(UTC),
            )
        except sanitizer.SanitizationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Refusing to generate — the issue would leak private data: {exc}",
            ) from exc
        except (Exception, SystemExit) as exc:
            # bench_audit.call_gateway raises SystemExit on config errors; surface any
            # synthesis failure as a clean message instead of an opaque 500.
            raise HTTPException(
                status_code=502, detail=f"Could not synthesize the issue: {exc}"
            ) from exc
        return {
            "run_id": run_id,
            "repository": "marcosdh1987/ml-python-base",
            "markdown": markdown,
            "form": form.to_dict(),
        }

    @app.get("/api/runs/{run_id}/sandbox")
    def get_sandbox(
        run_id: str, kind: str = "terminal", session: Session = Depends(get_session)
    ):
        sandbox = SandboxService(session).get_sandbox(run_id, kind)
        if sandbox is None:
            raise HTTPException(status_code=404, detail="No sandbox for this run")
        return sandbox

    @app.post("/api/runs/{run_id}/sandbox")
    def start_sandbox(
        run_id: str, kind: str = "terminal", session: Session = Depends(get_session)
    ):
        if kind not in VALID_KINDS:
            raise HTTPException(
                status_code=422, detail=f"kind must be one of {VALID_KINDS}"
            )
        workspace = get_settings().data_runs_dir / run_id / "workspace"
        if not workspace.exists():
            raise HTTPException(
                status_code=409,
                detail="No workspace to sandbox yet — the run must finish first.",
            )
        SandboxService(session).mark_starting(run_id, kind)
        enqueue_sandbox_start(run_id, kind)
        return {"status": "queued", "run_id": run_id, "kind": kind}

    @app.delete("/api/runs/{run_id}/sandbox", status_code=202)
    def stop_sandbox(run_id: str, kind: str = "terminal"):
        enqueue_sandbox_stop(run_id, kind)
        return {"status": "stopping", "run_id": run_id, "kind": kind}

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
