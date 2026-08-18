"""The direct LM Studio lane: model list, preflight gate and opencode routing.

Root cause this whole lane guards (run tetris-v1-K-031): a LiteLLM alias
forwarded a model name LM Studio does not know, the server answered ``Invalid
model identifier``, and the run-form preflight — which treats an unrecognized
4xx as ambiguous — showed green. A day of runs went to a name mismatch three
layers away from the picker. Addressing LM Studio directly removes the layers,
so these tests assert the opposite contract from the gateway ones: strict, not
fail-open.
"""

from __future__ import annotations

import urllib.error

import pytest

from harness_lab_app import (
    api as api_module,
    direct_hosts,
    gateway_models,
    lmstudio_direct,
    run_service,
)
from harness_lab_app.api import create_app
from harness_lab_app.lmstudio_direct import (
    LMSTUDIO_BACKEND,
    LMSTUDIO_GROUP_LABEL,
    LMStudioModel,
    LMStudioUnreachable,
    bare_model_id,
    build_direct_options,
    clear_lmstudio_cache,
    find_model,
    label_for,
    list_lmstudio_models,
    list_lmstudio_models_cached,
    lmstudio_model_id,
    lmstudio_root,
)
from harness_lab_app.opencode_overlay import build_config, build_lmstudio_provider
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate

V0_PAYLOAD = {
    "data": [
        {
            "id": "prism-ml/bonsai-27b",
            "type": "llm",
            "state": "loaded",
            "max_context_length": 131072,
            "loaded_context_length": 54000,
            "arch": "gemma3",
            "quantization": "Q4_K_M",
        },
        {
            "id": "qwen/qwen3.6-35b",
            "type": "llm",
            "state": "not-loaded",
            "max_context_length": 262144,
        },
        {
            "id": "text-embedding-nomic",
            "type": "embeddings",
            "state": "loaded",
            "max_context_length": 2048,
        },
    ]
}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_lmstudio_cache()
    yield
    clear_lmstudio_cache()


def _fake_clock(monkeypatch):
    """Freeze the cache's clock and hand back a setter for it.

    A settable value rather than a list of ticks: the cache reads the clock a
    different number of times depending on whether an entry hit or missed, and
    a test that has to predict that count breaks on every refactor.
    """
    now = [0.0]
    monkeypatch.setattr(direct_hosts.time, "monotonic", lambda: now[0])
    return lambda value: now.__setitem__(0, value)


def _serve(monkeypatch, payloads: dict[str, dict]) -> list[str]:
    """Stub the HTTP layer with a URL → payload map, recording the calls."""
    calls: list[str] = []

    def _get(url: str, timeout: float) -> dict:
        calls.append(url)
        if url not in payloads:
            raise urllib.error.HTTPError(url, 404, "not found", None, None)  # type: ignore[arg-type]
        return payloads[url]

    monkeypatch.setattr(lmstudio_direct, "_get_json", _get)
    return calls


# -- ids and URLs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://192.168.1.50:1234/v1", "http://192.168.1.50:1234"),
        ("http://192.168.1.50:1234/v1/", "http://192.168.1.50:1234"),
        ("http://192.168.1.50:1234/api/v0", "http://192.168.1.50:1234"),
        ("http://192.168.1.50:1234", "http://192.168.1.50:1234"),
        ("  http://host:1234/v1  ", "http://host:1234"),
        ("", ""),
    ],
)
def test_root_accepts_either_spelling_of_the_base_url(given, expected):
    assert lmstudio_root(given) == expected


def test_only_the_provider_prefix_is_stripped():
    # The model's own publisher slash must survive: LM Studio registers
    # "prism-ml/bonsai-27b", and asking it for "bonsai-27b" is a 404.
    assert bare_model_id("lmstudio/prism-ml/bonsai-27b") == "prism-ml/bonsai-27b"
    assert bare_model_id("prism-ml/bonsai-27b") == "prism-ml/bonsai-27b"
    assert lmstudio_model_id("prism-ml/bonsai-27b") == "lmstudio/prism-ml/bonsai-27b"
    assert lmstudio_model_id("lmstudio/x") == "lmstudio/x"


# -- listing -----------------------------------------------------------------


def test_lists_runnable_models_loaded_first(monkeypatch):
    _serve(monkeypatch, {"http://h:1234/api/v0/models": V0_PAYLOAD})
    models = list_lmstudio_models("http://h:1234/v1")

    # Embedding models are dropped: they cannot serve a run.
    assert [m.id for m in models] == ["prism-ml/bonsai-27b", "qwen/qwen3.6-35b"]
    bonsai = models[0]
    assert bonsai.loaded is True
    assert bonsai.loaded_context == 54000
    assert bonsai.max_context == 131072
    assert bonsai.usable_context == 54000
    # An unloaded model has no window: whatever a JIT load picks is unknown here.
    assert models[1].usable_context is None


