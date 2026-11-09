from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import local_api
from harness_lab_app.api import create_app
from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.local_api import local_router
from harness_lab_app.local_tuning.contracts import (
    GuardReport,
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
    ServerError,
)


def _route_endpoint(_app, path: str, method: str | None = None):
    """Look up ``local_router``'s own endpoint directly.

    ``create_app`` mounts ``local_router`` through ``include_router``, which
    (on this FastAPI version) wraps it in an ``_IncludedRouter`` rather than
    flattening its routes into ``app.router.routes`` — so route lookup goes
    straight to the router the feature owns, the same router ``api.py``
    mounts.
    """
    for route in local_router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is not None and method not in getattr(route, "methods", set()):
            continue
        return route.endpoint
    raise AssertionError(f"Route not found: {path} ({method})")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_reference_payload_shape():
    app = create_app()
    reference = _route_endpoint(app, "/api/local/reference", method="GET")
    payload = reference()
    assert "machines" in payload
    assert "context_options" in payload
    assert "eval_batch_options" in payload
    assert "kv_cache_types" in payload
    assert "default_load" in payload
    assert "defaults" in payload
    assert "lmstudio_url" in payload["defaults"]
    assert "ollama_url" in payload["defaults"]


def test_machines_and_profiles_round_trip():
    app = create_app()
    create_machine = _route_endpoint(app, "/api/local/machines", method="POST")
    create_profile = _route_endpoint(app, "/api/local/profiles", method="POST")
    list_profiles = _route_endpoint(app, "/api/local/profiles", method="GET")

    with _session() as session:
        machine = create_machine(
            local_api.MachineCreateRequest(name="M1", gpu_gb=24, ram_gb=64), session
        )
        profile = create_profile(
            local_api.ProfileCreateRequest(
                machine_id=machine.id,
                server_kind="lmstudio",
                server_url="http://localhost:1234",
                model_id="prism-ml/bonsai-27b",
            ),
            session,
        )
        profiles = list_profiles(machine_id=machine.id, model_id=None, session=session)
        assert [p.id for p in profiles] == [profile.id]


def test_load_returns_409_without_acknowledge(monkeypatch):
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    def fake_check_guard(_session, _url, _action, acknowledge_guard):
        from harness_lab_app.local_tuning.contracts import ActiveRunRef

        busy_report = GuardReport(
            host="http://localhost:1234",
            running=[ActiveRunRef(run_id="r-1", state="running", model="m")],
        )
        if not acknowledge_guard:
            raise HTTPException(
                status_code=409, detail={"guard": busy_report.model_dump()}
            )
        return "http://localhost:1234", busy_report

    monkeypatch.setattr(local_api, "_check_guard", fake_check_guard)

    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            load(
                local_api.LoadRequest(
                    kind="lmstudio",
                    url="http://localhost:1234",
                    model_id="m",
                    config=LoadConfig(context_length=8192),
                    acknowledge_guard=False,
                ),
                session,
            )
        assert exc.value.status_code == 409
        assert "guard" in exc.value.detail


class _FakeServer:
    kind = "lmstudio"
    base_url = "http://localhost:1234"

    def list_models(self):
        return []

    def load(self, model_id, config):
        return ServedConfig(context_length=config.context_length)

    def unload(self, model_id, instance_id=None):
        return None

    def complete(self, *args, **kwargs):
        raise NotImplementedError


def test_load_with_acknowledge_records_override(monkeypatch):
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    recorded = {}

    def fake_check_guard(session, url, action, acknowledge_guard):
        recorded["action"] = action
        recorded["acknowledge"] = acknowledge_guard
        return url, GuardReport(host=url)

    monkeypatch.setattr(local_api, "_check_guard", fake_check_guard)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _FakeServer(),
    )

    with _session() as session:
        result = load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=8192),
                acknowledge_guard=True,
            ),
            session,
        )
        assert result["served"].context_length == 8192
        assert recorded["acknowledge"] is True
        assert "load m" in recorded["action"]


def test_load_does_not_record_override_when_server_refuses(monkeypatch):
    """Item 8: the "Continue anyway" warning must land only after the action
    succeeds, never when the acknowledged load itself failed (422)."""
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    from harness_lab_app.local_tuning.contracts import ActiveRunRef

    busy_report = GuardReport(
        host="http://localhost:1234",
        running=[ActiveRunRef(run_id="r-1", state="running", model="m")],
    )
    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, busy_report),
    )

    recorded = []
    monkeypatch.setattr(
        "harness_lab_app.local_api.record_override",
        lambda _session, _report, action: recorded.append(action),
    )

    class _RefusingServer:
        def load(self, model_id, config):
            raise ServerError("model not found")

    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _RefusingServer(),
    )

    with _session() as session, pytest.raises(HTTPException):
        load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=8192),
                acknowledge_guard=True,
            ),
            session,
        )
    assert recorded == []


