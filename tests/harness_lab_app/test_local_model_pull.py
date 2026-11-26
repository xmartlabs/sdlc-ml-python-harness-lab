"""Downloading models onto a serving host (ADR-0101).

Covers the two adapters' ``pull`` against recorded response shapes, the job
registry, the model hub (curated list, Hugging Face parsing, fit) and the
three endpoints. No network: every HTTP seam is monkeypatched.
"""

from __future__ import annotations

import urllib.error

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import local_api
from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.local_tuning import model_hub, pull_jobs, servers
from harness_lab_app.local_tuning.contracts import (
    MachineSpec,
    PullProgress,
    ServerError,
    Verdict,
)

# --- Ollama: pull --------------------------------------------------------------


def _ollama_stream(lines: list[dict]):
    def fake(url: str, timeout: float, payload: dict):
        assert url == "http://box:11434/api/pull"
        assert payload == {"model": "qwen3:8b", "stream": True}
        yield from lines

    return fake


def test_ollama_pull_sums_layers_and_ends_on_success(monkeypatch):
    lines = [
        {"status": "pulling manifest"},
        {"status": "pulling aaa", "digest": "aaa", "total": 1000},
        {"status": "pulling aaa", "digest": "aaa", "total": 1000, "completed": 400},
        {"status": "pulling bbb", "digest": "bbb", "total": 200, "completed": 200},
        {"status": "pulling aaa", "digest": "aaa", "total": 1000, "completed": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "writing manifest"},
        {"status": "success"},
    ]
    monkeypatch.setattr(servers, "_stream_json_lines", _ollama_stream(lines))
    seen: list[PullProgress] = []

    final = servers.OllamaServer("http://box:11434").pull(
        "qwen3:8b", on_progress=seen.append
    )

    assert final.status == "completed"
    assert final.total_bytes == 1200
    assert final.completed_bytes == 1200
    # Progress is the sum over layers, never one layer's own numbers.
    mid = next(p for p in seen if p.completed_bytes == 600)
    assert mid.total_bytes == 1200
    assert any(p.detail == "verifying sha256 digest" for p in seen)


def test_ollama_pull_reports_the_servers_error(monkeypatch):
    lines = [
        {"status": "pulling manifest"},
        {"error": "pull model manifest: file does not exist"},
    ]
    monkeypatch.setattr(servers, "_stream_json_lines", _ollama_stream(lines))

    final = servers.OllamaServer("http://box:11434").pull(
        "qwen3:8b", on_progress=lambda _p: None
    )

    assert final.status == "failed"
    assert "file does not exist" in final.error


def test_ollama_pull_that_stops_without_success_is_a_failure(monkeypatch):
    lines = [{"status": "pulling aaa", "digest": "aaa", "total": 10, "completed": 5}]
    monkeypatch.setattr(servers, "_stream_json_lines", _ollama_stream(lines))

    final = servers.OllamaServer("http://box:11434").pull(
        "qwen3:8b", on_progress=lambda _p: None
    )

    assert final.status == "failed"
    assert final.completed_bytes == 5


# --- LM Studio: pull ------------------------------------------------------------


class _Router:
    def __init__(self, responses: dict[str, list]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url: str, timeout: float, payload: dict | None = None):
        self.calls.append((url, payload))
        for suffix, queue in self.responses.items():
            if url.endswith(suffix):
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"unrouted call: {url}")


def test_lmstudio_pull_polls_the_job_until_completed(monkeypatch):
    router = _Router(
        {
            "/api/v1/models/download": [
                {"job_id": "job_1", "status": "downloading", "total_size_bytes": 100}
            ],
            "/status/job_1": [
                {
                    "job_id": "job_1",
                    "status": "downloading",
                    "downloaded_bytes": 40,
                    "total_size_bytes": 100,
                },
                {"job_id": "job_1", "status": "completed", "total_size_bytes": 100},
            ],
        }
    )
    monkeypatch.setattr(servers, "_http", router)
    seen: list[PullProgress] = []

    final = servers.LMStudioServer("http://box:1234").pull(
        "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "Q4_K_M",
        on_progress=seen.append,
        sleep=lambda _s: None,
    )

    assert final.status == "completed"
    assert final.completed_bytes == 100
    assert router.calls[0][1] == {
        "model": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "quantization": "Q4_K_M",
    }
    assert [p.completed_bytes for p in seen] == [None, 40, 100]