def test_falls_back_to_the_openai_list_on_an_old_build(monkeypatch):
    calls = _serve(
        monkeypatch,
        {"http://h:1234/v1/models": {"data": [{"id": "some/model"}]}},
    )
    models = list_lmstudio_models("http://h:1234/v1")

    assert calls == ["http://h:1234/api/v0/models", "http://h:1234/v1/models"]
    # State is unknowable without the native API — reported as such, not guessed.
    assert [(m.id, m.state, m.loaded_context) for m in models] == [
        ("some/model", "unknown", None)
    ]


def test_an_unreachable_host_raises_instead_of_failing_open(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(lmstudio_direct, "_get_json", _boom)
    with pytest.raises(LMStudioUnreachable, match="connection refused"):
        list_lmstudio_models("http://h:1234/v1")


def test_an_unset_base_url_raises_with_the_fix(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    with pytest.raises(LMStudioUnreachable, match="LMSTUDIO_BASE_URL"):
        list_lmstudio_models("")


def test_the_cache_is_short_and_never_caches_a_failure(monkeypatch):
    calls = _serve(monkeypatch, {"http://h:1234/api/v0/models": V0_PAYLOAD})
    list_lmstudio_models_cached("http://h:1234/v1")
    list_lmstudio_models_cached("http://h:1234/api/v0")  # same root
    assert len(calls) == 1

    # A server that comes back must be visible at once, so failures are not
    # remembered — this is why the picker recovers without a restart.
    clear_lmstudio_cache()
    monkeypatch.setattr(
        lmstudio_direct,
        "_get_json",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")),
    )
    for _ in range(2):
        with pytest.raises(LMStudioUnreachable):
            list_lmstudio_models_cached("http://h:1234/v1")


def test_a_degraded_v1_listing_expires_far_sooner_than_a_healthy_one(monkeypatch):
    """The "only one model until you refresh" bug.

    On some builds ``/v1/models`` reports just the model currently loaded, so a
    single failed ``/api/v0`` call can pin a one-entry menu for the full TTL —
    indistinguishable from the server having lost every other model. A degraded
    listing is therefore cached for seconds, not for the healthy lifetime.
    """
    clock = _fake_clock(monkeypatch)
    calls = _serve(
        monkeypatch, {"http://h:1234/v1/models": {"data": [{"id": "only/one"}]}}
    )

    first = list_lmstudio_models_cached("http://h:1234/v1")
    assert [m.state for m in first] == ["unknown"]
    v0_and_v1 = 2  # the native API is tried first and 404s
    assert len(calls) == v0_and_v1

    clock(1.0)
    list_lmstudio_models_cached("http://h:1234/v1")
    assert len(calls) == v0_and_v1
    clock(3.0)  # past the degraded TTL, well inside the healthy one
    list_lmstudio_models_cached("http://h:1234/v1")
    assert len(calls) == 2 * v0_and_v1


def test_a_healthy_listing_keeps_the_full_ttl(monkeypatch):
    clock = _fake_clock(monkeypatch)
    calls = _serve(monkeypatch, {"http://h:1234/api/v0/models": V0_PAYLOAD})

    list_lmstudio_models_cached("http://h:1234/v1")
    clock(3.0)  # past the degraded TTL, well inside the healthy 15s
    list_lmstudio_models_cached("http://h:1234/v1")
    assert len(calls) == 1


def test_find_model_tolerates_the_prefix_on_either_side():
    models = [LMStudioModel("a/b", "llm", "loaded", 100, 100)]
    assert find_model(models, "lmstudio/a/b") is models[0]
    assert find_model(models, "a/b") is models[0]
    assert find_model(models, "a/c") is None


# -- picker options ----------------------------------------------------------


def test_labels_carry_the_two_facts_that_decide_a_run():
    loaded = LMStudioModel("p/m", "llm", "loaded", 131072, 54000)
    assert label_for(loaded) == "p/m — loaded, 54k ctx"
    cold = LMStudioModel("p/m", "llm", "not-loaded", 131072, None)
    assert "not loaded" in label_for(cold)
    old = LMStudioModel("p/m", "llm", "unknown", None, None)
    assert "state unknown" in label_for(old)


def test_direct_options_lead_the_picker_and_are_always_routable():
    options = build_direct_options([LMStudioModel("p/m", "llm", "loaded", 100, 100)])
    assert options == [
        {
            "id": "lmstudio/p/m",
            "label": "p/m — loaded, 0k ctx",
            "routable": True,
            "aliases": [],
            "backend": LMSTUDIO_BACKEND,
            "group": LMSTUDIO_GROUP_LABEL,
            "group_rank": -1,
            # The picker's first tier: "direct" is what distinguishes this from
            # a gateway/lmstudio-* alias, which reads the same but is not.
            "source": "direct",
            "provider": "lmstudio",
            "provider_label": "LM Studio",
            "loaded": True,
            "loaded_context": 100,
            "max_context": 100,
        }
    ]


# -- opencode routing --------------------------------------------------------


def test_the_overlay_declares_a_provider_for_each_direct_model():
    provider = build_lmstudio_provider(["lmstudio/prism-ml/bonsai-27b", "p/m"])
    assert provider["options"]["baseURL"] == "{env:LMSTUDIO_BASE_URL}"
    # Keyed by the bare id: opencode splits "lmstudio/prism-ml/bonsai-27b" on
    # the first slash only, so the model key keeps the publisher segment.
    assert set(provider["models"]) == {"prism-ml/bonsai-27b", "p/m"}


def test_build_config_adds_the_lmstudio_provider_only_when_one_is_selected():
    tracked = {"npm": "x", "models": {"a": {}}}
    assert set(build_config(tracked, {"b": {}})["provider"]) == {"gateway"}
    with_direct = build_config(tracked, {}, ["lmstudio/p/m"])
    assert set(with_direct["provider"]) == {"gateway", "lmstudio"}


# -- preflight ---------------------------------------------------------------


def _config(model: str) -> RunCreate:
    return RunCreate(
        run_id="direct-run",
        config=RunConfigCreate(
            case_id="tetris-v1", prompt_variant="sdlc", executor_model=model
        ),
    )


@pytest.fixture
def _direct(monkeypatch):
    """A configured, reachable LM Studio, with the network stubbed out."""
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        run_service, "read_env_file_value", lambda _k: "http://h:1234/v1"
    )

    def _set(models: list[LMStudioModel]):
        monkeypatch.setattr(
            lmstudio_direct, "list_lmstudio_models_cached", lambda *_a, **_k: models
        )

    return _set


def test_preflight_accepts_a_loaded_model_with_room_for_the_harness(session, _direct):
    _direct([LMStudioModel("prism-ml/bonsai-27b", "llm", "loaded", 131072, 54000)])
    run = RunService(session).create_run(_config("lmstudio/prism-ml/bonsai-27b"))
    assert run.run_id == "direct-run"


def test_preflight_names_the_models_the_server_actually_serves(session, _direct):
    """The K-031 fix: an unknown name is a hard, immediate, explained failure.

    Through the gateway this same mistake produced a green check and a run that
    burned its steps on ``Invalid model identifier``.
    """
    _direct([LMStudioModel("prism-ml/bonsai-27b", "llm", "loaded", 131072, 54000)])
    with pytest.raises(RunPreflightError, match="prism-ml/bonsai-27b"):
        RunService(session).create_run(_config("lmstudio/qwen3.6:35b"))


def test_preflight_rejects_a_model_that_is_not_loaded(session, _direct):
    _direct([LMStudioModel("p/m", "llm", "not-loaded", 131072, None)])
    with pytest.raises(RunPreflightError, match="not loaded"):
        RunService(session).create_run(_config("lmstudio/p/m"))


def test_preflight_rejects_a_window_too_small_for_the_system_prompt(session, _direct):
    _direct([LMStudioModel("p/m", "llm", "loaded", 131072, 8192)])
    with pytest.raises(RunPreflightError, match="8192"):
        RunService(session).create_run(_config("lmstudio/p/m"))


def test_an_old_build_with_unknown_state_still_runs(session, _direct):
    # The one direct case that falls through: the id is real, the metadata is
    # missing. Blocking would ground a working setup on a server version.
    _direct([LMStudioModel("p/m", "llm", "unknown", None, None)])
    assert (
        RunService(session).create_run(_config("lmstudio/p/m")).run_id == "direct-run"
    )


def test_preflight_rejects_an_unreachable_host(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        run_service, "read_env_file_value", lambda _k: "http://h:1234/v1"
    )
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: (_ for _ in ()).throw(LMStudioUnreachable("no answer")),
    )
    with pytest.raises(RunPreflightError, match="did not answer"):
        RunService(session).create_run(_config("lmstudio/p/m"))