def test_load_records_override_only_after_server_accepts(monkeypatch):
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    from harness_lab_app.local_tuning.contracts import ActiveRunRef

    busy_report = GuardReport(
        host="http://localhost:1234",
        running=[ActiveRunRef(run_id="r-1", state="running", model="m")],
    )
    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, busy_report),
    )

    recorded = []
    monkeypatch.setattr(
        "harness_lab_app.local_api.record_override",
        lambda _session, _report, action: recorded.append(action),
    )
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _FakeServer(),
    )

    with _session() as session:
        load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=8192),
                acknowledge_guard=True,
            ),
            session,
        )
    assert recorded == ["load m"]


def test_load_returns_502_when_unreachable(monkeypatch):
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )

    class _UnreachableServer:
        def load(self, model_id, config):
            raise DirectHostUnreachable("no answer")

    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _UnreachableServer(),
    )

    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            load(
                local_api.LoadRequest(
                    kind="lmstudio",
                    url="http://localhost:1234",
                    model_id="m",
                    config=LoadConfig(context_length=8192),
                    acknowledge_guard=True,
                ),
                session,
            )
        assert exc.value.status_code == 502
        assert "1234" in exc.value.detail


def test_load_returns_422_on_server_error(monkeypatch):
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )

    class _RefusingServer:
        def load(self, model_id, config):
            raise ServerError("model not found")

    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _RefusingServer(),
    )

    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            load(
                local_api.LoadRequest(
                    kind="lmstudio",
                    url="http://localhost:1234",
                    model_id="m",
                    config=LoadConfig(context_length=8192),
                    acknowledge_guard=True,
                ),
                session,
            )
        assert exc.value.status_code == 422


class _TwoInstanceServer:
    kind = "lmstudio"
    base_url = "http://localhost:1234"

    def __init__(self):
        self.unload_calls: list[tuple[str, str | None]] = []

    def list_models(self):
        return [
            ModelInfo(
                id="m",
                loaded=True,
                instance_ids=["m:1", "m:2"],
            )
        ]

    def load(self, model_id, config):
        raise NotImplementedError

    def unload(self, model_id, instance_id=None):
        self.unload_calls.append((model_id, instance_id))

    def complete(self, *args, **kwargs):
        raise NotImplementedError


def test_unload_without_instance_id_409s_when_multiple_instances(monkeypatch):
    app = create_app()
    unload = _route_endpoint(app, "/api/local/unload", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )
    server = _TwoInstanceServer()
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: server,
    )

    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            unload(
                local_api.UnloadRequest(
                    kind="lmstudio", url="http://localhost:1234", model_id="m"
                ),
                session,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["instances"] == ["m:1", "m:2"]
    assert server.unload_calls == []


def test_unload_with_instance_id_skips_409_and_targets_one_instance(monkeypatch):
    app = create_app()
    unload = _route_endpoint(app, "/api/local/unload", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )
    server = _TwoInstanceServer()
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: server,
    )

    with _session() as session:
        result = unload(
            local_api.UnloadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                instance_id="m:1",
            ),
            session,
        )
        assert result == {"unloaded": True}
    assert server.unload_calls == [("m", "m:1")]


def test_unload_with_force_skips_409_and_unloads_all(monkeypatch):
    app = create_app()
    unload = _route_endpoint(app, "/api/local/unload", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )
    server = _TwoInstanceServer()
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: server,
    )

    with _session() as session:
        result = unload(
            local_api.UnloadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                force=True,
            ),
            session,
        )
        assert result == {"unloaded": True}
    assert server.unload_calls == [("m", None)]


def test_servers_test_returns_200_reachable_false_on_bad_url():
    app = create_app()
    test_server = _route_endpoint(app, "/api/local/servers/test", method="POST")

    result = test_server(
        local_api.ServerTestRequest(kind="lmstudio", url="http://localhost:1234/evil")
    )
    assert result["reachable"] is False
    assert result["error"]