def test_lmstudio_pull_already_downloaded_needs_no_polling(monkeypatch):
    router = _Router({"/api/v1/models/download": [{"status": "already_downloaded"}]})
    monkeypatch.setattr(servers, "_http", router)

    final = servers.LMStudioServer("http://box:1234").pull(
        "qwen/qwen3-8b", on_progress=lambda _p: None, sleep=lambda _s: None
    )

    assert final.status == "completed"
    assert final.detail == "already downloaded"
    assert len(router.calls) == 1
    assert "quantization" not in router.calls[0][1]


def test_lmstudio_without_the_download_api_says_to_update(monkeypatch):
    # LM Studio's answer to a route it does not have (recorded from 0.4).
    unknown_route = "Unexpected endpoint or method. (POST /api/v1/models/download)"
    router = _Router(
        {"/api/v1/models/download": [servers._HttpServerError(unknown_route, 404)]}
    )
    monkeypatch.setattr(servers, "_http", router)

    with pytest.raises(ServerError, match=r"Update it to 0\.4"):
        servers.LMStudioServer("http://box:1234").pull(
            "qwen/qwen3-8b", on_progress=lambda _p: None, sleep=lambda _s: None
        )


def test_lmstudio_unknown_model_keeps_the_servers_own_words(monkeypatch):
    # Recorded from LM Studio 0.4: a missing model is a 404 as well.
    missing = (
        "harness-lab/no-such-model not found - it either does not exist or you "
        "do not have permission to read it"
    )
    router = _Router(
        {"/api/v1/models/download": [servers._HttpServerError(missing, 404)]}
    )
    monkeypatch.setattr(servers, "_http", router)

    with pytest.raises(ServerError, match="not found - it either does not exist"):
        servers.LMStudioServer("http://box:1234").pull(
            "harness-lab/no-such-model",
            on_progress=lambda _p: None,
            sleep=lambda _s: None,
        )


def test_lmstudio_failed_job_is_reported(monkeypatch):
    router = _Router(
        {
            "/api/v1/models/download": [{"job_id": "j", "status": "downloading"}],
            "/status/j": [{"job_id": "j", "status": "failed"}],
        }
    )
    monkeypatch.setattr(servers, "_http", router)

    final = servers.LMStudioServer("http://box:1234").pull(
        "qwen/qwen3-8b", on_progress=lambda _p: None, sleep=lambda _s: None
    )

    assert final.status == "failed"
    assert final.error


