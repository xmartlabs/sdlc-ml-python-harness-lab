from __future__ import annotations

import pytest
from sqlmodel import select

from harness_lab_app.local_tuning import host_guard
from harness_lab_app.models import Run, RunEvent, RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def _seed(
    session,
    run_id: str,
    *,
    executor_model: str = "",
    planner_model: str | None = None,
    state: RunState = RunState.running,
) -> Run:
    service = RunService(session)
    service.create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="sdlc",
                executor_model=executor_model,
                planner_model=planner_model,
                dry_run=True,
            ),
        )
    )
    run = session.exec(select(Run).where(Run.run_id == run_id)).one()
    run.state = state
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


ROUTING = {
    "qwen-alias": {
        "target": "lm_studio/qwen/qwen3",
        "api_base": "http://192.168.0.10:1234/v1",
    },
    "elsewhere-alias": {
        "target": "lm_studio/other",
        "api_base": "http://192.168.0.99:1234/v1",
    },
    "claude-alias": {"target": "anthropic/claude-opus-5-5", "api_base": None},
}


# ---- host_of_model ---------------------------------------------------------


def test_host_of_model_direct_lmstudio(monkeypatch):
    from harness_lab_app import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "read_env_file_value",
        lambda name, default="": (
            "http://192.168.0.10:1234/v1" if name == "LMSTUDIO_BASE_URL" else default
        ),
    )
    assert (
        host_guard.host_of_model("lmstudio/qwen3", None) == "http://192.168.0.10:1234"
    )


def test_host_of_model_gateway_alias_matching_lan_host():
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    assert host == "http://192.168.0.10:1234"


def test_host_of_model_gateway_alias_elsewhere():
    host = host_guard.host_of_model("gateway/elsewhere-alias", ROUTING)
    assert host == "http://192.168.0.99:1234"


def test_host_of_model_cloud_model_none():
    assert host_guard.host_of_model("claude-opus-5-5", ROUTING) is None
    assert host_guard.host_of_model("gateway/claude-alias", ROUTING) is None


def test_host_normalization_localhost_vs_127():
    assert host_guard._normalize_host(
        "http://localhost:1234"
    ) == host_guard._normalize_host("http://127.0.0.1:1234")


def test_host_normalization_v1_suffix_irrelevant():
    assert host_guard._normalize_host(
        "http://192.168.0.10:1234/v1"
    ) == host_guard._normalize_host("http://192.168.0.10:1234")


def test_host_normalization_ipv6_loopback_folds_with_localhost():
    assert host_guard._normalize_host(
        "http://[::1]:1234"
    ) == host_guard._normalize_host("http://localhost:1234")
    assert host_guard._normalize_host(
        "http://0.0.0.0:1234"
    ) == host_guard._normalize_host("http://localhost:1234")
    assert host_guard._normalize_host(
        "http://host.docker.internal:1234"
    ) == host_guard._normalize_host("http://localhost:1234")


# ---- _is_lan_host (false-negative hardening, spec item 7) -------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1234",
        "http://10.1.2.3:1234",
        "http://192.168.1.5:1234",
        "http://172.20.0.5:1234",
        "http://100.64.0.5:1234",  # CGNAT
        "http://[::1]:1234",
        "http://[fc00::1]:1234",  # unique-local IPv6
        "http://lmstudio-box:1234",  # single-label hostname, no dot
        "http://myserver.local:1234",
        "http://myserver.lan:1234",
        "http://myserver.internal:1234",
        "http://myserver.home.arpa:1234",
        "http://host.docker.internal:1234",
        "http://0.0.0.0:1234",
    ],
)
def test_is_lan_host_true_for_local_addresses(url):
    assert host_guard._is_lan_host(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:1234",
        "http://example.com:1234",
        "http://api.openai.com",
    ],
)
def test_is_lan_host_false_for_public_addresses(url):
    assert host_guard._is_lan_host(url) is False


# ---- active_runs_on_host ----------------------------------------------------


def test_active_runs_on_host_direct_lmstudio_match(session, monkeypatch):
    from harness_lab_app import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "read_env_file_value",
        lambda name, default="": (
            "http://192.168.0.10:1234/v1" if name == "LMSTUDIO_BASE_URL" else default
        ),
    )
    _seed(session, "guard-1", executor_model="lmstudio/qwen3", state=RunState.running)
    host = host_guard.host_of_model("lmstudio/qwen3", None)
    report = host_guard.active_runs_on_host(session, host, None)
    assert len(report.running) == 1
    assert report.running[0].run_id == "guard-1"


