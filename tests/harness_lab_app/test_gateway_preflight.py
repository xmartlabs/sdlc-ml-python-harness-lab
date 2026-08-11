"""Preflight gateway probe: fail fast when a model's backend is provably down.

Root cause this guards (runs K-004/K-005): a ``gateway/lmstudio-…`` executor
whose LM Studio host is off — LiteLLM returns 5xx "Connection error" for every
call and the run burns all its steps on opaque stream errors.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

from harness_lab_app import gateway_models, opencode_overlay, run_service
from harness_lab_app.gateway_models import probe_gateway_model
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _http_error(code: int, payload: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://gw/v1/chat/completions",
        code=code,
        msg="err",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(payload.encode()),
    )


def test_probe_flags_backend_connection_error(monkeypatch):
    error = _http_error(
        500,
        "litellm.InternalServerError: OpenAIException - Connection error."
        "No fallback model group found for lmstudio-qwen3.6-35b-build",
    )

    def _raise(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(gateway_models.urllib.request, "urlopen", _raise)
    detail = probe_gateway_model(
        "gateway/lmstudio-qwen3.6-35b-build", base_url="https://gw/v1", token="t"
    )
    assert detail is not None and "connection error" in detail.lower()


@pytest.mark.parametrize(
    "raiser",
    [
        lambda: (_ for _ in ()).throw(TimeoutError("model loading")),
        lambda: (_ for _ in ()).throw(urllib.error.URLError("gateway down")),
    ],
)
def test_probe_fails_open_on_ambiguity(monkeypatch, raiser):
    # Timeouts / unreachable gateway are ambiguous (JIT load, transient net):
    # the probe must never block a possibly-runnable config.
    monkeypatch.setattr(
        gateway_models.urllib.request, "urlopen", lambda *_a, **_k: raiser()
    )
    assert probe_gateway_model("gateway/x", base_url="https://gw/v1", token="t") is None


def test_probe_ignores_non_connection_http_errors(monkeypatch):
    error = _http_error(401, "invalid api key")

    def _raise(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(gateway_models.urllib.request, "urlopen", _raise)
    assert probe_gateway_model("gateway/x", base_url="https://gw/v1", token="t") is None


def _config(model: str = "gateway/lmstudio-qwen3.6-35b-build") -> RunCreate:
    return RunCreate(
        run_id="probe-run",
        config=RunConfigCreate(
            case_id="tetris-v1", prompt_variant="sdlc", executor_model=model
        ),
    )


def test_preflight_rejects_when_backend_is_down(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    service = RunService(session)
    monkeypatch.setattr(
        service, "_probe_gateway_executor", lambda *_a: "backend unreachable"
    )
    with pytest.raises(RunPreflightError, match="unreachable"):
        service.create_run(_config())


def test_preflight_rejects_a_model_opencode_cannot_route(session, monkeypatch):
    """A model the gateway serves but opencode has no provider entry for.

    This is the failure the web model catalog exists to prevent: it used to
    surface only after the run had started burning steps.
    """
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        opencode_overlay, "routable_model_ids", lambda _root: {"gpt-5.5"}
    )
    with pytest.raises(RunPreflightError, match="not routable"):
        RunService(session).create_run(_config("gateway/never-declared"))


def test_preflight_accepts_a_model_the_overlay_makes_routable(session, monkeypatch):
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        opencode_overlay, "routable_model_ids", lambda _root: {"brand-new"}
    )
    service = RunService(session)
    monkeypatch.setattr(service, "_probe_gateway_executor", lambda *_a: None)

    run = service.create_run(_config("gateway/brand-new"))
    assert run.run_id == "probe-run"


def test_preflight_fails_open_when_the_routing_map_is_unreadable(session, monkeypatch):
    """An unreadable opencode.json must not block every run."""
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(opencode_overlay, "routable_model_ids", lambda _root: set())
    service = RunService(session)
    monkeypatch.setattr(service, "_probe_gateway_executor", lambda *_a: None)

    assert service.create_run(_config("gateway/anything")).run_id == "probe-run"


def test_preflight_env_kill_switch_skips_probe(session, monkeypatch):
    # The autouse fixture sets the kill switch; the probe must not run at all.
    called = []
    monkeypatch.setattr(
        gateway_models,
        "probe_gateway_model",
        lambda *_a, **_k: called.append(1) or "down",
    )
    run = RunService(session).create_run(_config())
    assert run.run_id == "probe-run"
    assert called == []


# -- context window gate -----------------------------------------------------


def _probe_result(status: str, context_tokens: int | None = None):
    from harness_lab_app.gateway_models import ContextProbeResult

    return ContextProbeResult(
        model="m",
        status=status,
        required_tokens=32768,
        context_tokens=context_tokens,
        prompt_tokens=None,
        detail="probe says " + status,
    )


def test_preflight_rejects_a_context_window_too_small_for_the_harness(
    session, monkeypatch
):
    """Run K-020: LM Studio auto-loaded an 8k window and all 4432 steps died.

    The harness system prompt alone is ~13.7k tokens, so the model rejected
    every single call in 0.4s. Two and a half hours of wall clock for nothing —
    which is why this is a hard gate, not a warning.
    """
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(run_service, "read_env_file_value", lambda _k: "https://gw/v1")
    monkeypatch.setattr(
        gateway_models,
        "probe_model_context_cached",
        lambda *_a, **_k: _probe_result("insufficient", 8192),
    )
    service = RunService(session)
    monkeypatch.setattr(service, "_probe_gateway_executor", lambda *_a: None)
    with pytest.raises(RunPreflightError, match="8192"):
        service.create_run(_config())


@pytest.mark.parametrize("status", ["ok", "unknown"])
def test_the_context_gate_only_blocks_on_a_proven_shortfall(
    session, monkeypatch, status
):
    """Fail open: an inconclusive probe must never stop a runnable config."""
    monkeypatch.delenv("HARNESS_LAB_DISABLE_GATEWAY_PROBE", raising=False)
    monkeypatch.setattr(
        gateway_models,
        "probe_model_context_cached",
        lambda *_a, **_k: _probe_result(status),
    )
    service = RunService(session)
    monkeypatch.setattr(service, "_probe_gateway_executor", lambda *_a: None)
    assert service.create_run(_config()).run_id == "probe-run"


def test_the_context_gate_honours_the_kill_switch(session, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(
        gateway_models,
        "probe_model_context_cached",
        lambda *_a, **_k: called.append(1) or _probe_result("insufficient", 8192),
    )
    assert RunService(session).create_run(_config()).run_id == "probe-run"
    assert called == []