def test_preflight_demands_the_v1_suffix(session, monkeypatch):
    """A URL without /v1 lists models fine and 404s on every completion.

    The native model API lives at /api/v0 beside the OpenAI-compatible one, so
    the list this preflight reads would look perfect while the run failed.
    """
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(run_service, "read_env_file_value", lambda _k: "http://h:1234")
    with pytest.raises(RunPreflightError, match=r"http://h:1234/v1"):
        RunService(session).create_run(_config("lmstudio/p/m"))


def test_preflight_reports_an_unset_base_url(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(run_service, "read_env_file_value", lambda _k: "")
    with pytest.raises(RunPreflightError, match="LMSTUDIO_BASE_URL"):
        RunService(session).create_run(_config("lmstudio/p/m"))


def test_the_kill_switch_skips_the_direct_check_too(session, monkeypatch):
    """The autouse fixture sets it: no test may reach for a real host."""
    called: list[int] = []
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: called.append(1) or [],
    )
    assert (
        RunService(session).create_run(_config("lmstudio/p/m")).run_id == "direct-run"
    )
    assert called == []


# -- HTTP endpoint -----------------------------------------------------------


def _endpoint(path: str):
    app = create_app()
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def test_the_endpoint_reports_what_the_host_serves(monkeypatch):
    monkeypatch.setattr(
        api_module, "read_env_file_value", lambda _k: "http://h:1234/v1"
    )
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: [LMStudioModel("p/m", "llm", "loaded", 131072, 54000)],
    )
    result = _endpoint("/api/lmstudio/models")(refresh=True)

    assert result["reachable"] is True
    assert result["base_url"] == "http://h:1234"
    assert result["models"][0]["bare_id"] == "p/m"
    assert result["models"][0]["id"] == "lmstudio/p/m"
    assert result["models"][0]["loaded_context"] == 54000


