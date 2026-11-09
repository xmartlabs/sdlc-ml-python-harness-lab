from __future__ import annotations

from harness_lab_app.local_tuning import test_sessions as sessions_mod
from harness_lab_app.local_tuning.contracts import (
    Completion,
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
)


def _sync_executor(fn, *args):
    fn(*args)


class FakeServer:
    kind = "lmstudio"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def list_models(self):
        return []

    def load(self, model_id, config):
        return ServedConfig()

    def unload(self, model_id):
        return None

    def complete(
        self,
        model_id,
        prompt,
        *,
        max_tokens=128,
        tools=None,
        config=None,
        timeout=180.0,
    ):
        return Completion(
            text="OK",
            prompt_tokens=10,
            time_to_first_token_s=0.1,
            prompt_tokens_per_second=500.0,
            tokens_per_second=50.0,
        )


def _model() -> ModelInfo:
    return ModelInfo(id="m", size_bytes=1024**3, supports_tools=True)


def _config() -> LoadConfig:
    return LoadConfig(context_length=8192)


def _machine() -> MachineSpec:
    return MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64)


def setup_function(_fn):
    with sessions_mod._lock:
        sessions_mod._sessions.clear()
        sessions_mod._running_hosts.clear()


def test_start_session_runs_tests_in_order_synchronously():
    server = FakeServer("http://localhost:1234")
    row = sessions_mod.start_session(
        server,
        _model(),
        _config(),
        _machine(),
        tests=("fit", "speed", "window"),
        executor=_sync_executor,
    )
    assert row.status == "done"
    names = [result.name for result in row.results]
    assert names == ["fit", "speed", "window"]


def test_start_session_refuses_when_host_busy():
    server = FakeServer("http://localhost:1234")

    def never_finishes(fn, *args):
        # Leave the session "running" by never invoking fn.
        pass

    sessions_mod.start_session(
        server, _model(), _config(), _machine(), tests=("fit",), executor=never_finishes
    )

    other_model = ModelInfo(id="other", size_bytes=1024**3)
    try:
        sessions_mod.start_session(
            server,
            other_model,
            _config(),
            _machine(),
            tests=("fit",),
            executor=_sync_executor,
        )
        raise AssertionError("expected SessionBusy")
    except sessions_mod.SessionBusy as exc:
        assert exc.existing.model_id == "m"


def test_different_hosts_do_not_block_each_other():
    server_a = FakeServer("http://localhost:1234")
    server_b = FakeServer("http://localhost:5678")

    def never_finishes(fn, *args):
        pass

    sessions_mod.start_session(
        server_a,
        _model(),
        _config(),
        _machine(),
        tests=("fit",),
        executor=never_finishes,
    )
    row_b = sessions_mod.start_session(
        server_b,
        _model(),
        _config(),
        _machine(),
        tests=("fit",),
        executor=_sync_executor,
    )
    assert row_b.status == "done"


def test_get_session_returns_none_for_unknown_id():
    assert sessions_mod.get_session("does-not-exist") is None


def test_busy_registry_keys_by_normalized_host_not_raw_base_url():
    """localhost and 127.0.0.1 are the same server; starting on one while the
    other is busy must be refused, per the spec's "only one test session runs
    per host at a time" (item 9)."""
    server_a = FakeServer("http://localhost:1234")
    server_b = FakeServer("http://127.0.0.1:1234")

    def never_finishes(fn, *args):
        pass

    sessions_mod.start_session(
        server_a,
        _model(),
        _config(),
        _machine(),
        tests=("fit",),
        executor=never_finishes,
    )

    try:
        sessions_mod.start_session(
            server_b,
            ModelInfo(id="other", size_bytes=1024**3),
            _config(),
            _machine(),
            tests=("fit",),
            executor=_sync_executor,
        )
        raise AssertionError("expected SessionBusy")
    except sessions_mod.SessionBusy as exc:
        assert exc.existing.model_id == "m"


def test_a_finished_session_is_handed_to_the_history_saver():
    from harness_lab_app.local_tuning import test_sessions as ts
    from harness_lab_app.local_tuning.contracts import (
        LoadConfig,
        MachineSpec,
        ModelInfo,
    )

    saved = []

    class _Server:
        kind = "lmstudio"
        base_url = "http://history-host:1234"

        def complete(self, *args, **kwargs):
            raise RuntimeError("offline")

    row = ts.start_session(
        _Server(),
        ModelInfo(id="m"),
        LoadConfig(context_length=8192),
        MachineSpec(memory_kind="discrete", gpu_gb=24, ram_gb=64),
        tests=("fit",),
        executor=lambda fn, *args: fn(*args),
        on_done=lambda finished: saved.append(finished) or 7,
    )
    assert len(saved) == 1 and saved[0].results[0].name == "fit"
    assert ts.get_session(row.id).profile_id == 7
