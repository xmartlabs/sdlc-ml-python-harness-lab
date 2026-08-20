"""The direct Ollama lane: model list, preflight gate and opencode routing.

The sibling of ``test_lmstudio_direct``, for the second LAN host. The contract
is the same everywhere it can be — strict about what the host can answer — and
deliberately different in one place: a cold Ollama model is a warning, not a
block, because Ollama loads with a *server-wide* ``OLLAMA_CONTEXT_LENGTH``
rather than a per-model default, so "not loaded" does not imply "will load with
a window nobody can predict" the way it does in LM Studio.
"""

from __future__ import annotations

import pytest

from harness_lab_app import (
    api as api_module,
    ollama_direct,
    run_service,
)
from harness_lab_app.api import create_app
from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.ollama_direct import (
    OLLAMA_BACKEND,
    OLLAMA_GROUP_LABEL,
    OllamaModel,
    bare_model_id,
    build_direct_options,
    clear_ollama_cache,
    find_model,
    label_for,
    list_ollama_models,
    ollama_model_id,
    ollama_root,
)
from harness_lab_app.opencode_overlay import (
    build_config,
    build_ollama_provider,
    split_direct_models,
)
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate

TAGS = {
    "models": [
        {
            "model": "qwen3:32b",
            "details": {
                "parameter_size": "32.8B",
                "quantization_level": "Q4_K_M",
                "family": "qwen3",
            },
        },
        {"model": "llama3.3:70b", "details": {"parameter_size": "70B"}},
    ]
}
PS = {"models": [{"model": "qwen3:32b", "context_length": 40960}]}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_ollama_cache()
    yield
    clear_ollama_cache()


def _serve(monkeypatch, payloads: dict[str, dict]) -> list[str]:
    calls: list[str] = []

    def _get(url: str, timeout: float, payload: dict | None = None) -> dict:
        calls.append(url)
        if url not in payloads:
            raise OSError(f"no route to {url}")
        return payloads[url]

    monkeypatch.setattr(ollama_direct, "get_json", _get)
    return calls


# -- ids ---------------------------------------------------------------------


def test_only_the_provider_prefix_is_stripped():
    # The tag separator must survive: Ollama registers "qwen3:32b".
    assert bare_model_id("ollama/qwen3:32b") == "qwen3:32b"
    assert bare_model_id("qwen3:32b") == "qwen3:32b"
    assert ollama_model_id("qwen3:32b") == "ollama/qwen3:32b"
    assert ollama_root("http://h:11434/v1") == "http://h:11434"
    assert ollama_root("http://h:11434/api") == "http://h:11434"


# -- listing -----------------------------------------------------------------


def test_lists_installed_models_with_the_running_ones_first(monkeypatch):
    _serve(
        monkeypatch,
        {"http://h:11434/api/tags": TAGS, "http://h:11434/api/ps": PS},
    )
    models = list_ollama_models("http://h:11434/v1")

    assert [m.id for m in models] == ["qwen3:32b", "llama3.3:70b"]
    hot = models[0]
    assert hot.running is True
    # The served window, straight from /api/ps — no probing, no inference.
    assert hot.context_length == 40960
    assert hot.parameter_size == "32.8B"
    assert hot.quantization == "Q4_K_M"
    # A model that is not running has no window: it would get whatever
    # OLLAMA_CONTEXT_LENGTH the server was started with, unreadable from here.
    assert models[1].running is False
    assert models[1].context_length is None


def test_a_failing_ps_still_leaves_a_usable_menu(monkeypatch):
    """An old or busy host: the installed list is still the right menu."""
    _serve(monkeypatch, {"http://h:11434/api/tags": TAGS})
    models = list_ollama_models("http://h:11434/v1")
    assert [m.id for m in models] == ["llama3.3:70b", "qwen3:32b"]
    assert all(not m.running for m in models)


