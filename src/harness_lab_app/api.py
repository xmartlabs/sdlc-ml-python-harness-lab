from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from sqlmodel import Session

from harness_lab_app.artifact_service import ArtifactService
from harness_lab_app.attribution_service import AttributionService
from harness_lab_app.audit_service import AuditService
from harness_lab_app.auth import (
    SESSION_COOKIE,
    issue_session,
    record_failure,
    record_success,
    throttle_retry_after,
    verify_password,
)
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
from harness_lab_app.gateway_catalog_service import GatewayCatalogService
from harness_lab_app.harness_lifecycle import drafting, sanitizer
from harness_lab_app.improvement_service import ImprovementError, ImprovementService
from harness_lab_app.leaderboard_service import LeaderboardService
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.sandbox_service import VALID_KINDS, SandboxService
from harness_lab_app.schemas import (
    CaseCreate,
    EnvUpdateRequest,
    GatewayModelUpdate,
    GatewayTestRequest,
    GroupIssueRequest,
    HealRequest,
    RunConfigCreate,
    RunCreate,
    StagedSkillCreate,
    StagedSkillFileWrite,
    StagedSkillImportRequest,
    StagedSkillUpdate,
    SwebenchImportRequest,
)
from harness_lab_app.session_middleware import (
    SessionMiddleware,
    admin_password_hash,
    auth_enabled,
    has_valid_session,
    session_secret,
    session_ttl,
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
    """Single-user login: there is no username, only the admin password."""

    password: str


class ScoreRequest(BaseModel):
    score: int
    interventions: int
    notes: str = ""
    rubric: dict[str, int] = {}


def _improvements_status(runs_dir: Path, run_id: str) -> str:
    """Has this run's improvement synthesis run, and did it yield proposals?

    Read straight from the file artifact (data/runs/<id>/improvements.json), the
    same source the per-run panel uses, so the Runs list shows at a glance whether
    a run still needs a synthesis pass:

    - ``"yes"``     — synthesized and it proposed at least one change.
    - ``"no"``      — synthesized but proposed nothing (gate closed / clean run).
    - ``"pending"`` — never synthesized (or unreadable) — go generate it.

    Best-effort: any read/parse error degrades to ``"pending"`` rather than
    failing the whole list.
    """
    path = runs_dir / run_id / "improvements.json"
    if not path.exists():
        return "pending"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "pending"
    return "yes" if doc.get("proposals") else "no"


def _gateway_endpoint() -> tuple[str, str]:
    """The gateway base URL and token as reachable *from this process*.

    Inside a container "localhost" is the container itself, so it is rewritten
    to the docker host alias. Values come from ``.env`` on every call, which is
    why a Settings-page edit needs no restart.
    """
    from harness_lab_app.executors import rewrite_localhost

    base = read_env_file_value("GATEWAY_BASE_URL")
    token = read_env_file_value("GATEWAY_TOKEN")
    if base and Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    return base, token


def _prepare_gateway_env() -> None:
    """Populate GATEWAY_BASE_URL/TOKEN from .env for in-process LLM calls.

    bench_audit.call_gateway reads them from os.environ, which the API process
    (especially in Docker) may not have; localhost is rewritten so a container
    can reach the host gateway. Raises a 502 HTTPException when unconfigured.

    ``read_env_file_value`` re-reads ``.env`` on every call, so a value saved
    from the Settings page applies to the very next request — no restart.
    """
    import os

    from harness_lab_app.executors import rewrite_localhost

    base = read_env_file_value("GATEWAY_BASE_URL")
    token = read_env_file_value("GATEWAY_TOKEN")
    if not base or not token:
        raise HTTPException(
            status_code=502,
            detail=(
                "LLM gateway not configured — set GATEWAY_BASE_URL and "
                "GATEWAY_TOKEN on the Settings page. The change takes effect "
                "immediately; no restart is needed."
            ),
        )
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    os.environ["GATEWAY_BASE_URL"] = base
    os.environ["GATEWAY_TOKEN"] = token


def _client_key(request: Request | None) -> str:
    """Throttle bucket for a login attempt."""
    return request.client.host if request is not None and request.client else "unknown"


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic Harness Lab")
    # Gate every /api route that is not explicitly public. Middleware rather than
    # a per-route dependency so routes added later are covered by default
    # (ADR-0015).
    app.add_middleware(SessionMiddleware)

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

    @app.get("/api/health/detail")
    def health_detail(session: Session = Depends(get_session)):
        """The drift panel (ADR-0018). Deliberately *not* public: it reports
        gateway configuration and repository state."""
        from harness_lab_app.health_service import collect_health

        return collect_health(session)

    @app.post("/api/health/heal")
    def health_heal(data: HealRequest, session: Session = Depends(get_session)):
        from harness_lab_app.health_service import HealthError, heal

        try:
            return heal(data.action, session)
        except HealthError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # a failed repair is a 502, not a crash
            raise HTTPException(
                status_code=502, detail=f"{data.action} failed: {exc}"
            ) from exc

    @app.get("/api/auth/session")
    def session_probe(request: Request = None) -> dict[str, bool]:  # type: ignore[assignment]
        """What the SPA asks on mount: is auth on, and am I already in?

        In open mode ``authenticated`` is true so the app renders as it always
        has; the UI keys its "authentication is off" banner off ``auth_enabled``.
        """
        if not auth_enabled():
            return {"authenticated": True, "auth_enabled": False}
        return {
            "authenticated": request is not None and has_valid_session(request),
            "auth_enabled": True,
        }

    @app.post("/api/auth/login")
    def login(
        data: LoginRequest,
        response: Response,
        request: Request = None,  # type: ignore[assignment]
    ) -> dict[str, bool]:
        if not auth_enabled():
            # No password configured: nothing to log into, and refusing here
            # would strand the UI. `auth_enabled` tells it to show the banner.
            return {"authenticated": True, "auth_enabled": False}

        client = _client_key(request)
        retry_after = throttle_retry_after(client)
        if retry_after > 0:
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Try again shortly.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        if not verify_password(data.password, admin_password_hash()):
            record_failure(client)
            raise HTTPException(status_code=401, detail="Invalid password")

        record_success(client)
        response.set_cookie(
            SESSION_COOKIE,
            issue_session(session_secret()),
            max_age=session_ttl(),
            httponly=True,
            samesite="lax",
            secure=get_settings().session_cookie_secure,
            path="/",
        )
        return {"authenticated": True, "auth_enabled": True}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE, path="/")
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
    def list_models(session: Session = Depends(get_session)):
        # Live model discovery from LiteLLM so the run form stays in sync with
        # the gateway (the single place the user updates). Falls back to the
        # opencode.json routing map when the gateway is unreachable.
        from harness_lab_app.gateway_models import (
            build_model_catalog,
            fetch_gateway_routing_cached,
            list_gateway_models_cached,
            read_opencode_gateway_models,
        )

        settings = get_settings()
        base, token = _gateway_endpoint()
        live = list_gateway_models_cached(base, token)
        opencode_models = read_opencode_gateway_models(settings.repo_root)
        overlay = GatewayCatalogService(session).overlay_models()
        # Aliases resolving to the same served model collapse into one option:
        # they are one experimental arm, and listing both only makes the picker
        # harder to read.
        routing = fetch_gateway_routing_cached(base, token)
        return build_model_catalog(
            live, opencode_models, overlay=overlay, routing=routing
        )

    @app.get("/api/models/catalog")
    def list_model_catalog(session: Session = Depends(get_session)):
        """Catalog rows, each tagged with the backend heading it belongs under.

        The service already returns them in backend order; tagging each row lets
        the Settings table draw the same LM Studio → Cloud → Ollama sections the
        run picker uses, without re-deriving the grouping in the browser.
        """
        from harness_lab_app.gateway_models import classify_backend

        rows = GatewayCatalogService(session).list_models()
        return [
            {
                **row.model_dump(),
                "backend": backend,
                "group": group_label,
                "group_rank": rank,
            }
            for row in rows
            for rank, backend, group_label in (classify_backend(row.model_id),)
        ]

    @app.post("/api/models/refresh")
    def refresh_model_catalog(session: Session = Depends(get_session)):
        """Adopt whatever the gateway serves right now — the CLI-free sync."""
        from harness_lab_app.gateway_models import (
            clear_model_cache,
            list_gateway_models,
        )

        base, token = _gateway_endpoint()
        if not base or not token:
            raise HTTPException(
                status_code=502,
                detail=(
                    "LLM gateway not configured — set GATEWAY_BASE_URL and "
                    "GATEWAY_TOKEN on the Settings page first."
                ),
            )
        clear_model_cache()
        live = list_gateway_models(base_url=base, token=token)
        result = GatewayCatalogService(session).refresh(live)
        if not result["gateway_reachable"]:
            raise HTTPException(
                status_code=502,
                detail=(
                    "The gateway returned no models. Check that LiteLLM is up "
                    "and the token is valid; nothing was changed."
                ),
            )
        return result

    @app.post("/api/models/{model_id:path}/context-check")
    def check_model_context(model_id: str, required_tokens: int | None = None):
        """Measure the context window a model is *actually* serving right now.

        LiteLLM's declared limit is configuration; LM Studio serves whatever
        window the model was loaded with, and a JIT auto-load quietly uses the
        model's small default. Only a real oversized prompt tells the truth.
        """
        from harness_lab_app.gateway_models import (
            MIN_CONTEXT_TOKENS,
            clear_context_cache,
            probe_model_context,
        )

        base, token = _gateway_endpoint()
        if not base or not token:
            raise HTTPException(
                status_code=502,
                detail=(
                    "LLM gateway not configured — set GATEWAY_BASE_URL and "
                    "GATEWAY_TOKEN on the Settings page first."
                ),
            )
        # An explicit press means "measure it now", so the memoized verdict the
        # run gate reuses is dropped rather than replayed.
        clear_context_cache()
        result = probe_model_context(
            model_id,
            base_url=base,
            token=token,
            required_tokens=required_tokens or MIN_CONTEXT_TOKENS,
            timeout=180.0,
        )
        return result.as_dict()

    @app.patch("/api/models/{model_id:path}")
    def update_model(
        model_id: str,
        data: GatewayModelUpdate,
        session: Session = Depends(get_session),
    ):
        from harness_lab_app.gateway_catalog_service import GatewayCatalogError

        try:
            return GatewayCatalogService(session).update_model(
                model_id,
                label=data.label,
                enabled=data.enabled,
                is_default=data.is_default,
                options=data.options,
            )
        except GatewayCatalogError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/settings/gateway/test")
    def test_gateway(data: GatewayTestRequest):
        """Probe a base URL/token pair without persisting anything."""
        from harness_lab_app.executors import rewrite_localhost
        from harness_lab_app.gateway_models import list_gateway_models
        from harness_lab_app.settings_service import UNCHANGED_SENTINEL

        base = data.base_url.strip() or read_env_file_value("GATEWAY_BASE_URL")
        token = data.token.strip()
        # The form round-trips a masked secret; an untouched field means "use
        # what is already stored" rather than "test with an empty token".
        if token in ("", UNCHANGED_SENTINEL):
            token = read_env_file_value("GATEWAY_TOKEN")
        if not base or not token:
            return {"ok": False, "detail": "Base URL and token are both required."}
        if Path("/.dockerenv").exists():
            base = rewrite_localhost(base, get_settings().docker_host_alias)
        models = list_gateway_models(base_url=base, token=token)
        if not models:
            return {
                "ok": False,
                "detail": (
                    "No models returned. The gateway is unreachable from this "
                    "container, or the token is invalid."
                ),
            }
        return {"ok": True, "model_count": len(models), "sample": models[:5]}

    @app.get("/api/settings/env")
    def read_env_settings():
        from harness_lab_app.settings_service import read_settings

        return read_settings()

    @app.put("/api/settings/env")
    def write_env_settings(data: EnvUpdateRequest):
        from harness_lab_app.settings_service import (
            SettingsError,
            apply_settings,
            read_settings,
        )

        try:
            result = apply_settings(data.updates)
        except SettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Return the fresh (masked) view so the form re-syncs without a second
        # round-trip and never keeps a stale sentinel in its state.
        return {**result, **read_settings()}

    @app.get("/api/settings/runtime")
    def read_runtime_settings():
        from harness_lab_app.settings_service import read_runtime_facts

        return read_runtime_facts()

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

    def _import_choices(exc) -> dict:
        """A catalog source is not an error — it just has to be narrowed down."""
        return {
            "preview": False,
            "choices": [
                {
                    "name": candidate.name,
                    "path": candidate.path,
                    "description": candidate.description,
                }
                for candidate in exc.candidates
            ],
            "detail": str(exc),
        }

    def _import_preview(imported, requested_name: str = "") -> dict:
        """What the user reviews before anything is written."""
        return {
            "preview": True,
            "choices": [],
            "name": requested_name or imported.name,
            "description": imported.description,
            "source_url": imported.source_url,
            "source_ref": imported.source_ref,
            "source_kind": imported.source_kind,
            "warnings": imported.warnings,
            "total_bytes": imported.total_bytes,
            "files": [
                {"path": path, "size": len(blob)}
                for path, blob in sorted(imported.files.items())
            ],
        }

    @app.post("/api/staged-skills/import")
    def import_staged_skill(
        data: StagedSkillImportRequest, session: Session = Depends(get_session)
    ):
        from harness_lab_app.skill_import import (
            SkillChoiceRequired,
            SkillImportError,
            fetch_skill,
        )

        try:
            imported = fetch_skill(data.url, data.ref, data.skill)
        except SkillChoiceRequired as exc:
            return _import_choices(exc)
        except SkillImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not data.confirm:
            return _import_preview(imported, data.name)
        try:
            return StagedSkillService(session).import_skill(
                imported, name=data.name, replace=data.replace
            )
        except (StagedSkillError, SkillImportError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/staged-skills/upload")
    async def upload_staged_skill(
        file: UploadFile = File(...),
        name: str = Form(""),
        skill: str = Form(""),
        confirm: bool = Form(False),
        replace: bool = Form(False),
        session: Session = Depends(get_session),
    ):
        from harness_lab_app.skill_import import (
            MAX_DOWNLOAD_BYTES,
            SkillChoiceRequired,
            SkillImportError,
            import_upload,
        )

        payload = await file.read(MAX_DOWNLOAD_BYTES + 1)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} "
                    "MiB limit."
                ),
            )
        try:
            imported = import_upload(payload, file.filename or "", skill)
        except SkillChoiceRequired as exc:
            return _import_choices(exc)
        except SkillImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not confirm:
            return _import_preview(imported, name)
        try:
            return StagedSkillService(session).import_skill(
                imported, name=name, replace=replace
            )
        except (StagedSkillError, SkillImportError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/staged-skills/{name}/files")
    def list_staged_skill_files(name: str, session: Session = Depends(get_session)):
        try:
            return StagedSkillService(session).list_files(name)
        except StagedSkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/staged-skills/{name}/files/{path:path}")
    def read_staged_skill_file(
        name: str, path: str, session: Session = Depends(get_session)
    ):
        try:
            return StagedSkillService(session).get_file(name, path)
        except StagedSkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/staged-skills/{name}/files/{path:path}")
    def write_staged_skill_file(
        name: str,
        path: str,
        data: StagedSkillFileWrite,
        session: Session = Depends(get_session),
    ):
        from harness_lab_app.skill_import import SkillImportError

        try:
            return StagedSkillService(session).put_file(name, path, data.content)
        except (StagedSkillError, SkillImportError) as exc:
            status = 404 if "not found" in str(exc).lower() else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/staged-skills/{name}/files/{path:path}", status_code=204)
    def delete_staged_skill_file(
        name: str, path: str, session: Session = Depends(get_session)
    ):
        try:
            StagedSkillService(session).delete_file(name, path)
        except StagedSkillError as exc:
            status = 404 if "not found" in str(exc).lower() else 422
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
        runs_dir = get_settings().data_runs_dir
        return [
            {
                **run.model_dump(),
                "improvements": _improvements_status(runs_dir, run.run_id),
            }
            for run in RunService(session).list_runs()
        ]

    @app.get("/api/leaderboard")
    def leaderboard(session: Session = Depends(get_session)):
        # Flat per-run condition rows; the Dashboard groups/filters client-side.
        return LeaderboardService(session).list_rows()

    @app.get("/api/leaderboard/reference")
    def leaderboard_reference(session: Session = Depends(get_session)):
        # Curated official swebench.com rows (data/reference/…json, editable).
        return LeaderboardService(session).list_reference()

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

    @app.post("/api/runs/preflight")
    def preflight_run_config(
        config: RunConfigCreate, session: Session = Depends(get_session)
    ):
        """Run the creation preflight without creating anything.

        Same checks `POST /api/runs` applies, so a green answer here means the
        run form will not be rejected — and the slow part (probing the backend
        and measuring its context window) is memoized, so pressing Create right
        after costs nothing extra.
        """
        try:
            RunService(session).preflight(config)
        except RunPreflightError as exc:
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": "Configuration looks runnable."}

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
        strictness: str | None = None,
        session: Session = Depends(get_session),
    ):
        # Improvement synthesis needs a finished audit (it reads audit.md).
        if strictness is not None and strictness not in {
            "strict",
            "balanced",
            "lenient",
        }:
            raise HTTPException(
                status_code=400,
                detail="strictness must be one of: strict, balanced, lenient",
            )
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
        enqueue_improve(run_id, model, strictness)
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
        _prepare_gateway_env()

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

    @app.post("/api/runs/{run_id}/improvements/save")
    def save_improvements(run_id: str, session: Session = Depends(get_session)):
        # Snapshot the run's improvements.json proposals into the cross-run
        # worklist (Improvements page). Idempotent per (run_id, title).
        try:
            return ImprovementService(session).save_from_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="No improvements yet — synthesize proposals first.",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail=f"improvements.json unreadable: {exc}"
            ) from exc

    @app.get("/api/improvements")
    def list_saved_proposals(
        repo: str | None = None,
        kind: str | None = None,
        case_id: str | None = None,
        status: str | None = None,
        harness: str | None = None,
        session: Session = Depends(get_session),
    ):
        return ImprovementService(session).list_proposals(
            repo=repo, kind=kind, case=case_id, status=status, harness=harness
        )

    @app.get("/api/improvements/group-issues")
    def list_group_issues(session: Session = Depends(get_session)):
        return ImprovementService(session).list_group_issues()

    @app.post("/api/improvements/group-issue")
    def create_group_issue(
        payload: GroupIssueRequest, session: Session = Depends(get_session)
    ):
        # Consolidate N saved proposals into ONE combined sanitized issue for
        # ml-python-base. Synchronous single LLM call, same pattern as the
        # per-run proposal-issue endpoint.
        service = ImprovementService(session)
        try:
            selected = service.get_proposals(payload.proposal_ids)
        except ImprovementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _prepare_gateway_env()
        runs_dir = get_settings().data_runs_dir
        run_ids = sorted({p.run_id for p in selected})
        items = [
            {
                "run_id": p.run_id,
                "title": p.title,
                "body": p.body,
                "kind": p.kind,
                "target": p.target,
                "case": p.case,
                "harness": p.harness,
                "executor_model": p.executor_model,
            }
            for p in selected
        ]
        repository = "marcosdh1987/ml-python-base"
        model = payload.model or drafting.DEFAULT_MODEL
        try:
            markdown, form = drafting.synthesize_group_issue(
                items,
                [runs_dir / run_id for run_id in run_ids],
                repository=repository,
                created_at=datetime.now(UTC),
                model=model,
            )
        except sanitizer.SanitizationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Refusing to generate — the issue would leak private data: {exc}",
            ) from exc
        except (Exception, SystemExit) as exc:
            raise HTTPException(
                status_code=502, detail=f"Could not synthesize the issue: {exc}"
            ) from exc
        issue = service.record_group_issue(
            selected,
            repository=repository,
            title=form.title,
            markdown=markdown,
            form_json=form.to_dict(),
            model=model,
        )
        return {
            "group_issue_id": issue.id,
            "repository": repository,
            "markdown": markdown,
            "form": form.to_dict(),
            "proposal_ids": issue.proposal_ids,
            "source_run_ids": issue.source_run_ids,
        }

    @app.delete("/api/improvements/{proposal_id}", status_code=204)
    def delete_saved_proposal(
        proposal_id: int, session: Session = Depends(get_session)
    ):
        try:
            ImprovementService(session).delete_proposal(proposal_id)
        except ImprovementError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