def test_stream_json_lines_translates_a_dead_host(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(servers.urllib.request, "urlopen", refuse)

    with pytest.raises(DirectHostUnreachable):
        list(servers._stream_json_lines("http://box:11434/api/pull", 1.0, {}))


# --- Job registry ----------------------------------------------------------------


class _FakeServer:
    kind = "ollama"

    def __init__(self, base_url: str, outcome: PullProgress | Exception) -> None:
        self.base_url = base_url
        self.outcome = outcome
        self.asked: list[tuple[str, str]] = []

    def pull(self, model, quantization="", *, on_progress):
        self.asked.append((model, quantization))
        on_progress(
            PullProgress(status="downloading", total_bytes=10, completed_bytes=3)
        )
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _sync(fn, *args):
    fn(*args)


def _never(fn, *args):  # leaves the job downloading
    return None


def setup_function(_fn):
    with pull_jobs._lock:
        pull_jobs._jobs.clear()
        pull_jobs._running_hosts.clear()


def test_job_records_the_final_reading():
    server = _FakeServer(
        "http://box:11434",
        PullProgress(status="completed", total_bytes=10, completed_bytes=10),
    )

    job = pull_jobs.start_pull(server, "qwen3:8b", executor=_sync)

    assert job.status == "completed"
    assert job.completed_bytes == 10
    assert server.asked == [("qwen3:8b", "")]


def test_second_download_on_the_same_host_is_refused():
    pull_jobs.start_pull(
        _FakeServer("http://localhost:11434", PullProgress(status="completed")),
        "qwen3:8b",
        executor=_never,
    )

    with pytest.raises(pull_jobs.PullBusy) as busy:
        pull_jobs.start_pull(
            _FakeServer("http://127.0.0.1:11434", PullProgress(status="completed")),
            "qwen3:4b",
            executor=_sync,
        )
    assert busy.value.existing.model == "qwen3:8b"


def test_host_is_free_again_after_a_failure():
    failing = _FakeServer("http://box:11434", DirectHostUnreachable("gone"))
    job = pull_jobs.start_pull(failing, "qwen3:8b", executor=_sync)
    assert job.status == "failed"
    assert "stopped answering" in job.error

    again = pull_jobs.start_pull(
        _FakeServer("http://box:11434", PullProgress(status="completed")),
        "qwen3:8b",
        executor=_sync,
    )
    assert again.status == "completed"


# --- Model hub -------------------------------------------------------------------


def test_curated_quants_name_what_each_server_pulls():
    qwen8 = next(
        m for m in model_hub.curated_models() if m.repo == "Qwen/Qwen3-8B-GGUF"
    )
    q4, q8 = qwen8.quants

    assert q4.ollama_model == "qwen3:8b"  # the library default is Q4_K_M
    assert q8.ollama_model == "hf.co/Qwen/Qwen3-8B-GGUF:Q8_0"
    assert q8.lmstudio_model == "https://huggingface.co/Qwen/Qwen3-8B-GGUF"


def test_quants_from_tree_skips_shards_projectors_and_subfolders():
    tree = [
        {"type": "file", "path": "M-Q8_0.gguf", "size": 8},
        {"type": "file", "path": "M-Q4_K_M.gguf", "size": 4},
        {"type": "file", "path": "M-UD-Q4_K_XL.gguf", "size": 5},
        {"type": "file", "path": "mmproj-model-f16.gguf", "size": 1},
        {"type": "file", "path": "M-Q6_K-00001-of-00002.gguf", "size": 3},
        {"type": "file", "path": "BF16/M-BF16.gguf", "size": 16},
        {"type": "directory", "path": "BF16"},
        {"type": "file", "path": "README.md", "size": 1},
    ]

    quants = model_hub.quants_from_tree("o/M-GGUF", tree)

    assert [q.name for q in quants] == ["Q4_K_M", "UD-Q4_K_XL", "Q8_0"]
    assert quants[0].ollama_model == "hf.co/o/M-GGUF:Q4_K_M"


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "30B-A3B"),
        ("Qwen/Qwen3-8B-GGUF", "8B"),
        ("lmstudio-community/gemma-3-4b-it-GGUF", "4B"),
        ("someone/Coder-Next-GGUF", ""),
    ],
)
def test_params_from_name(name, params):
    assert model_hub.params_from_name(name) == params


def test_search_huggingface_parses_listing_and_caches(monkeypatch):
    model_hub._search_cache.clear()
    calls: list[str] = []

    def fake_fetch(url: str):
        calls.append(url)
        if "/tree/main" in url:
            if "NoFiles" in url:
                return [{"type": "file", "path": "README.md", "size": 1}]
            return [
                {"type": "file", "path": "Qwen3-8B-Q4_K_M.gguf", "size": 5_000_000_000}
            ]
        return [
            {
                "id": "Qwen/Qwen3-8B-GGUF",
                "downloads": 864743,
                "gguf": {
                    "architecture": "qwen3",
                    "context_length": 40960,
                    "chat_template": "{% if tools %}",
                },
            },
            {"id": "x/NoFiles-GGUF", "gguf": {}},
        ]

    monkeypatch.setattr(model_hub, "_fetch", fake_fetch)

    models = model_hub.search_huggingface("  qwen3   8b ")
    again = model_hub.search_huggingface("QWEN3 8B")

    assert [m.repo for m in models] == ["Qwen/Qwen3-8B-GGUF"]
    assert models[0].supports_tools is True
    assert models[0].max_context == 40960
    assert "search=qwen3+8b" in calls[0]
    assert len(calls) == 3  # one listing, two trees; the second search is cached
    assert again[0].repo == models[0].repo