def test_active_runs_on_host_gateway_alias_match(session):
    _seed(
        session, "guard-2", executor_model="gateway/qwen-alias", state=RunState.running
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    assert any(ref.run_id == "guard-2" for ref in report.running)


def test_active_runs_on_host_gateway_alias_pointed_elsewhere_no_match(session):
    _seed(
        session,
        "guard-3",
        executor_model="gateway/elsewhere-alias",
        state=RunState.running,
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    assert not any(ref.run_id == "guard-3" for ref in report.running)


def test_active_runs_on_host_claude_no_match(session):
    _seed(session, "guard-4", executor_model="claude-opus-5-5", state=RunState.running)
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    assert not any(ref.run_id == "guard-4" for ref in report.running)


def test_active_runs_on_host_completed_run_no_match(session):
    _seed(
        session,
        "guard-5",
        executor_model="gateway/qwen-alias",
        state=RunState.completed,
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    assert not any(ref.run_id == "guard-5" for ref in report.running)
    assert not any(ref.run_id == "guard-5" for ref in report.queued)


def test_active_runs_on_host_pending_is_queued(session):
    _seed(
        session, "guard-6", executor_model="gateway/qwen-alias", state=RunState.pending
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    assert any(ref.run_id == "guard-6" for ref in report.queued)
    assert not any(ref.run_id == "guard-6" for ref in report.running)


# ---- record_override --------------------------------------------------------


def test_record_override_writes_one_event_per_running_run(session):
    _seed(
        session, "guard-7", executor_model="gateway/qwen-alias", state=RunState.running
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    count = host_guard.record_override(session, report, "load qwen3")
    assert count == 1
    events = session.exec(
        select(RunEvent).where(
            RunEvent.run_id == "guard-7", RunEvent.level == "warning"
        )
    ).all()
    assert len(events) == 1
    assert "Tune your local model" in events[0].message
    assert "load qwen3" in events[0].message


def test_record_override_skips_queued_runs(session):
    _seed(
        session, "guard-8", executor_model="gateway/qwen-alias", state=RunState.pending
    )
    host = host_guard.host_of_model("gateway/qwen-alias", ROUTING)
    report = host_guard.active_runs_on_host(session, host, ROUTING)
    count = host_guard.record_override(session, report, "tests")
    assert count == 0
    events = session.exec(
        select(RunEvent).where(
            RunEvent.run_id == "guard-8", RunEvent.level == "warning"
        )
    ).all()
    assert len(events) == 0


# ---- model_load_check --------------------------------------------------------


class _FakeServer:
    kind = "lmstudio"
    base_url = "http://192.168.0.10:1234"

    def list_models(self):
        from harness_lab_app.local_tuning.contracts import ModelInfo

        return [
            ModelInfo(id="qwen/qwen3", loaded=True, size_bytes=5 * 1024**3),
        ]

    def load(self, model_id, config):
        raise NotImplementedError

    def unload(self, model_id):
        raise NotImplementedError

    def complete(self, *args, **kwargs):
        raise NotImplementedError


def test_model_load_check_with_fake_server(session, monkeypatch):
    from harness_lab_app import config as config_mod
    from harness_lab_app.local_tuning import servers as servers_mod

    monkeypatch.setattr(
        config_mod,
        "read_env_file_value",
        lambda name, default="": (
            "http://192.168.0.10:1234/v1" if name == "LMSTUDIO_BASE_URL" else default
        ),
    )
    monkeypatch.setattr(
        servers_mod, "make_server", lambda _kind, _url, **_kw: _FakeServer()
    )

    result = host_guard.model_load_check(session, "gateway/qwen-alias", ROUTING)
    assert result["host"] == "http://192.168.0.10:1234"
    assert result["model_bare"] == "qwen/qwen3"
    assert result["loaded"] is True
    assert result["size_gb"] == 5.0


def test_model_load_check_gateway_uses_card_api_base_not_env_var(session, monkeypatch):
    """A gateway card must be probed at its own api_base, never the lab's env var.

    The env var here points at a different host than the card's api_base; if
    model_load_check used it, it would ask the wrong machine for the model.
    """
    from harness_lab_app import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "read_env_file_value",
        lambda name, default="": (
            "http://10.0.0.99:1234/v1" if name == "LMSTUDIO_BASE_URL" else default
        ),
    )

    seen_urls: list[str] = []

    def fake_make_server(kind, url, **kwargs):
        seen_urls.append(url)
        return _FakeServer()

    result = host_guard.model_load_check(
        session, "gateway/qwen-alias", ROUTING, make_server=fake_make_server
    )
    assert seen_urls == ["http://192.168.0.10:1234/v1"]
    assert result["loaded"] is True


def test_model_load_check_unknown_target_prefix_returns_loaded_none(session):
    routing = {
        "vllm-alias": {
            "target": "hosted_vllm/some-model",
            "api_base": "http://192.168.0.50:8000",
        }
    }
    result = host_guard.model_load_check(session, "gateway/vllm-alias", routing)
    assert result["server_kind"] is None
    assert result["loaded"] is None


def test_model_load_check_fails_open_without_server(session, monkeypatch):
    from harness_lab_app import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "read_env_file_value",
        lambda name, default="": (
            "http://192.168.0.10:1234/v1" if name == "LMSTUDIO_BASE_URL" else default
        ),
    )
    result = host_guard.model_load_check(session, "gateway/qwen-alias", ROUTING)
    assert result["loaded"] is None