def test_guard_rejects_invalid_url_with_422():
    app = create_app()
    guard = _route_endpoint(app, "/api/local/guard", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            guard(
                local_api.GuardRequest(
                    kind="lmstudio", url="http://localhost:1234?x=1"
                ),
                session,
            )
        assert exc.value.status_code == 422


def test_load_rejects_invalid_url_with_422():
    app = create_app()
    load = _route_endpoint(app, "/api/local/load", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            load(
                local_api.LoadRequest(
                    kind="lmstudio",
                    url="ftp://localhost:1234",
                    model_id="m",
                    config=LoadConfig(context_length=8192),
                ),
                session,
            )
        assert exc.value.status_code == 422


def test_unload_rejects_invalid_url_with_422():
    app = create_app()
    unload = _route_endpoint(app, "/api/local/unload", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            unload(
                local_api.UnloadRequest(
                    kind="lmstudio",
                    url="http://localhost:1234/foo",
                    model_id="m",
                ),
                session,
            )
        assert exc.value.status_code == 422


def test_tests_start_rejects_invalid_url_with_422():
    app = create_app()
    start_tests = _route_endpoint(app, "/api/local/tests", method="POST")
    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            start_tests(
                local_api.TestsStartRequest(
                    kind="lmstudio",
                    url="http://user:pass@localhost:1234",
                    model=ModelInfo(id="m", size_bytes=1024**3),
                    config=LoadConfig(context_length=8192),
                    machine=MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64),
                ),
                session,
            )
        assert exc.value.status_code == 422


def test_guard_endpoint_reports_clear_when_no_runs(monkeypatch):
    app = create_app()
    guard = _route_endpoint(app, "/api/local/guard", method="POST")
    monkeypatch.setattr(local_api, "_gateway_routing", lambda: ({}, False))
    with _session() as session:
        result = guard(
            local_api.GuardRequest(kind="lmstudio", url="http://localhost:1234"),
            session,
        )
        assert result["clear"] is True
        assert result["routing_unknown"] is False


def test_guard_endpoint_sets_routing_unknown_when_gateway_fetch_fails(monkeypatch):
    app = create_app()
    guard = _route_endpoint(app, "/api/local/guard", method="POST")
    monkeypatch.setattr(local_api, "_gateway_routing", lambda: ({}, True))
    with _session() as session:
        result = guard(
            local_api.GuardRequest(kind="lmstudio", url="http://localhost:1234"),
            session,
        )
        assert result["routing_unknown"] is True


def test_estimate_endpoint_delegates_to_memory_estimate(monkeypatch):
    app = create_app()
    estimate = _route_endpoint(app, "/api/local/estimate", method="POST")

    sentinel = {"ok": True}
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.memory_estimate.estimate_memory",
        lambda _model, _config, _machine: sentinel,
    )

    result = estimate(
        local_api.EstimateRequest(
            model=ModelInfo(id="m", size_bytes=1024**3),
            config=LoadConfig(context_length=8192),
            machine=MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64),
        )
    )
    assert result is sentinel


def test_tests_start_returns_409_when_busy(monkeypatch):
    app = create_app()
    start_tests = _route_endpoint(app, "/api/local/tests", method="POST")

    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda _session, url, _action, _ack: (url, GuardReport(host=url)),
    )
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda _kind, _url, **_kw: _FakeServer(),
    )

    from harness_lab_app.local_tuning.contracts import TestSession
    from harness_lab_app.local_tuning.test_sessions import SessionBusy

    def fake_start_session(*args, **kwargs):
        raise SessionBusy(
            TestSession(
                id="existing",
                server_url="http://localhost:1234",
                model_id="m",
                status="running",
            )
        )

    monkeypatch.setattr(local_api, "start_session", fake_start_session)

    with _session() as session:
        with pytest.raises(HTTPException) as exc:
            start_tests(
                local_api.TestsStartRequest(
                    kind="lmstudio",
                    url="http://localhost:1234",
                    model=ModelInfo(id="m", size_bytes=1024**3),
                    config=LoadConfig(context_length=8192),
                    machine=MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64),
                ),
                session,
            )
        assert exc.value.status_code == 409
        assert "session" in exc.value.detail


def test_get_tests_404_when_missing():
    app = create_app()
    get_tests = _route_endpoint(app, "/api/local/tests/{session_id}", method="GET")
    with pytest.raises(HTTPException) as exc:
        get_tests("missing")
    assert exc.value.status_code == 404


class _LoadedServer(_FakeServer):
    """An LM Studio that already holds `m` at 50k context (seen live, 2026-11-08:
    each load added another instance, leaving three copies in VRAM)."""

    def __init__(self):
        self.loads = []
        self.unloads = []

    def list_models(self):
        from harness_lab_app.local_tuning.contracts import ModelInfo

        return [
            ModelInfo(
                id="m",
                loaded=True,
                instance_ids=["m"],
                served=ServedConfig(context_length=50432, instance_id="m"),
            )
        ]

    def load(self, model_id, config):
        self.loads.append(config.context_length)
        return ServedConfig(context_length=config.context_length, instance_id="m:2")

    def unload(self, model_id, instance_id=None):
        self.unloads.append(instance_id)