def test_with_fit_reports_the_largest_context_that_fits():
    jetson = MachineSpec(
        memory_kind="unified",
        gpu_gb=8,
        ram_gb=8,
        reserve_gb=1.5,
        gpu_wired_fraction=1.0,
    )
    big = MachineSpec(memory_kind="unified", gpu_gb=36, ram_gb=36, reserve_gb=4)
    qwen4 = next(
        m for m in model_hub.curated_models() if m.repo == "Qwen/Qwen3-4B-GGUF"
    )

    on_jetson = model_hub.with_fit([qwen4.model_copy(deep=True)], jetson)[0].quants[0]
    on_big = model_hub.with_fit([qwen4.model_copy(deep=True)], big)[0].quants[0]

    assert on_jetson.verdict == Verdict.fail  # not at the 32k default
    assert on_jetson.fit_context is not None and on_jetson.fit_context < 32768
    assert on_big.verdict == Verdict.ok
    assert on_big.fit_context is not None and on_big.fit_context >= 32768


# --- Endpoints -------------------------------------------------------------------


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_hub_search_without_query_returns_the_curated_list():
    result = local_api.hub_search(local_api.HubSearchRequest())
    assert result["error"] == ""
    assert len(result["models"]) == len(model_hub.curated_models())


def test_hub_search_reports_an_unreachable_hub(monkeypatch):
    def down(_query):
        raise model_hub.HubUnavailable("no internet")

    monkeypatch.setattr(model_hub, "search_huggingface", down)

    result = local_api.hub_search(local_api.HubSearchRequest(query="qwen"))
    assert result == {"models": [], "error": "no internet"}


@pytest.mark.parametrize(
    "model",
    [
        "qwen3:8b",
        "hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M",
        "qwen/qwen3-8b",
        "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
    ],
)
def test_pull_request_accepts_model_references(model):
    local_api.PullStartRequest(kind="ollama", url="http://box:11434", model=model)


@pytest.mark.parametrize(
    "model",
    [
        "",
        "qwen3 8b",
        "https://evil.example/x",
        "http://huggingface.co/x",
        "hf.co/x:y://z",
        "-rf",
        "qwen3;rm",
        "a\nb",
    ],
)
def test_pull_request_rejects_anything_else(model):
    with pytest.raises(ValidationError):
        local_api.PullStartRequest(kind="ollama", url="http://box:11434", model=model)


class _InlineThread:
    """Stands in for ``threading.Thread``: runs the target on ``start()``."""

    def __init__(self, target, args, daemon) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self) -> None:
        self.target(*self.args)


def test_start_pull_returns_the_job_and_the_hosts_runs(monkeypatch):
    server = _FakeServer("http://box:11434", PullProgress(status="completed"))
    monkeypatch.setattr(servers, "make_server", lambda _kind, _url: server)
    monkeypatch.setattr(local_api, "_gateway_routing", lambda: ({}, False))
    monkeypatch.setattr(
        pull_jobs.threading,
        "Thread",
        _InlineThread,
    )

    with _session() as session:
        result = local_api.start_pull(
            local_api.PullStartRequest(
                kind="ollama", url="http://box:11434", model="qwen3:8b"
            ),
            session,
        )

    assert result["job"]["model"] == "qwen3:8b"
    assert result["guard"]["clear"] is True
    polled = local_api.get_pull(result["job"]["id"])
    assert polled.status == "completed"