def test_an_unreachable_host_raises_instead_of_failing_open(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(DirectHostUnreachable, match="did not answer"):
        list_ollama_models("http://h:11434/v1")


def test_an_unset_base_url_raises_with_the_fix(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    with pytest.raises(DirectHostUnreachable, match="OLLAMA_BASE_URL"):
        list_ollama_models("")


def test_find_model_tolerates_the_prefix_on_either_side():
    models = [OllamaModel("qwen3:32b", True, 40960)]
    assert find_model(models, "ollama/qwen3:32b") is models[0]
    assert find_model(models, "qwen3:32b") is models[0]
    assert find_model(models, "qwen3:8b") is None


# -- picker options ----------------------------------------------------------


def test_labels_warn_about_the_server_default_on_a_cold_model():
    hot = OllamaModel("qwen3:32b", True, 40960, parameter_size="32.8B")
    assert label_for(hot) == "qwen3:32b 32.8B — running, 41k ctx"
    cold = OllamaModel("qwen3:32b", False, None)
    # The 4k default is the whole trap, so the label says it before the pick.
    assert "not loaded" in label_for(cold) and "4k" in label_for(cold)


def test_direct_options_share_the_shape_the_picker_expects():
    options = build_direct_options([OllamaModel("qwen3:32b", True, 40960)])
    assert options == [
        {
            "id": "ollama/qwen3:32b",
            "label": "qwen3:32b — running, 41k ctx",
            "routable": True,
            "aliases": [],
            "backend": OLLAMA_BACKEND,
            "group": OLLAMA_GROUP_LABEL,
            "group_rank": -1,
            "source": "direct",
            "provider": "ollama",
            "provider_label": "Ollama",
            "loaded": True,
            "loaded_context": 40960,
            "max_context": None,
        }
    ]


# -- opencode routing --------------------------------------------------------


def test_direct_ids_are_split_by_the_provider_that_must_declare_them():
    grouped = split_direct_models(
        ["lmstudio/p/m", "ollama/qwen3:32b", "gateway/gpt-5.5", ""]
    )
    assert grouped == {"lmstudio": ["lmstudio/p/m"], "ollama": ["ollama/qwen3:32b"]}


def test_the_overlay_declares_an_ollama_provider_for_direct_models():
    provider = build_ollama_provider(["ollama/qwen3:32b"])
    assert provider["options"]["baseURL"] == "{env:OLLAMA_BASE_URL}"
    assert set(provider["models"]) == {"qwen3:32b"}


def test_build_config_can_carry_both_direct_providers_at_once():
    tracked = {"npm": "x", "models": {"a": {}}}
    both = build_config(tracked, {}, ["lmstudio/p/m", "ollama/qwen3:32b"])
    assert set(both["provider"]) == {"gateway", "lmstudio", "ollama"}
    # And neither appears when no direct model was selected.
    assert set(build_config(tracked, {"b": {}})["provider"]) == {"gateway"}


# -- preflight ---------------------------------------------------------------


def _config(model: str) -> RunCreate:
    return RunCreate(
        run_id="ollama-run",
        config=RunConfigCreate(
            case_id="tetris-v1", prompt_variant="sdlc", executor_model=model
        ),
    )


@pytest.fixture
def _direct(monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        run_service, "read_env_file_value", lambda _k: "http://h:11434/v1"
    )

    def _set(models: list[OllamaModel]):
        monkeypatch.setattr(
            ollama_direct, "list_ollama_models_cached", lambda *_a, **_k: models
        )

    return _set


def test_preflight_accepts_a_running_model_with_room_for_the_harness(session, _direct):
    _direct([OllamaModel("qwen3:32b", True, 40960)])
    run = RunService(session).create_run(_config("ollama/qwen3:32b"))
    assert run.run_id == "ollama-run"


def test_preflight_names_the_models_the_host_actually_has(session, _direct):
    _direct([OllamaModel("qwen3:32b", True, 40960)])
    with pytest.raises(RunPreflightError, match="qwen3:32b"):
        RunService(session).create_run(_config("ollama/qwen3.6:35b"))


def test_preflight_rejects_a_running_window_below_the_floor(session, _direct):
    _direct([OllamaModel("qwen3:32b", True, 4096)])
    with pytest.raises(RunPreflightError, match="OLLAMA_CONTEXT_LENGTH"):
        RunService(session).create_run(_config("ollama/qwen3:32b"))


def test_a_cold_model_is_allowed_through(session, _direct):
    """Where Ollama and LM Studio deliberately differ.

    LM Studio's JIT window is per model and unknowable, so a cold model is
    refused there. Ollama's comes from the server, which may well be configured
    correctly — blocking on something this preflight cannot read would ground a
    working host.
    """
    _direct([OllamaModel("qwen3:32b", False, None)])
    assert RunService(session).create_run(_config("ollama/qwen3:32b")).run_id == (
        "ollama-run"
    )


def test_preflight_demands_the_v1_suffix(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(run_service, "read_env_file_value", lambda _k: "http://h:11434")
    with pytest.raises(RunPreflightError, match=r"http://h:11434/v1"):
        RunService(session).create_run(_config("ollama/qwen3:32b"))


def test_preflight_rejects_an_unreachable_host(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        run_service, "read_env_file_value", lambda _k: "http://h:11434/v1"
    )
    monkeypatch.setattr(
        ollama_direct,
        "list_ollama_models_cached",
        lambda *_a, **_k: (_ for _ in ()).throw(DirectHostUnreachable("no answer")),
    )
    with pytest.raises(RunPreflightError, match="did not answer"):
        RunService(session).create_run(_config("ollama/qwen3:32b"))


def test_the_kill_switch_skips_the_direct_check_too(session, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(
        ollama_direct,
        "list_ollama_models_cached",
        lambda *_a, **_k: called.append(1) or [],
    )
    assert RunService(session).create_run(_config("ollama/x")).run_id == "ollama-run"
    assert called == []


# -- HTTP endpoint -----------------------------------------------------------


def _endpoint(path: str):
    app = create_app()
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def test_the_endpoint_reports_what_the_host_has(monkeypatch):
    monkeypatch.setattr(
        api_module, "read_env_file_value", lambda _k: "http://h:11434/v1"
    )
    monkeypatch.setattr(
        ollama_direct,
        "list_ollama_models_cached",
        lambda *_a, **_k: [OllamaModel("qwen3:32b", True, 40960)],
    )
    result = _endpoint("/api/ollama/models")(refresh=True)

    assert result["reachable"] is True
    assert result["base_url"] == "http://h:11434"
    assert result["models"][0]["bare_id"] == "qwen3:32b"
    assert result["models"][0]["id"] == "ollama/qwen3:32b"
    assert result["models"][0]["context_length"] == 40960


def test_the_catalog_reports_each_direct_host_separately(session, monkeypatch):
    """One host down must not hide the other, nor its reason.

    A single flag could not express "LM Studio is off but Ollama is fine",
    which is the normal state of a two-machine lab.
    """
    from harness_lab_app import gateway_models, lmstudio_direct

    monkeypatch.setattr(
        api_module,
        "read_env_file_value",
        lambda key: {
            "LMSTUDIO_BASE_URL": "http://lms:1234/v1",
            "OLLAMA_BASE_URL": "http://oll:11434/v1",
        }.get(key, ""),
    )
    monkeypatch.setattr(
        gateway_models, "list_gateway_models_cached", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        gateway_models, "fetch_gateway_routing_cached", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        lmstudio_direct,
        "list_lmstudio_models_cached",
        lambda *_a, **_k: (_ for _ in ()).throw(
            lmstudio_direct.LMStudioUnreachable("LM Studio is off")
        ),
    )
    monkeypatch.setattr(
        ollama_direct,
        "list_ollama_models_cached",
        lambda *_a, **_k: [OllamaModel("qwen3:32b", True, 40960)],
    )

    catalog = _endpoint("/api/models")(refresh=False, session=session)

    assert catalog["direct_errors"] == {"lmstudio": "LM Studio is off"}
    assert [m["id"] for m in catalog["models"] if m["source"] == "direct"] == [
        "ollama/qwen3:32b"
    ]