def _clear_guard(monkeypatch):
    monkeypatch.setattr(
        local_api,
        "_check_guard",
        lambda *_a: (
            "http://localhost:1234",
            GuardReport(host="http://localhost:1234"),
        ),
    )


def test_loading_an_already_loaded_model_with_the_same_config_reuses_it(monkeypatch):
    server = _LoadedServer()
    _clear_guard(monkeypatch)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server", lambda *_a, **_k: server
    )
    load = _route_endpoint(create_app(), "/api/local/load", method="POST")
    with _session() as session:
        result = load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=50432),
            ),
            session,
        )
    assert server.loads == []
    assert result["served"].instance_id == "m"
    assert result["reused"] is True


def test_loading_an_already_loaded_model_with_another_config_asks_first(monkeypatch):
    server = _LoadedServer()
    _clear_guard(monkeypatch)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server", lambda *_a, **_k: server
    )
    load = _route_endpoint(create_app(), "/api/local/load", method="POST")
    with _session() as session, pytest.raises(HTTPException) as exc:
        load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=65536),
            ),
            session,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["loaded_instances"] == ["m"]
    assert server.loads == []


def test_replace_unloads_the_existing_instances_before_loading(monkeypatch):
    server = _LoadedServer()
    _clear_guard(monkeypatch)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server", lambda *_a, **_k: server
    )
    load = _route_endpoint(create_app(), "/api/local/load", method="POST")
    with _session() as session:
        load(
            local_api.LoadRequest(
                kind="lmstudio",
                url="http://localhost:1234",
                model_id="m",
                config=LoadConfig(context_length=65536),
                replace=True,
            ),
            session,
        )
    assert server.unloads == ["m"]
    assert server.loads == [65536]


class _ServingServer(_FakeServer):
    """A device whose model the user started by hand (e.g. a Jetson)."""

    kind = "ollama"

    def __init__(self, loaded=True):
        self._loaded = loaded
        self.loads = []

    def list_models(self):
        from harness_lab_app.local_tuning.contracts import ModelInfo

        return [
            ModelInfo(
                id="qwen3:4b",
                loaded=self._loaded,
                served=(
                    ServedConfig(context_length=16384, vram_bytes=5_418_202_240)
                    if self._loaded
                    else None
                ),
            )
        ]

    def load(self, model_id, config):
        self.loads.append(config)
        raise AssertionError("evaluate-only must never load")


def _tests_request(**over):
    from harness_lab_app.local_tuning.contracts import MachineSpec, ModelInfo

    data = {
        "kind": "ollama",
        "url": "http://192.168.0.130:11434",
        "model": ModelInfo(id="qwen3:4b"),
        "config": LoadConfig(context_length=32768, eval_batch_size=512),
        "machine": MachineSpec(memory_kind="unified", gpu_gb=8, ram_gb=8),
        "evaluate_only": True,
    }
    data.update(over)
    return local_api.TestsStartRequest(**data)


def test_evaluate_only_tests_the_served_config_and_never_loads(monkeypatch):
    server = _ServingServer()
    _clear_guard(monkeypatch)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server", lambda *_a, **_k: server
    )
    captured = {}

    def fake_start(srv, model, config, machine, **kwargs):
        captured["config"], captured["served"] = config, kwargs["served"]
        return {"id": "s"}

    monkeypatch.setattr(local_api, "start_session", fake_start)
    start = _route_endpoint(create_app(), "/api/local/tests", method="POST")
    with _session() as session:
        start(_tests_request(), session)
    assert captured["config"].context_length == 16384
    assert (
        captured["config"].eval_batch_size is None
    )  # Ollama would reload on a batch change
    assert captured["served"].vram_bytes == 5_418_202_240
    assert server.loads == []


def test_evaluate_only_refuses_a_model_that_is_not_loaded(monkeypatch):
    _clear_guard(monkeypatch)
    monkeypatch.setattr(
        "harness_lab_app.local_tuning.servers.make_server",
        lambda *_a, **_k: _ServingServer(loaded=False),
    )
    start = _route_endpoint(create_app(), "/api/local/tests", method="POST")
    with _session() as session, pytest.raises(HTTPException) as exc:
        start(_tests_request(), session)
    assert exc.value.status_code == 409
    assert exc.value.detail["not_loaded"] == "qwen3:4b"