def test_start_pull_on_a_busy_host_is_409(monkeypatch):
    pull_jobs.start_pull(
        _FakeServer("http://box:11434", PullProgress(status="completed")),
        "qwen3:8b",
        executor=_never,
    )
    monkeypatch.setattr(
        servers,
        "make_server",
        lambda _kind, _url: _FakeServer(
            "http://box:11434", PullProgress(status="completed")
        ),
    )

    with _session() as session, pytest.raises(HTTPException) as exc:
        local_api.start_pull(
            local_api.PullStartRequest(
                kind="ollama", url="http://box:11434", model="qwen3:4b"
            ),
            session,
        )
    assert exc.value.status_code == 409


def test_start_pull_rejects_a_bad_server_url():
    with _session() as session, pytest.raises(HTTPException) as exc:
        local_api.start_pull(
            local_api.PullStartRequest(
                kind="ollama", url="file:///etc/passwd", model="qwen3:8b"
            ),
            session,
        )
    assert exc.value.status_code == 422


def test_get_pull_unknown_is_404():
    with pytest.raises(HTTPException) as exc:
        local_api.get_pull("nope")
    assert exc.value.status_code == 404


# --- Review follow-ups -------------------------------------------------------------


def test_lmstudio_downloading_without_a_job_id_is_a_failure(monkeypatch):
    router = _Router({"/api/v1/models/download": [{"status": "downloading"}]})
    monkeypatch.setattr(servers, "_http", router)

    final = servers.LMStudioServer("http://box:1234").pull(
        "qwen/qwen3-8b", on_progress=lambda _p: None, sleep=lambda _s: None
    )

    assert final.status == "failed"
    assert "job id" in final.error
    assert len(router.calls) == 1


def test_lmstudio_gives_up_after_ten_minutes_without_progress(monkeypatch):
    stuck = {"job_id": "j", "status": "downloading", "downloaded_bytes": 5}
    router = _Router({"/api/v1/models/download": [stuck], "/status/j": [stuck]})
    monkeypatch.setattr(servers, "_http", router)
    now = [0.0]

    def clock() -> float:
        now[0] += 120.0  # two minutes per reading
        return now[0]

    final = servers.LMStudioServer("http://box:1234").pull(
        "qwen/qwen3-8b",
        on_progress=lambda _p: None,
        sleep=lambda _s: None,
        clock=clock,
    )

    assert final.status == "failed"
    assert "no progress" in final.error
    assert final.completed_bytes == 5


def test_a_pull_that_returns_still_downloading_fails_and_frees_the_host():
    server = _FakeServer("http://box:11434", PullProgress(status="downloading"))

    job = pull_jobs.start_pull(server, "qwen3:8b", executor=_sync)

    assert job.status == "failed"
    assert pull_jobs._running_hosts == {}


def test_a_thread_that_cannot_start_fails_the_job_and_frees_the_host():
    def cannot_start(_fn, *_args):
        raise RuntimeError("can't start new thread")

    server = _FakeServer("http://box:11434", PullProgress(status="completed"))
    job = pull_jobs.start_pull(server, "qwen3:8b", executor=cannot_start)

    assert job.status == "failed"
    assert "can't start" in job.error
    assert pull_jobs._running_hosts == {}


def test_finished_jobs_are_pruned_to_the_limit():
    for i in range(pull_jobs._MAX_FINISHED_JOBS + 5):
        pull_jobs.start_pull(
            _FakeServer(f"http://box{i}:11434", PullProgress(status="completed")),
            "qwen3:8b",
            executor=_sync,
        )

    assert len(pull_jobs._jobs) == pull_jobs._MAX_FINISHED_JOBS


@pytest.mark.parametrize(
    "model",
    ["evil.example/ns/model:tag", "localhost:5000/m:tag", "192.168.0.9/x/y:z"],
)
def test_pull_request_refuses_other_registries(model):
    with pytest.raises(ValidationError):
        local_api.PullStartRequest(kind="ollama", url="http://box:11434", model=model)


@pytest.mark.parametrize(
    "model",
    ["user/model:tag", "registry.ollama.ai/library/qwen3:8b", "hf.co/a/b:Q4_K_M"],
)
def test_pull_request_accepts_known_registries(model):
    local_api.PullStartRequest(kind="ollama", url="http://box:11434", model=model)
