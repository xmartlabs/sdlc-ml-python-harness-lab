from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from sqlmodel import Session, select

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
from harness_lab_app.case_derivation import derive_candidates
from harness_lab_app.case_draft_service import generate_case_draft
from harness_lab_app.case_service import (
    CaseExistsError,
    CaseService,
    CaseValidationError,
    resolve_prompt_variant_text,
)
from harness_lab_app.celery_app import (
    enqueue_audit,
    enqueue_compare_audit,
    enqueue_container_cleanup,
    enqueue_experiment_audit,
    enqueue_gate_check,
    enqueue_improve,
    enqueue_repository_audit,
    enqueue_run,
    enqueue_sandbox_start,
    enqueue_sandbox_stop,
    enqueue_validation,
)
from harness_lab_app.checkout_service import CheckoutError
from harness_lab_app.compare_audit_service import (
    MAX_RUNS,
    MIN_RUNS,
    CompareAuditService,
    comparison_key,
    runs_finished as comparison_runs_finished,
)
from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.constants import EXAMPLE_LMSTUDIO_URL, EXAMPLE_OLLAMA_URL
from harness_lab_app.db import get_engine, get_session, init_db
from harness_lab_app.experiment_audit_service import (
    TERMINAL_STATES,
    ExperimentAuditService,
    runs_finished,
)
from harness_lab_app.experiment_service import (
    ExperimentDesignError,
    ExperimentError,
    ExperimentNotFoundError,
    ExperimentService,
)
from harness_lab_app.finding_service import (
    FindingError,
    FindingNotFoundError,
    FindingService,
)
from harness_lab_app.gateway_catalog_service import GatewayCatalogService
from harness_lab_app.harness_lifecycle import drafting, sanitizer
from harness_lab_app.improvement_service import ImprovementError, ImprovementService
from harness_lab_app.leaderboard_service import LeaderboardService
from harness_lab_app.observability_service import ObservabilityService
from harness_lab_app.overview_service import OverviewService
from harness_lab_app.run_service import (
    RunAlreadyExistsError,
    RunBatchTooLargeError,
    RunContinueError,
    RunDeleteConflictError,
    RunPreflightError,
    RunService,
)
from harness_lab_app.sandbox_service import VALID_KINDS, SandboxService
from harness_lab_app.schemas import (
    AssessmentCreate,
    CaseCreate,
    CaseDraftRequest,
    CaseDraftResponse,
    CheckoutAttachRequest,
    CheckoutFromLocalRequest,
    EnvUpdateRequest,
    ExperimentCreate,
    ExperimentLaunch,
    FindingCreate,
    FindingUpdate,
    GatewayModelUpdate,
    GatewayTestRequest,
    GroupIssueRequest,
    HealRequest,
    RunBatchCreate,
    RunBatchRead,
    RunConfigCreate,
    RunContinueRequest,
    RunCreate,
    StagedSkillCreate,
    StagedSkillFileWrite,
    StagedSkillImportRequest,
    StagedSkillUpdate,
    SuggestionAccept,
    SuggestionReject,
    SuiteExperimentCreate,
    SwebenchImportRequest,
    TargetInspectRequest,
    TargetRepositoryCreate,
    TargetRepositoryUpdate,
    TargetResolveRequest,
)
from harness_lab_app.score_service import ScoreService
from harness_lab_app.scores import REGISTRY, definitions_by_category
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
from harness_lab_app.target_service import (
    TargetError,
    TargetNotFoundError,
    TargetService,
    clone_source,
    pin_checkout,
    resolve_ref,
)
from harness_lab_app.validation_service import ValidationService

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_lab_app.gateway_models import DirectHostProbe


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


