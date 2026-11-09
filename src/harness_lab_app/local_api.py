"""HTTP surface for *Tune your local model* (spec 2026-11-08).

An ``APIRouter`` kept separate from the monolithic ``api.py``: the page is a
self-contained feature with its own pydantic request models, so splitting it
out avoids inflating the file that already holds the rest of the lab's
routes. Wired into ``create_app`` with one ``app.include_router(local_router)``
line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from harness_lab_app.config import read_env_file_value
from harness_lab_app.db import get_session
from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.local_tuning.contracts import (
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
    ServerError,
    ServerKind,
)
from harness_lab_app.local_tuning.host_guard import (
    active_runs_on_host,
    model_load_check,
    record_override,
)
from harness_lab_app.local_tuning.profile_service import ProfileService
from harness_lab_app.local_tuning.test_sessions import (
    SessionBusy,
    get_session as get_test_session,
    start_session,
)

if TYPE_CHECKING:
    from sqlmodel import Session

local_router = APIRouter(prefix="/api/local")


def _gateway_routing() -> tuple[dict, bool]:
    """Routing table for the host guard, mirroring ``api._gateway_endpoint``.

    Lazily imported so this module (and its tests) never pay for the gateway
    client unless a route actually needs routing. Returns ``(routing,
    routing_unknown)``: ``routing_unknown`` is true when the gateway is
    configured (``GATEWAY_BASE_URL``/``GATEWAY_TOKEN`` both set) but the fetch
    came back with nothing — ``fetch_gateway_routing_cached`` swallows its own
    network errors and returns ``{}`` either way, so an empty table from a
    configured gateway is the only outward signal that the fetch failed
    (spec item 7: a gateway run's host cannot be checked in that case).
    """
    from pathlib import Path

    from harness_lab_app.config import get_settings
    from harness_lab_app.executors import rewrite_localhost
    from harness_lab_app.gateway_models import fetch_gateway_routing_cached

    base = read_env_file_value("GATEWAY_BASE_URL")
    token = read_env_file_value("GATEWAY_TOKEN")
    if not base or not token:
        return {}, False
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    routing = fetch_gateway_routing_cached(base, token)
    return routing, not routing


def _unreachable_detail(url: str) -> str:
    return (
        f"Could not reach {url}. Usual causes: the server is off, LM Studio "
        "is not serving on the local network, a firewall is blocking the "
        "port, or the port is wrong."
    )


class MachineCreateRequest(BaseModel):
    name: str
    memory_kind: str = "discrete"
    gpu_gb: float = 0.0
    ram_gb: float = 0.0
    reserve_gb: float = 4.0
    gpu_wired_fraction: float = 0.75
    bandwidth_gbs: float | None = None
    notes: str = ""


class MachineUpdateRequest(BaseModel):
    name: str | None = None
    memory_kind: str | None = None
    gpu_gb: float | None = None
    ram_gb: float | None = None
    reserve_gb: float | None = None
    gpu_wired_fraction: float | None = None
    bandwidth_gbs: float | None = None
    notes: str | None = None


class ServerTestRequest(BaseModel):
    kind: ServerKind
    url: str


class EstimateRequest(BaseModel):
    model: ModelInfo
    config: LoadConfig
    machine: MachineSpec


class GuardRequest(BaseModel):
    kind: ServerKind
    url: str


class LoadRequest(BaseModel):
    kind: ServerKind
    url: str
    model_id: str
    config: LoadConfig
    acknowledge_guard: bool = False
    # Each LM Studio load adds an instance; it never reconfigures one in place.
    # With the model already loaded, `replace` unloads those instances first.
    replace: bool = False


class UnloadRequest(BaseModel):
    kind: ServerKind
    url: str
    model_id: str
    instance_id: str | None = None
    force: bool = False
    acknowledge_guard: bool = False


class TestsStartRequest(BaseModel):
    kind: ServerKind
    url: str
    model: ModelInfo
    config: LoadConfig
    machine: MachineSpec
    served: ServedConfig | None = None
    acknowledge_guard: bool = False
    # When set, the finished session is saved to this machine's history
    # automatically, so two configurations can be compared later.
    machine_id: int | None = None
    estimate: dict | None = None
    # "I load the model myself" (a Jetson, a box someone else runs): test the
    # model exactly as it is served. The lab never loads, reloads or reconfigures
    # it, and refuses when it is not loaded rather than loading it.
    evaluate_only: bool = False


class ProfileCreateRequest(BaseModel):
    machine_id: int
    server_kind: ServerKind
    server_url: str
    model_id: str
    label: str = ""
    requested_config: dict = {}
    served_config: dict = {}
    estimate: dict = {}
    results: list = []
    observed_memory_gb: float | None = None
    server_version: str = ""
    recommended: bool = False


class ProfileUpdateRequest(BaseModel):
    label: str | None = None
    recommended: bool | None = None
    observed_memory_gb: float | None = None


def _host_of(url: str) -> str:
    """Normalized host a server URL resolves to, for the guard report.

    Raises :class:`fastapi.HTTPException` (422) for a URL ``urlsplit`` itself
    rejects (e.g. a malformed IPv6 literal) — every endpoint taking a ``url``
    must answer with a clear message rather than a 500 (spec item 4).
    """
    from harness_lab_app.local_tuning.host_guard import _normalize_host

    try:
        host = _normalize_host(url)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"{url!r} is not a valid URL: {exc}"
        ) from exc
    return host or url


def _validate_url(url: str) -> None:
    """422 on a URL that fails the server-URL allowlist (spec item 5)."""
    from harness_lab_app.local_tuning.servers import (
        InvalidServerUrl,
        validate_server_url,
    )

    try:
        validate_server_url(url)
    except InvalidServerUrl as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _check_guard(
    session: Session,
    url: str,
    action: str,  # noqa: ARG001 - kept for parity with _record_override_after_success
    acknowledge_guard: bool,
):
    """Raise 409 with the guard report when it is not clear and unacknowledged.

    Returns the ``(host, report)`` pair. The override is *not* recorded here:
    the spec requires the ``RunEvent`` to land only after the action it
    warns about actually went through, never on a guard that was merely
    acknowledged and then failed with a 502/422/409 (spec item 8) — so
    callers must call :func:`_record_override_after_success` themselves once
    their own action has returned successfully. ``action`` stays a parameter
    (unused here) so every call site reads symmetrically with that function.
    """
    _validate_url(url)
    routing, routing_unknown = _gateway_routing()
    host = _host_of(url)
    report = active_runs_on_host(session, host, routing)
    report.routing_unknown = routing_unknown
    if not report.clear and not acknowledge_guard:
        raise HTTPException(status_code=409, detail={"guard": report.model_dump()})
    return host, report


def _record_override_after_success(
    session: Session, report, action: str, acknowledge_guard: bool
) -> None:
    """Write the "Continue anyway" warning, but only once the action succeeded."""
    if acknowledge_guard and not report.clear:
        record_override(session, report, action)


@local_router.get("/reference")
def get_reference():
    from harness_lab_app.local_tuning.catalog import (
        CONTEXT_OPTIONS,
        DEFAULT_LOAD,
        EVAL_BATCH_OPTIONS,
        KV_CACHE_TYPES,
        REFERENCE_MACHINES,
    )

    return {
        "machines": REFERENCE_MACHINES,
        "context_options": CONTEXT_OPTIONS,
        "eval_batch_options": EVAL_BATCH_OPTIONS,
        "kv_cache_types": KV_CACHE_TYPES,
        "default_load": DEFAULT_LOAD.model_dump(),
        "defaults": {
            "lmstudio_url": read_env_file_value("LMSTUDIO_BASE_URL")
            or "http://localhost:1234",
            "ollama_url": read_env_file_value("OLLAMA_BASE_URL")
            or "http://localhost:11434",
        },
    }


@local_router.get("/machines")
def list_machines(session: Session = Depends(get_session)):
    return ProfileService(session).list_machines()


@local_router.post("/machines")
def create_machine(data: MachineCreateRequest, session: Session = Depends(get_session)):
    return ProfileService(session).create_machine(data.model_dump())


@local_router.put("/machines/{machine_id}")
def update_machine(
    machine_id: int,
    data: MachineUpdateRequest,
    session: Session = Depends(get_session),
):
    updated = ProfileService(session).update_machine(
        machine_id, data.model_dump(exclude_none=True)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return updated


@local_router.delete("/machines/{machine_id}")
def delete_machine(machine_id: int, session: Session = Depends(get_session)):
    if not ProfileService(session).delete_machine(machine_id):
        raise HTTPException(status_code=404, detail="Machine not found")
    return {"deleted": True}


@local_router.post("/servers/test")
def test_server(data: ServerTestRequest):
    from harness_lab_app.local_tuning.servers import InvalidServerUrl, make_server

    try:
        server = make_server(data.kind, data.url)
        models = server.list_models()
        return {
            "kind": data.kind,
            "url": data.url,
            "reachable": True,
            "models": [m.model_dump() for m in models],
            "error": "",
        }
    except (DirectHostUnreachable, ServerError, InvalidServerUrl) as exc:
        return {
            "kind": data.kind,
            "url": data.url,
            "reachable": False,
            "models": [],
            "error": str(exc),
        }


@local_router.post("/estimate")
def estimate(data: EstimateRequest):
    from harness_lab_app.local_tuning.memory_estimate import estimate_memory

    return estimate_memory(data.model, data.config, data.machine)


@local_router.post("/guard")
def guard(data: GuardRequest, session: Session = Depends(get_session)):
    _validate_url(data.url)
    routing, routing_unknown = _gateway_routing()
    host = _host_of(data.url)
    report = active_runs_on_host(session, host, routing)
    report.routing_unknown = routing_unknown
    return report.model_dump()


@local_router.post("/load")
def load(data: LoadRequest, session: Session = Depends(get_session)):
    from harness_lab_app.local_tuning.servers import (
        diff_config,
        make_server,
        unsupported_settings,
    )

    _, report = _check_guard(
        session, data.url, f"load {data.model_id}", data.acknowledge_guard
    )
    try:
        server = make_server(data.kind, data.url)
        existing = _loaded_instance(server, data.model_id)
        if existing is not None and not data.replace:
            if not diff_config(data.config, existing.served or ServedConfig()):
                return {
                    "served": existing.served,
                    "diff": [],
                    "unsupported": unsupported_settings(data.kind, data.config),
                    "reused": True,
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "loaded_instances": existing.instance_ids,
                    "served": (existing.served or ServedConfig()).model_dump(),
                    "message": (
                        f"{data.model_id} is already loaded with a different "
                        "configuration. Loading again would keep both copies in "
                        "memory; replace it to unload the current one first."
                    ),
                },
            )
        if existing is not None:
            for instance_id in existing.instance_ids:
                server.unload(data.model_id, instance_id)
        served = server.load(data.model_id, data.config)
    except DirectHostUnreachable as exc:
        raise HTTPException(
            status_code=502, detail=_unreachable_detail(data.url)
        ) from exc
    except ServerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_override_after_success(
        session, report, f"load {data.model_id}", data.acknowledge_guard
    )
    return {
        "served": served,
        "diff": diff_config(data.config, served),
        "unsupported": unsupported_settings(data.kind, data.config),
        "reused": False,
    }


def _loaded_instance(server, model_id: str) -> ModelInfo | None:
    """The model's listing entry when the server already holds it loaded.

    Ollama serves one runner per model and reloads it in place, so only LM
    Studio, which adds an instance per load, can end up with duplicate copies.
    """
    if getattr(server, "kind", None) != "lmstudio":
        return None
    try:
        models = server.list_models()
    except (DirectHostUnreachable, ServerError):
        # The load below reports the host's state with its own error.
        return None
    model = next((m for m in models if m.id == model_id), None)
    if model is None or not model.loaded or not model.instance_ids:
        return None
    return model


@local_router.post("/unload")
def unload(data: UnloadRequest, session: Session = Depends(get_session)):
    from harness_lab_app.local_tuning.servers import make_server

    _, report = _check_guard(
        session, data.url, f"unload {data.model_id}", data.acknowledge_guard
    )
    try:
        server = make_server(data.kind, data.url)
        if data.instance_id is None and not data.force:
            model = next(
                (m for m in server.list_models() if m.id == data.model_id), None
            )
            instances = model.instance_ids if model is not None else []
            if len(instances) > 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "instances": instances,
                        "message": (
                            f"{data.model_id} has {len(instances)} loaded "
                            "instances; pick one to unload, or pass force=true "
                            "to unload them all."
                        ),
                    },
                )
        server.unload(data.model_id, data.instance_id)
    except DirectHostUnreachable as exc:
        raise HTTPException(
            status_code=502, detail=_unreachable_detail(data.url)
        ) from exc
    except ServerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_override_after_success(
        session, report, f"unload {data.model_id}", data.acknowledge_guard
    )
    return {"unloaded": True}


@local_router.post("/tests")
def start_tests(data: TestsStartRequest, session: Session = Depends(get_session)):
    from harness_lab_app.local_tuning.servers import make_server

    _, report = _check_guard(session, data.url, "tests", data.acknowledge_guard)
    try:
        server = make_server(data.kind, data.url)
    except DirectHostUnreachable as exc:
        raise HTTPException(
            status_code=502, detail=_unreachable_detail(data.url)
        ) from exc
    config, served = data.config, data.served
    if data.evaluate_only:
        config, served = _as_served(server, data)
    try:
        result = start_session(
            server,
            data.model,
            config,
            data.machine,
            served=served,
            on_done=(
                _history_saver(
                    data.model_copy(update={"config": config, "served": served})
                )
                if data.machine_id is not None
                else None
            ),
        )
    except SessionBusy as exc:
        raise HTTPException(
            status_code=409, detail={"session": exc.existing.model_dump()}
        ) from exc
    _record_override_after_success(session, report, "tests", data.acknowledge_guard)
    return result


def _as_served(server, data: TestsStartRequest) -> tuple[LoadConfig, ServedConfig]:
    """The configuration the model is running with right now, for evaluate-only.

    Every test request then repeats exactly what the server already holds. On
    Ollama a request whose options differ (context, batch) reloads the runner,
    so the batch is left unset and the context is the served one.
    """
    try:
        models = server.list_models()
    except (DirectHostUnreachable, ServerError) as exc:
        raise HTTPException(
            status_code=502, detail=_unreachable_detail(data.url)
        ) from exc
    current = next((m for m in models if m.id == data.model.id), None)
    if current is None or not current.loaded or current.served is None:
        raise HTTPException(
            status_code=409,
            detail={
                "not_loaded": data.model.id,
                "message": (
                    f"{data.model.id} is not loaded on {data.url}. In evaluate-only "
                    "mode the lab never loads a model: start it on the device "
                    "(e.g. `ollama run <model>` or LM Studio's load button), then "
                    "test again."
                ),
            },
        )
    served = current.served
    config = data.config.model_copy(
        update={
            "context_length": served.context_length or data.config.context_length,
            "eval_batch_size": served.eval_batch_size,
            "flash_attention": served.flash_attention,
            "offload_kv_cache_to_gpu": served.offload_kv_cache_to_gpu,
        }
    )
    return config, served


def _history_saver(data: TestsStartRequest):
    """A callback that files a finished session as a profile, in its own DB session.

    It runs on the test thread after the request has returned, so it cannot use
    the request's session.
    """
    from sqlmodel import Session as DbSession

    from harness_lab_app.db import get_engine
    from harness_lab_app.local_tuning.history import auto_label

    def save(finished) -> int | None:
        with DbSession(get_engine()) as db:
            profile = ProfileService(db).create_profile(
                {
                    "machine_id": data.machine_id,
                    "server_kind": data.kind,
                    "server_url": data.url,
                    "model_id": data.model.id,
                    "label": auto_label(data.model.id, data.config, data.served),
                    "requested_config": data.config.model_dump(),
                    "served_config": (
                        data.served.model_dump() if data.served is not None else {}
                    ),
                    "estimate": data.estimate or {},
                    "results": [r.model_dump() for r in finished.results],
                }
            )
            return profile.id

    return save


@local_router.get("/tests/{session_id}")
def get_tests(session_id: str):
    row = get_test_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Test session not found")
    return row


@local_router.get("/reference-devices")
def reference_devices():
    """Published speeds as devices x models, each with its bandwidth ceiling."""
    from harness_lab_app.local_tuning.reference_benchmarks import reference_payload

    return reference_payload()


@local_router.get("/results")
def results(session: Session = Depends(get_session)):
    """Speed tests and local-model case runs, each ranked on its own terms."""
    from harness_lab_app.local_tuning.results_service import local_results

    return local_results(session)


@local_router.get("/history")
def history(
    machine_id: int,
    model_id: str | None = None,
    session: Session = Depends(get_session),
):
    """A machine's saved test runs with the numbers that tell them apart."""
    return ProfileService(session).history(machine_id, model_id)


@local_router.get("/profiles")
def list_profiles(
    machine_id: int | None = None,
    model_id: str | None = None,
    session: Session = Depends(get_session),
):
    return ProfileService(session).list_profiles(
        machine_id=machine_id, model_id=model_id
    )


@local_router.post("/profiles")
def create_profile(data: ProfileCreateRequest, session: Session = Depends(get_session)):
    return ProfileService(session).create_profile(data.model_dump())


@local_router.get("/profiles/compare")
def compare_profiles(a: int, b: int, session: Session = Depends(get_session)):
    return ProfileService(session).compare(a, b)


@local_router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = ProfileService(session).get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@local_router.patch("/profiles/{profile_id}")
def update_profile(
    profile_id: int,
    data: ProfileUpdateRequest,
    session: Session = Depends(get_session),
):
    updated = ProfileService(session).update_profile(
        profile_id,
        label=data.label,
        recommended=data.recommended,
        observed_memory_gb=data.observed_memory_gb,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return updated


@local_router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_session)):
    if not ProfileService(session).delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"deleted": True}


@local_router.get("/model-load-check")
def model_load_check_endpoint(model: str, session: Session = Depends(get_session)):
    routing, routing_unknown = _gateway_routing()
    result = model_load_check(session, model, routing)
    guard = result.get("guard")
    if guard is not None:
        guard.routing_unknown = routing_unknown
    return result