def test_reloading_the_run_form_menu_drops_both_caches(session, monkeypatch):
    """Both halves of one menu, behind two caches with different lifetimes.

    Clearing only the gateway side is what made "Refresh" look like it
    half-worked: the direct group kept whatever the 15s cache held.
    """
    cleared: list[str] = []
    monkeypatch.setattr(
        lmstudio_direct, "clear_lmstudio_cache", lambda: cleared.append("direct")
    )
    monkeypatch.setattr(
        gateway_models, "clear_model_cache", lambda: cleared.append("gateway")
    )
    monkeypatch.setattr(api_module, "read_env_file_value", lambda _k: "")
    monkeypatch.setattr(
        gateway_models, "list_gateway_models_cached", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        gateway_models, "fetch_gateway_routing_cached", lambda *_a, **_k: {}
    )

    endpoint = _endpoint("/api/models")
    endpoint(refresh=False, session=session)
    assert cleared == []
    endpoint(refresh=True, session=session)
    assert sorted(cleared) == ["direct", "gateway"]


def test_a_dead_host_marks_the_gateway_cards_that_point_at_it(session, monkeypatch):
    """One probe, two groups.

    The run form used to say "LM Studio did not answer" under the direct
    heading while the gateway aliases for that same machine sat above it
    looking launchable — the trap that sends a run to a powered-off box.
    """
    monkeypatch.setattr(
        api_module,
        "read_env_file_value",
        lambda key: ("http://h:1234/v1" if key == "LMSTUDIO_BASE_URL" else ""),
    )
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: (_ for _ in ()).throw(LMStudioUnreachable("server is off")),
    )
    monkeypatch.setattr(
        gateway_models,
        "list_gateway_models_cached",
        lambda *_a, **_k: ["lmstudio-qwen3.6-35b", "gpt-5.5"],
    )
    monkeypatch.setattr(
        gateway_models,
        "fetch_gateway_routing_cached",
        lambda *_a, **_k: {
            "lmstudio-qwen3.6-35b": {
                "target": "lm_studio/qwen/qwen3.6-35b-a3b",
                "api_base": "http://h:1234/v1",
                "params": {},
            },
            "gpt-5.5": {"target": "openai/gpt-5.5", "api_base": None, "params": {}},
        },
    )

    catalog = _endpoint("/api/models")(refresh=True, session=session)

    by_id = {m["id"]: m for m in catalog["models"]}
    assert by_id["gateway/lmstudio-qwen3.6-35b"]["reachable"] is False
    assert (
        "server is off" in by_id["gateway/lmstudio-qwen3.6-35b"]["unreachable_reason"]
    )
    # The cloud card shares no machine with the dead host and keeps its place.
    assert by_id["gateway/gpt-5.5"]["reachable"] is None
    assert catalog["direct_errors"]["lmstudio"] == "server is off"


def test_the_endpoint_explains_why_the_list_is_empty(monkeypatch):
    """An empty group with no reason is unactionable — the fix depends on why."""
    monkeypatch.setattr(api_module, "read_env_file_value", lambda _k: "")
    unset = _endpoint("/api/lmstudio/models")(refresh=False)
    assert unset["reachable"] is False
    assert "LMSTUDIO_BASE_URL" in unset["error"]

    monkeypatch.setattr(
        api_module, "read_env_file_value", lambda _k: "http://h:1234/v1"
    )
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: (_ for _ in ()).throw(LMStudioUnreachable("server is off")),
    )
    down = _endpoint("/api/lmstudio/models")(refresh=False)
    assert down == {
        "base_url": "http://h:1234",
        "reachable": False,
        "error": "server is off",
        "models": [],
    }