def _direct_endpoint(env_key: str) -> str:
    """A direct host's base URL as reachable *from this process*.

    Same container caveat as :func:`_gateway_endpoint`: a ``localhost`` URL
    written on the host means the container itself once the API runs in Docker,
    so it is rewritten to the docker host alias. Empty when unset.
    """
    from harness_lab_app.executors import rewrite_localhost

    base = read_env_file_value(env_key) or ""
    if base and Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    return base


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
        # Every authored benchmark case exists on a fresh DB, and a stale one
        # is refreshed from its task.yaml (ADR-0025). Seeding must never block
        # startup.
        with contextlib.suppress(Exception), Session(get_engine()) as session:
            CaseService(session).seed_benchmark_cases()

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
    def list_models(refresh: bool = False, session: Session = Depends(get_session)):
        # Live model discovery from LiteLLM so the run form stays in sync with
        # the gateway (the single place the user updates). Falls back to the
        # opencode.json routing map when the gateway is unreachable.
        #
        # ``refresh`` drops both TTL caches first. It backs the run form's
        # Reload button: the two lists come from different servers with
        # different cache lifetimes, and having to guess which one is stale is
        # exactly the confusion the button exists to remove.
        from harness_lab_app.gateway_models import (
            build_model_catalog,
            clear_model_cache,
            fetch_gateway_routing_cached,
            list_gateway_models_cached,
            read_opencode_gateway_models,
        )
        from harness_lab_app.lmstudio_direct import clear_lmstudio_cache
        from harness_lab_app.ollama_direct import clear_ollama_cache

        if refresh:
            clear_model_cache()
            clear_lmstudio_cache()
            clear_ollama_cache()
        settings = get_settings()
        base, token = _gateway_endpoint()
        live = list_gateway_models_cached(base, token)
        opencode_models = read_opencode_gateway_models(settings.repo_root)
        overlay = GatewayCatalogService(session).overlay_models()
        # Aliases resolving to the same served model collapse into one option:
        # they are one experimental arm, and listing both only makes the picker
        # harder to read.
        routing = fetch_gateway_routing_cached(base, token)
        # Read the LAN hosts first: the same probe that fills the direct group
        # tells the gateway group which of its cards point at a machine that is
        # currently down, so a dead backend costs one round-trip, not two.
        direct, direct_errors, probes = _direct_options()
        catalog = build_model_catalog(
            live, opencode_models, overlay=overlay, routing=routing, probes=probes
        )
        # Direct models lead the menu: they are the only entries whose name is
        # verified against the server that will serve them.
        catalog["models"] = direct + catalog["models"]
        catalog["direct_errors"] = direct_errors
        return catalog

    def _direct_options() -> tuple[list[dict], dict[str, str], list[DirectHostProbe]]:
        """Picker options for the direct sources, why each empty one is empty,
        and what each host answered.

        The reasons travel with the list because an empty group is otherwise
        indistinguishable from "no models installed", and the fix (start the
        server, fix the URL) depends entirely on which one it was — per host,
        since one may be up while the other is not.

        The probes travel with it for a second consumer: gateway cards forward
        to these very hosts, so one reading serves both groups. Without it the
        run form said "LM Studio host did not answer" under the direct heading
        while listing the gateway aliases for that same host as launchable.
        """
        from harness_lab_app.direct_hosts import DirectHostUnreachable, host_root
        from harness_lab_app.gateway_models import DirectHostProbe
        from harness_lab_app.lmstudio_direct import (
            LMSTUDIO_PROVIDER_LABEL,
            build_direct_options as build_lmstudio_options,
            list_lmstudio_models_cached,
        )
        from harness_lab_app.ollama_direct import (
            OLLAMA_PROVIDER_LABEL,
            build_direct_options as build_ollama_options,
            list_ollama_models_cached,
        )

        options: list[dict] = []
        errors: dict[str, str] = {}
        probes: list[DirectHostProbe] = []

        def collect(
            provider: str,
            env_key: str,
            label: str,
            read: Callable[[str], tuple[list[dict], set[str]]],
        ) -> None:
            """Add one host's options and its probe, or record why it has none."""
            base = _direct_endpoint(env_key)
            if not base:
                errors[provider] = f"{env_key} is not set."
                return
            root = host_root(base)
            try:
                host_options, model_ids = read(base)
            except DirectHostUnreachable as exc:
                errors[provider] = str(exc)
                probes.append(
                    DirectHostProbe(
                        root=root, label=label, reachable=False, error=str(exc)
                    )
                )
                return
            options.extend(host_options)
            probes.append(
                DirectHostProbe(
                    root=root,
                    label=label,
                    reachable=True,
                    model_ids=frozenset(model_ids),
                )
            )

        def lmstudio(base: str) -> tuple[list[dict], set[str]]:
            models = list_lmstudio_models_cached(base)
            return build_lmstudio_options(models), {m.id for m in models}

        def ollama(base: str) -> tuple[list[dict], set[str]]:
            models = list_ollama_models_cached(base)
            return build_ollama_options(models), {m.id for m in models}

        collect("lmstudio", "LMSTUDIO_BASE_URL", LMSTUDIO_PROVIDER_LABEL, lmstudio)
        collect("ollama", "OLLAMA_BASE_URL", OLLAMA_PROVIDER_LABEL, ollama)
        return options, errors, probes

    @app.get("/api/lmstudio/models")
    def list_lmstudio(refresh: bool = False):
        """What the LM Studio host is serving right now, verbatim.

        No catalog, no database, no alias map: the Settings page renders this
        list as-is so what you see is what a run will address.
        """
        from harness_lab_app.lmstudio_direct import (
            LMStudioUnreachable,
            clear_lmstudio_cache,
            list_lmstudio_models_cached,
            lmstudio_root,
        )

        base = _direct_endpoint("LMSTUDIO_BASE_URL")
        if not base:
            return {
                "base_url": "",
                "reachable": False,
                "error": (
                    "LMSTUDIO_BASE_URL is not set — point it at your LM Studio "
                    f"server (for example {EXAMPLE_LMSTUDIO_URL}) below."
                ),
                "models": [],
            }
        if refresh:
            clear_lmstudio_cache()
        try:
            models = list_lmstudio_models_cached(base)
        except LMStudioUnreachable as exc:
            return {
                "base_url": lmstudio_root(base),
                "reachable": False,
                "error": str(exc),
                "models": [],
            }
        return {
            "base_url": lmstudio_root(base),
            "reachable": True,
            "error": None,
            "models": [model.as_dict() for model in models],
        }

    @app.get("/api/ollama/models")
    def list_ollama(refresh: bool = False):
        """What the Ollama host has right now, verbatim — the sibling endpoint.

        ``running`` models report the context window the server is serving;
        everything else runs at the host's ``OLLAMA_CONTEXT_LENGTH``, which this
        cannot read remotely and which defaults far below what a run needs.
        """
        from harness_lab_app.direct_hosts import DirectHostUnreachable
        from harness_lab_app.ollama_direct import (
            clear_ollama_cache,
            list_ollama_models_cached,
            ollama_root,
        )

        base = _direct_endpoint("OLLAMA_BASE_URL")
        if not base:
            return {
                "base_url": "",
                "reachable": False,
                "error": (
                    "OLLAMA_BASE_URL is not set — point it at your Ollama server "
                    f"(for example {EXAMPLE_OLLAMA_URL}) below."
                ),
                "models": [],
            }
        if refresh:
            clear_ollama_cache()
        try:
            models = list_ollama_models_cached(base)
        except DirectHostUnreachable as exc:
            return {
                "base_url": ollama_root(base),
                "reachable": False,
                "error": str(exc),
                "models": [],
            }
        return {
            "base_url": ollama_root(base),
            "reachable": True,
            "error": None,
            "models": [model.as_dict() for model in models],
        }

    @app.get("/api/models/catalog")
    def list_model_catalog(session: Session = Depends(get_session)):
        """Catalog rows, each tagged with the backend heading it belongs under.

        The service already returns them in backend order; tagging each row lets
        the Settings table draw the same LM Studio → Cloud → Ollama sections the
        run picker uses, without re-deriving the grouping in the browser.
        """
        from harness_lab_app.gateway_models import (
            SOURCE_GATEWAY,
            classify_backend,
            classify_provider,
        )

        rows = GatewayCatalogService(session).list_models()
        return [
            {
                **row.model_dump(),
                "backend": backend,
                "group": group_label,
                "group_rank": rank,
                "source": SOURCE_GATEWAY,
                "provider": provider,
                "provider_label": provider_label,
            }
            for row in rows
            for rank, backend, group_label in (classify_backend(row.model_id),)
            for provider, provider_label in (classify_provider(row.model_id),)
        ]

    @app.post("/api/models/refresh")
    def refresh_model_catalog(session: Session = Depends(get_session)):
        """Adopt whatever the gateway serves right now — the CLI-free sync."""
        from harness_lab_app.gateway_models import (
            clear_model_cache,
            list_gateway_models,
        )
        from harness_lab_app.lmstudio_direct import clear_lmstudio_cache

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
        # The direct list is a second half of the same menu, on a second cache.
        # Leaving it behind is what made "Refresh" look like it half-worked.
        clear_lmstudio_cache()
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

    @app.get("/api/overview")
    def get_overview(window_days: int = 30, session: Session = Depends(get_session)):
        """What the lab knows about itself: quality, regressions, cost, backlog.

        Computed server-side so the Dashboard cannot drift from what the same
        numbers mean anywhere else, and so the definitions ("what counts as a
        regression") are testable rather than implied by a chart.
        """
        return OverviewService(session).build(window_days=max(1, min(window_days, 365)))

    @app.get("/api/attention")
    def get_attention(session: Session = Depends(get_session)):
        """What needs attention, in human language, each with a way in (§4)."""
        from harness_lab_app.attention_service import AttentionService

        return AttentionService(session).build()

    @app.get("/api/evidence-summary")
    def get_evidence_summary(session: Session = Depends(get_session)):
        """The handful of figures the home page may carry below the workflows."""
        from harness_lab_app.attention_service import evidence_summary

        return evidence_summary(session)

    @app.get("/api/findings")
    def list_findings(session: Session = Depends(get_session)):
        return FindingService(session).list_findings()

    @app.get("/api/findings/suggestions")
    def list_finding_suggestions(session: Session = Depends(get_session)):
        """Patterns the corpus supports — proposals, not findings (§14).

        Computed on demand and never written. Nothing here is a finding of
        record until a person accepts it and their name goes on it.
        """
        return {"suggestions": FindingService(session).suggestions()}

    @app.post("/api/findings/suggestions/{key:path}/accept", status_code=201)
    def accept_finding_suggestion(
        key: str, data: SuggestionAccept, session: Session = Depends(get_session)
    ):
        try:
            finding, warnings = FindingService(session).accept_suggestion(
                key,
                author=data.author,
                finding_id=data.finding_id,
                overrides=data.overrides,
            )
        except FindingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**finding.model_dump(), "warnings": warnings}

    @app.post("/api/findings/suggestions/{key:path}/reject", status_code=201)
    def reject_finding_suggestion(
        key: str, data: SuggestionReject, session: Session = Depends(get_session)
    ):
        """Record the rejection rather than discarding it.

        A pattern somebody has already considered and declined should not come
        back next week as though it were new.
        """
        try:
            return FindingService(session).reject_suggestion(
                key, author=data.author, reason=data.reason
            )
        except FindingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/findings/{finding_id}")
    def get_finding(finding_id: str, session: Session = Depends(get_session)):
        try:
            return FindingService(session).get_finding(finding_id)
        except FindingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/findings", status_code=201)
    def create_finding(data: FindingCreate, session: Session = Depends(get_session)):
        try:
            finding, warnings = FindingService(session).create_finding(data)
        except FindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**finding.model_dump(), "warnings": warnings}

    @app.patch("/api/findings/{finding_id}")
    def update_finding(
        finding_id: str, data: FindingUpdate, session: Session = Depends(get_session)
    ):
        try:
            return FindingService(session).update_finding(finding_id, data)
        except FindingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/findings/{finding_id}/experiment-draft")
    def draft_experiment_from_finding(
        finding_id: str, session: Session = Depends(get_session)
    ):
        """The experiment that would settle this finding — as a draft.

        Not created outright: the design needs cases and a harness axis the
        operator chooses, and an experiment auto-generated from a sentence is
        exactly the unanswerable design ADR-0033 refuses.
        """
        try:
            return FindingService(session).experiment_draft(finding_id)
        except FindingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/assessments/rubric")
    def get_assessment_rubric():
        """The maturity rubric. Scored by a human, never computed."""
        return {"dimensions": FindingService.rubric()}

    @app.get("/api/assessments")
    def list_assessments(session: Session = Depends(get_session)):
        return FindingService(session).list_assessments()

    @app.post("/api/assessments", status_code=201)
    def create_assessment(
        data: AssessmentCreate, session: Session = Depends(get_session)
    ):
        try:
            assessment, warnings = FindingService(session).create_assessment(data)
        except FindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**assessment.model_dump(), "warnings": warnings}

    @app.get("/api/scores/definitions")
    def list_score_definitions():
        """The registry itself, so a client can render a score it has never seen.

        Every value the lab stores is filed under one of these. The `provenance`
        field is the one that must reach the UI: a fact, an observation and a
        judgement are three different kinds of claim and must not be drawn the
        same way.
        """
        return {
            "count": len(REGISTRY),
            "categories": {
                category: [definition.__dict__ for definition in definitions]
                for category, definitions in definitions_by_category().items()
            },
        }

    @app.post("/api/runs/{run_id}/scores/export")
    def export_run_scores(run_id: str, session: Session = Depends(get_session)):
        """Push this run's exportable scores onto its Langfuse session.

        One-way on purpose: the lab database stays the source of truth for the
        experiment domain, and Langfuse is where traces and telemetry live.
        """
        from harness_lab_app.observability.score_export import export_scores

        scores = ScoreService(session).list_scores(run_id)
        result = export_scores(run_id, scores)
        return result.__dict__

    @app.get("/api/scores/aggregate")
    def aggregate_scores(session: Session = Depends(get_session)):
        """The score corpus, one entry per definition, with direction attached."""
        return ScoreService(session).aggregate()

    @app.get("/api/runs/{run_id}/scores")
    def get_run_scores(run_id: str, session: Session = Depends(get_session)):
        return ScoreService(session).list_scores(run_id)

    @app.post("/api/runs/{run_id}/scores")
    def evaluate_run_scores(
        run_id: str, profile: str = "default", session: Session = Depends(get_session)
    ):
        try:
            return ScoreService(session).evaluate_run(run_id, profile)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/experiments")
    def list_experiments(family_id: str = "", session: Session = Depends(get_session)):
        service = ExperimentService(session)
        experiments = service.list_experiments()
        if family_id:
            # A ladder is read as one family (ADR-0051).
            experiments = [e for e in experiments if e.family_id == family_id]
        rows = []
        for experiment in experiments:
            runs = service.runs(experiment.experiment_id)
            rows.append(
                {
                    **experiment.model_dump(),
                    "condition_count": len(
                        service.conditions(experiment.experiment_id)
                    ),
                    "run_count": len(runs),
                    # Derived from the runs, not from `status`, which is set at
                    # launch and can lag behind the last run.
                    "finished": bool(runs)
                    and all(run.state in TERMINAL_STATES for run in runs),
                }
            )
        return rows

    @app.get("/api/experiments/{experiment_id}")
    def get_experiment(experiment_id: str, session: Session = Depends(get_session)):
        service = ExperimentService(session)
        try:
            experiment = service.get_experiment(experiment_id)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            **experiment.model_dump(),
            "conditions": service.conditions(experiment_id),
            "runs": service.runs(experiment_id),
            # The configuration the experiment's real runs already carry, so a
            # relaunch reuses it instead of asking again — the second ask is
            # how two measurements end up filed under one name.
            "launch_config": service.launch_config(experiment_id),
        }

    @app.get("/api/experiments/{experiment_id}/audit")
    def get_experiment_audit(
        experiment_id: str, session: Session = Depends(get_session)
    ):
        audit = ExperimentAuditService(session).get(experiment_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="No audit for this experiment")
        return audit

    @app.post("/api/experiments/{experiment_id}/audit")
    def audit_experiment(
        experiment_id: str,
        auditor_model: str | None = None,
        session: Session = Depends(get_session),
    ):
        """Queue the LLM reading of a finished experiment.

        Refused while any run is still executing: a reading of a half-finished
        experiment would describe a comparison that does not exist yet, and it
        would be persisted as though it did.
        """
        service = ExperimentService(session)
        try:
            service.get_experiment(experiment_id)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finished, done, total = runs_finished(session, experiment_id)
        if not finished:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{done} of {total} run(s) have finished; the reading waits "
                    "for the last one."
                    if total
                    else "This experiment has no runs to read."
                ),
            )
        ExperimentAuditService(session).mark_running(experiment_id, auditor_model or "")
        enqueue_experiment_audit(experiment_id, auditor_model)
        return {"status": "queued", "experiment_id": experiment_id}

    def _resolve_comparison(session: Session, run_ids: list[str]) -> list[str]:
        """The selection, validated: 2-4 distinct runs that all exist."""
        ordered = list(dict.fromkeys(run_ids))
        if len(ordered) != len(run_ids):
            raise HTTPException(
                status_code=400, detail="A run cannot be compared with itself."
            )
        if not MIN_RUNS <= len(ordered) <= MAX_RUNS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A comparison reads {MIN_RUNS} to {MAX_RUNS} runs; "
                    f"{len(ordered)} were given."
                ),
            )
        runs = RunService(session)
        for run_id in ordered:
            try:
                runs.get_run(run_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ordered

    @app.get("/api/compare/audit")
    def get_compare_audit(
        run_id: list[str] = Query(default=[]),
        session: Session = Depends(get_session),
    ):
        """The stored reading for this exact selection, if one was ever asked for.

        The 404 is the empty box: the page never generates on its own, so
        "nothing here yet" is the normal state, not an error to surface.
        """
        ordered = _resolve_comparison(session, run_id)
        audit = CompareAuditService(session).get(comparison_key(ordered))
        if audit is None:
            raise HTTPException(
                status_code=404, detail="No reading for this comparison"
            )
        return audit

    @app.post("/api/compare/audit")
    def audit_compare(
        run_id: list[str] = Query(default=[]),
        auditor_model: str | None = None,
        session: Session = Depends(get_session),
    ):
        """Queue the LLM reading of a hand-picked comparison.

        Refused while any run is still executing: a reading of a run that has
        not stopped would describe an outcome that does not exist yet, and it
        would be persisted as though it did.
        """
        ordered = _resolve_comparison(session, run_id)
        finished, done, total = comparison_runs_finished(session, ordered)
        if not finished:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{done} of {total} run(s) have finished; the reading waits "
                    "for the last one."
                ),
            )
        key = comparison_key(ordered)
        CompareAuditService(session).mark_running(key, ordered, auditor_model or "")
        enqueue_compare_audit(ordered, auditor_model)
        return {"status": "queued", "comparison_key": key}

    @app.get("/api/experiments/{experiment_id}/summary")
    def summarize_experiment(
        experiment_id: str, session: Session = Depends(get_session)
    ):
        try:
            return ExperimentService(session).summarize(experiment_id)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/experiments", status_code=201)
    def create_experiment(
        data: ExperimentCreate, session: Session = Depends(get_session)
    ):
        try:
            experiment, warnings = ExperimentService(session).create_experiment(data)
        except ExperimentDesignError as exc:
            # A design that contradicts its mode is a 422, not a 500: the request
            # was well-formed, the *experiment* is not answerable.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExperimentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**experiment.model_dump(), "warnings": warnings}

    @app.post("/api/experiments/from-suite", status_code=201)
    def create_experiment_from_suite(
        data: SuiteExperimentCreate, session: Session = Depends(get_session)
    ):
        """Turn a regression suite into an experiment (§13).

        Deliberately routed through the ordinary creation path: a suite run is a
        `harness_evaluation` experiment, validated by the same design rules and
        read through the same case matrix. The suite contributes only the
        design — which cases, repeated how often.
        """
        from harness_lab_app.harness_lifecycle.suite import (
            load_default_suite,
        )
        from harness_lab_app.harness_lifecycle.suite_experiment import (
            SuiteArm,
            SuiteExperimentError,
            experiment_from_suite,
        )

        try:
            suite = load_default_suite(get_settings().repo_root, data.suite_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown suite '{data.suite_id}': {exc}"
            ) from exc

        try:
            payload = experiment_from_suite(
                suite,
                experiment_id=data.experiment_id,
                control=SuiteArm(
                    label=data.control.label,
                    overrides=data.control.overrides,
                    description=data.control.description,
                ),
                treatment=SuiteArm(
                    label=data.treatment.label,
                    overrides=data.treatment.overrides,
                    description=data.treatment.description,
                ),
                prompt_variant=data.prompt_variant,
                question=data.question,
                name=data.name,
                notes=data.notes,
            )
            experiment, warnings = ExperimentService(session).create_experiment(payload)
        except SuiteExperimentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExperimentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**experiment.model_dump(), "warnings": warnings}

    @app.post("/api/experiments/{experiment_id}/abandon")
    def abandon_experiment(experiment_id: str, session: Session = Depends(get_session)):
        """Hide an experiment from the working set; its runs stay in the corpus."""
        try:
            return ExperimentService(session).abandon_experiment(experiment_id)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/experiments/{experiment_id}")
    def delete_experiment(
        experiment_id: str,
        delete_runs: bool = False,
        session: Session = Depends(get_session),
    ):
        """Erase an experiment — with its runs, or not at all (see the service)."""
        try:
            return ExperimentService(
                session, enqueue_cleanup=enqueue_container_cleanup
            ).delete_experiment(experiment_id, delete_runs=delete_runs)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperimentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/experiments/{experiment_id}/launch", status_code=201)
    def launch_experiment(
        experiment_id: str,
        data: ExperimentLaunch,
        session: Session = Depends(get_session),
    ):
        try:
            runs = ExperimentService(session, enqueue=enqueue_run).launch(
                experiment_id, data.config
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ExperimentError, RunPreflightError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "experiment_id": experiment_id,
            "count": len(runs),
            "run_ids": [run.run_id for run in runs],
        }

    @app.get("/api/targets")
    def list_targets(
        include_archived: bool = False, session: Session = Depends(get_session)
    ):
        return TargetService(session).list_targets(include_archived=include_archived)

    @app.get("/api/targets/{target_id}")
    def get_target(target_id: str, session: Session = Depends(get_session)):
        try:
            return TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/targets", status_code=201)
    def create_target(
        data: TargetRepositoryCreate, session: Session = Depends(get_session)
    ):
        try:
            return TargetService(session).create_target(data)
        except TargetError as exc:
            # A duplicate id or a URL git would read as an option is a bad
            # request, not a server fault — the form can show it inline.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/targets/{target_id}")
    def update_target(
        target_id: str,
        data: TargetRepositoryUpdate,
        session: Session = Depends(get_session),
    ):
        try:
            return TargetService(session).update_target(target_id, data)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TargetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/targets/{target_id}", status_code=204)
    def delete_target(target_id: str, session: Session = Depends(get_session)):
        try:
            TargetService(session).delete_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/targets/inspect")
    def inspect_target(
        data: TargetInspectRequest, session: Session = Depends(get_session)
    ):
        """Readiness for a repository before it is saved (the form's Inspect).

        Lets a user learn a repository's runtime is unsupported — or that its
        draft setup/validation commands mismatch it — before creating a target
        and launching a run that would abort in provisioning. Nothing is saved.
        """
        from harness_lab_app.readiness_service import ReadinessService

        try:
            return ReadinessService(session).inspect(
                data.repository_url,
                data.default_ref,
                setup_commands=data.setup_commands,
                validation_commands=data.validation_commands,
            )
        except TargetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/targets/{target_id}/checkout")
    def target_checkout(target_id: str, session: Session = Depends(get_session)):
        """What the lab can see of the operator's own clone of this repository.

        Polled by the onboarding while the operator runs the clone command in
        their terminal. Everything it reports is observed from the directory —
        "not cloned yet" is an ordinary answer, not an error.
        """
        from harness_lab_app.checkout_service import guided_command, inspect_checkout

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        from harness_lab_app.local_sources_service import is_enabled

        try:
            state = inspect_checkout(target)
            command = guided_command(target)
        except CheckoutError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "command": command,
            "local_sources_enabled": is_enabled(),
            **state.as_dict(),
        }

    @app.post("/api/targets/{target_id}/checkout/pin")
    def pin_target_checkout(target_id: str, session: Session = Depends(get_session)):
        """Freeze the repository at the commit the operator's clone is on.

        From here the whole audit — readiness, derived cases, every run —
        reads one commit, so two experiments a week apart compare agents rather
        than a moving branch (ADR-0042).
        """
        from harness_lab_app.checkout_service import inspect_checkout

        try:
            target = pin_checkout(session, target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (CheckoutError, TargetError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return inspect_checkout(target).as_dict()

    @app.get("/api/checkouts")
    def list_lab_checkouts(session: Session = Depends(get_session)):
        """Every clone already sitting under data/checkouts, and who claims it.

        A repository cloned once should be auditable twice. This is what the
        onboarding offers instead of asking for a second copy when a clone
        exists under a name that is not the target's id.
        """
        from harness_lab_app.checkout_service import (
            list_checkouts,
            recorded_checkout_path,
        )

        claimed: dict[str, list[str]] = {}
        for target in TargetService(session).list_targets(include_archived=True):
            path = recorded_checkout_path(target)
            if path is not None:
                claimed.setdefault(path.name, []).append(target.target_id)
        rows = list_checkouts()
        for row in rows:
            row["claimed_by"] = claimed.get(str(row["name"]), [])
        return rows

    @app.post("/api/targets/{target_id}/checkout/attach")
    def attach_target_checkout(
        target_id: str,
        data: CheckoutAttachRequest,
        session: Session = Depends(get_session),
    ):
        """Point this target at a clone that already exists (ADR-0045)."""
        from harness_lab_app.checkout_service import inspect_checkout
        from harness_lab_app.target_service import attach_checkout

        try:
            target = attach_checkout(session, target_id, data.name)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (CheckoutError, TargetError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return inspect_checkout(target).as_dict()

    @app.post("/api/targets/{target_id}/checkout/local-command")
    def local_checkout_command(
        target_id: str,
        data: CheckoutFromLocalRequest,
        session: Session = Depends(get_session),
    ):
        """The command that copies a repository the operator already has.

        Built here rather than in the browser because the destination is a host
        path only the server can work out, and because the source has to be
        quoted for a shell.
        """
        from harness_lab_app.checkout_service import local_copy_command

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            command = local_copy_command(target.target_id, data.source)
        except CheckoutError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"target_id": target.target_id, "command": command}

    @app.get("/api/local-sources")
    def browse_local_sources(path: str = ""):
        """One level of the operator's own repositories, if they mounted any.

        Off unless HARNESS_LAB_LOCAL_SOURCES_DIR is set: the lab lists nothing
        it was not explicitly pointed at (ADR-0045).
        """
        from harness_lab_app.local_sources_service import (
            LocalSourcesDisabled,
            LocalSourcesError,
            browse,
        )

        try:
            return browse(path)
        except LocalSourcesDisabled as exc:
            return {"enabled": False, "reason": str(exc), "entries": []}
        except LocalSourcesError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/targets/{target_id}/checkout/from-local")
    def copy_local_checkout(
        target_id: str,
        data: CheckoutFromLocalRequest,
        session: Session = Depends(get_session),
    ):
        """Copy a mounted local repository into the lab's own checkout folder."""
        from harness_lab_app.checkout_service import (
            create_checkout_from,
            inspect_checkout,
        )
        from harness_lab_app.local_sources_service import LocalSourcesError, resolve

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            source = resolve(data.source)
            create_checkout_from(source, target)
        except (LocalSourcesError, CheckoutError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return inspect_checkout(target).as_dict()

    @app.post("/api/targets/{target_id}/gate-check", status_code=202)
    def start_gate_check(target_id: str, session: Session = Depends(get_session)):
        """Measure the repository alone: its own gate, at the pin, with no agent.

        Without it the first red run is ambiguous — the agent may have failed,
        or the repository may simply not build in the runner image — and those
        mean opposite things (ADR-0042).
        """
        import uuid

        from harness_lab_app.gate_check_service import (
            GateCheckError,
            build_gate_check_spec,
        )
        from harness_lab_app.models import GateCheck

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            spec = build_gate_check_spec(target)
        except (GateCheckError, CheckoutError, TargetError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        row = GateCheck(
            gate_check_id=f"gate-{target.target_id}-{uuid.uuid4().hex[:8]}",
            target_id=target.target_id,
            commit=spec["commit"],
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        try:
            enqueue_gate_check(row.gate_check_id)
        except Exception as exc:
            row.status = "failed"
            row.error = f"Could not queue the gate check: {exc!r}"
            session.add(row)
            session.commit()
            raise HTTPException(status_code=503, detail=row.error) from exc
        return row

    @app.get("/api/targets/{target_id}/gate-check")
    def latest_gate_check(target_id: str, session: Session = Depends(get_session)):
        """The most recent gate check for this repository, or nothing."""
        from harness_lab_app.models import GateCheck

        row = session.exec(
            select(GateCheck)
            .where(GateCheck.target_id == target_id)
            .order_by(GateCheck.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        return row

    @app.get("/api/targets/{target_id}/readiness")
    def target_readiness(target_id: str, session: Session = Depends(get_session)):
        """Step 2 of the guided audit: what this repository already has.

        Reads the repository rather than the lab's expectations of it. Anything
        it cannot observe is reported as "Not detected", never inferred — an
        audit whose opening page overstates what a client has is worse than no
        audit at all.
        """
        from harness_lab_app.readiness_service import ReadinessService

        try:
            return ReadinessService(session).build(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/targets/{target_id}/repository-audit")
    def get_repository_audit(target_id: str, session: Session = Depends(get_session)):
        """The LLM harness audit of this repository, if one was generated."""
        from harness_lab_app.repository_audit_service import RepositoryAuditService

        audit = RepositoryAuditService(session).get(target_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="No audit for this repository")
        return audit

    @app.post("/api/targets/{target_id}/repository-audit", status_code=202)
    def run_repository_audit(
        target_id: str,
        auditor_model: str | None = None,
        session: Session = Depends(get_session),
    ):
        """Queue the LLM harness audit of a pinned repository.

        Refused without a pin: the audit reads one commit and says which, and
        a report about a moving branch could not be re-read a week later. Runs
        in the worker like every other LLM reading; the UI polls the GET.
        """
        from harness_lab_app.repository_audit_service import RepositoryAuditService

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not (target.pinned_commit or "").strip():
            raise HTTPException(
                status_code=409,
                detail=(
                    "This repository is not pinned to a commit yet. Pin its "
                    "checkout in step 1, then audit it."
                ),
            )
        service = RepositoryAuditService(session)
        service.mark_running(target_id, auditor_model or "", target.pinned_commit)
        try:
            enqueue_repository_audit(target_id, auditor_model)
        except Exception as exc:
            service.store_error(target_id, f"could not queue the audit: {exc}")
            raise HTTPException(
                status_code=503,
                detail=f"The worker queue is unreachable: {exc}",
            ) from exc
        return {"status": "queued", "target_id": target_id}

    @app.get("/api/targets/{target_id}/audit-report")
    def target_audit_report(target_id: str, session: Session = Depends(get_session)):
        """The audit as a client-facing markdown document.

        Assembled live from what the lab holds — readiness, derived cases,
        experiments, accepted findings — so it can never say more than the
        evidence does. Regenerating after more runs simply says more.
        """
        from harness_lab_app.audit_report import build_report
        from harness_lab_app.readiness_service import ReadinessService

        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        readiness = ReadinessService(session).build(target_id)
        return {
            "target_id": target_id,
            "markdown": build_report(session, target, readiness),
        }

    @app.post("/api/targets/{target_id}/resolve")
    def resolve_target_ref(
        target_id: str,
        data: TargetResolveRequest,
        session: Session = Depends(get_session),
    ):
        """Resolve a ref to a commit without creating anything.

        Powers the run form's "check" affordance: the operator sees the exact
        commit the run would be pinned to *before* spending a container on it,
        and an unreachable repository or a typo'd branch is reported here
        instead of surfacing as a failed run.
        """
        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ref = (data.ref or target.default_ref or "").strip()
        try:
            commit = resolve_ref(clone_source(target), ref)
        except TargetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"target_id": target.target_id, "ref": ref, "commit": commit}

    @app.post("/api/targets/{target_id}/derive-cases")
    def derive_target_cases(
        target_id: str,
        limit: int = 20,
        scan: int = 300,
        ref: str = "",
        session: Session = Depends(get_session),
    ):
        """Mine candidate cases from the repository's own commit history.

        Nothing is persisted: candidates are proposals for a human to read,
        edit and accept. A derived case is only worth running if the team
        recognises it as their work, and that judgement is not the lab's to
        make.
        """
        try:
            target = TargetService(session).get_target(target_id)
        except TargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            candidates = derive_candidates(
                clone_source(target),
                ref or target.default_ref,
                get_settings().target_mirrors_dir,
                target.target_id,
                scan=max(1, min(scan, 2000)),
                limit=max(1, min(limit, 100)),
            )
        except TargetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "target_id": target.target_id,
            "ref": ref or target.default_ref,
            "scanned": scan,
            "candidates": [candidate.as_dict() for candidate in candidates],
        }

    @app.get("/api/cases")
    def list_cases(origin_target_id: str = "", session: Session = Depends(get_session)):
        """Every case, or the library of one repository (ADR-0042)."""
        cases = CaseService(session).list_cases()
        if origin_target_id:
            cases = [
                case for case in cases if case.origin_target_id == origin_target_id
            ]
        return cases

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, session: Session = Depends(get_session)):
        try:
            return CaseService(session).get_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases")
    def create_case(data: CaseCreate, session: Session = Depends(get_session)):
        try:
            return CaseService(session).create_case(data)
        except CaseExistsError as exc:
            # 409, so a client can treat "already there" as the non-event it is.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CaseValidationError as exc:
            # 422 with one message per offending command: the person is still on
            # the form and each message names the command and the fix.
            raise HTTPException(status_code=422, detail=exc.problems) from exc

    @app.patch("/api/cases/{case_id}")
    def update_case(
        case_id: str, data: CaseCreate, session: Session = Depends(get_session)
    ):
        # These four are driven by the case editor in the browser, so an
        # unknown id is an ordinary 404, not a 500 traceback in the panel.
        try:
            return CaseService(session).update_case(case_id, data)
        except CaseValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.problems) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases/{case_id}/dry-run")
    def dry_run_case(case_id: str, session: Session = Depends(get_session)):
        """Run this case's own checks against the clean, pinned tree.

        A check that passes here passed before the agent started, so its green
        says nothing about the agent. The verdict lands on the case as
        `metadata_json["dry_run"]`; `verdict == "tautological"` means the case
        cannot tell work from no work and has to be re-authored.
        """
        from harness_lab_app.celery_app import enqueue_case_dry_run
        from harness_lab_app.validation import is_executable_command

        try:
            case = CaseService(session).get_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not [c for c in case.validation_commands if is_executable_command(c)]:
            raise HTTPException(
                status_code=422,
                detail="This case has no runnable validation commands to dry-run.",
            )
        enqueue_case_dry_run(case_id)
        return {"status": "queued", "case_id": case_id}

    @app.delete("/api/cases/{case_id}")
    def delete_case(case_id: str, session: Session = Depends(get_session)):
        try:
            return CaseService(session).delete_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases/{case_id}/clone")
    def clone_case(
        case_id: str,
        new_case_id: str,
        new_name: str,
        session: Session = Depends(get_session),
    ):
        try:
            return CaseService(session).clone_case(case_id, new_case_id, new_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases/{case_id}/sync")
    def sync_case(case_id: str, session: Session = Depends(get_session)):
        try:
            path = CaseService(session).sync_case_to_yaml(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"task_path": str(path)}

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

    @app.post("/api/cases/seed")
    def seed_benchmarks(session: Session = Depends(get_session)):
        """Import or refresh every benchmarks/*/task.yaml case."""
        return CaseService(session).seed_benchmark_cases()

    @app.get("/api/cases/{case_id}/run-ids")
    def list_case_run_ids(case_id: str, session: Session = Depends(get_session)):
        return {"run_ids": CaseService(session).list_case_run_ids(case_id)}

    @app.get("/api/cases/{case_id}/prompt-content")
    def get_case_prompt_content(
        case_id: str, variant: str, session: Session = Depends(get_session)
    ):
        """The literal text behind a prompt variant (New evaluation's "View
        prompt" preview) — resolving a ``task.yaml``-style path to file
        content, same as the runner does at launch time."""
        try:
            case = CaseService(session).get_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        content = resolve_prompt_variant_text(case, variant)
        if content is None:
            raise HTTPException(
                status_code=404,
                detail=f"Prompt variant {variant!r} has no readable content",
            )
        return {"variant": variant, "content": content}

    @app.post("/api/cases/generate-draft", response_model=CaseDraftResponse)
    def generate_case_draft_endpoint(req: CaseDraftRequest) -> CaseDraftResponse:
        try:
            return generate_case_draft(req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs")
    def list_runs(batch_id: str = "", session: Session = Depends(get_session)):
        """Runs, each carrying the objective verdict the lab already computed.

        Without it a run whose produced code does not even start looks exactly
        like one waiting to be reviewed: both sit in ``awaiting_manual_score``
        with an empty score. The smoke check has already answered the question
        — runs K-035/K-037/K-039 failed it with exit 124, 1 and 2 — so the list
        shows the answer instead of asking someone to open each run to find it.
        """
        from harness_lab_app.models import Validation
        from harness_lab_app.run_failures import classify_failure, failure_label

        runs_dir = get_settings().data_runs_dir
        verdicts = {
            row.run_id: (row.objective or {}).get("passed")
            for row in session.exec(select(Validation)).all()
        }
        return [
            {
                **run.model_dump(),
                "improvements": _improvements_status(runs_dir, run.run_id),
                # True/False from the automated checks; None when they have not
                # run or the case has no objective criterion.
                "objective_passed": verdicts.get(run.run_id),
                # What kind of failure this was, so the list can separate "the
                # lab broke" from "the agent did badly" without re-parsing the
                # reason string in the browser.
                "failure_kind": classify_failure(run.failure_reason),
                "failure_label": failure_label(run.failure_reason),
            }
            for run in RunService(session).list_runs(batch_id=batch_id or None)
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

    @app.post("/api/runs/batch", status_code=201)
    def create_run_batch(
        data: RunBatchCreate, session: Session = Depends(get_session)
    ) -> RunBatchRead:
        """Queue the cross-product of the requested axes as one batch.

        All-or-nothing: a preflight failure on any combination rejects the whole
        request with 422 and persists nothing, so the caller never has to unpick
        a partially-created batch.
        """
        try:
            batch_id, runs = RunService(session, enqueue=enqueue_run).create_batch(data)
        except RunBatchTooLargeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RunPreflightError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RunBatchRead(
            batch_id=batch_id,
            count=len(runs),
            run_ids=[run.run_id for run in runs],
        )

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

    @app.get("/api/runs/{run_id}/context")
    def get_run_context(run_id: str, session: Session = Depends(get_session)):
        """What condition this run *was* — the questions asked before the logs.

        A run page that opens with stdout answers "what happened inside the
        container?". Before that is worth reading, a person needs to know which
        experiment this arm belongs to, which repository at which commit it
        worked on, and whose governance the agent could see. Those live across
        three tables; composing them here keeps the frontend from having to
        know that.
        """
        from harness_lab_app.models import Case, Experiment, TargetRepository

        service = RunService(session)
        try:
            run = service.get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        config = service.get_run_config(run.run_config_id)

        experiment = None
        if run.experiment_id:
            experiment = session.exec(
                select(Experiment).where(Experiment.experiment_id == run.experiment_id)
            ).first()

        case = session.exec(select(Case).where(Case.case_id == config.case_id)).first()
        target = None
        if config.target_repo_id:
            target = session.exec(
                select(TargetRepository).where(
                    TargetRepository.target_id == config.target_repo_id
                )
            ).first()

        return {
            "run_id": run.run_id,
            "experiment_id": run.experiment_id or "",
            "experiment_question": experiment.question if experiment else "",
            "experiment_mode": experiment.mode.value if experiment else "",
            "condition_label": run.condition_label or "",
            "case_id": config.case_id,
            "case_name": case.name if case else "",
            "prompt_variant": config.prompt_variant,
            "harness": config.harness,
            # The condition ADR-0031 makes a first-class axis: whether the agent
            # saw the target repository's own setup or the one the lab injected.
            "harness_source": config.harness_source.value,
            "executor_model": config.executor_model or "",
            "planner_model": config.planner_model or "",
            "target_repo_id": config.target_repo_id,
            "target_name": target.name if target else "",
            "target_url": target.repository_url if target else "",
            "target_ref": config.target_ref,
            # The immutable fact. The ref can move; this is what makes the run
            # reproducible (ADR-0029).
            "target_commit": config.target_commit,
            "validation_commands": (
                list(target.validation_commands)
                if target and target.validation_commands
                else list(case.validation_commands)
                if case
                else []
            ),
            "max_steps": config.max_steps,
            "parent_run_id": run.parent_run_id or "",
        }

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

    @app.get("/api/runs/{run_id}/langfuse")
    def get_langfuse(run_id: str, session: Session = Depends(get_session)):
        """Tracing status for this run: where to look, or why there is nothing."""
        return ObservabilityService(session).status(run_id)

    @app.post("/api/runs/{run_id}/langfuse-export")
    def export_langfuse(run_id: str, session: Session = Depends(get_session)):
        # Re-exporting is safe: span ids are derived from the run id, so a
        # second export updates the same session instead of duplicating it.
        result = ObservabilityService(session).export(run_id)
        return {
            "run_id": result.run_id,
            "session_id": result.session_id,
            "session_url": result.session_url,
            "ok": result.ok,
            "spans": result.spans,
            "traces": result.traces,
            "reason": result.reason,
        }

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

        settings = get_settings()
        run_dir = settings.data_runs_dir / run_id
        try:
            markdown, form = drafting.synthesize_issue(
                run_dir,
                repository=settings.template_repository,
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
            "repository": settings.template_repository,
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
        repository = get_settings().template_repository
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

    @app.post("/api/runs/{run_id}/continue")
    def continue_run(
        run_id: str,
        payload: RunContinueRequest,
        session: Session = Depends(get_session),
    ):
        """Queue a child run seeded from this run's workspace (ADR-0026)."""
        try:
            return RunService(session, enqueue=enqueue_run).continue_run(
                run_id, payload.prompt, payload.max_steps
            )
        except RunContinueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RunPreflightError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
