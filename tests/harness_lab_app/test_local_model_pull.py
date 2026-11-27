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
    LoadCheck,
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
        self.checked: list[str] = []

    load_check = LoadCheck(status="ok")

    def check_load(self, model_id):
        self.checked.append(model_id)
        if isinstance(self.load_check, Exception):
            raise self.load_check
        return self.load_check

    def version(self):
        return ""

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
    assert all(q.common for q in quants)
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


# --- Review of the first Jetson test (2026-11-26) -------------------------------
# A Gemma 4 GGUF from Hugging Face downloaded fine onto Ollama 0.24 and then
# failed in step 4 with "unable to load model: <blob>". Every case below is a
# piece of that session.

_JETSON = MachineSpec(
    memory_kind="unified", gpu_gb=8, ram_gb=8, reserve_gb=1.5, gpu_wired_fraction=1.0
)


def test_a_completed_download_is_load_checked():
    server = _FakeServer("http://box:11434", PullProgress(status="completed"))
    server.load_check = LoadCheck(status="failed", error="Ollama 0.24.0 could not")

    job = pull_jobs.start_pull(server, "hf.co/o/m:Q4_K_M", executor=_sync)

    assert server.checked == ["hf.co/o/m:Q4_K_M"]
    assert job.status == "completed"  # the file is on the server
    assert job.load_check == "failed"
    assert "0.24.0" in job.load_error


def test_a_failed_download_is_not_load_checked():
    server = _FakeServer(
        "http://box:11434", PullProgress(status="failed", error="no manifest")
    )

    job = pull_jobs.start_pull(server, "qwen3:8b", executor=_sync)

    assert server.checked == []
    assert job.load_check == ""


def test_a_load_check_that_raises_keeps_the_download():
    server = _FakeServer("http://box:11434", PullProgress(status="completed"))
    server.load_check = DirectHostUnreachable("gone")

    job = pull_jobs.start_pull(server, "qwen3:8b", executor=_sync)

    assert job.status == "completed"
    assert job.load_check == "failed"


def test_ollama_load_check_never_evicts_a_loaded_model(monkeypatch):
    calls: list[str] = []

    def fake_http(url, timeout, payload=None):
        calls.append(url)
        if url.endswith("/api/ps"):
            return {"models": [{"name": "qwen3:4b", "model": "qwen3:4b"}]}
        raise AssertionError(f"must not load: {url}")

    monkeypatch.setattr(servers, "_http", fake_http)

    check = servers.OllamaServer("http://jetson:11434").check_load("gemma")

    assert check.status == "skipped"
    assert not any(url.endswith("/api/generate") for url in calls)


def test_ollama_load_check_explains_an_old_server(monkeypatch):
    sent: list[dict] = []

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/ps"):
            return {"models": []}
        if url.endswith("/api/version"):
            return {"version": "0.24.0"}
        if url.endswith("/api/generate"):
            sent.append(payload)
            raise ServerError("unable to load model: /usr/share/ollama/blobs/sha256-1")
        raise AssertionError(url)

    monkeypatch.setattr(servers, "_http", fake_http)

    check = servers.OllamaServer("http://jetson:11434").check_load("hf.co/x/g:Q3_K_M")

    assert check.status == "failed"
    assert "Ollama 0.24.0" in check.error
    assert "install.sh" in check.error
    assert "unable to load model" in check.error  # Ollama's own words stay
    assert sent[0]["keep_alive"] != 0  # 0 with an empty prompt only unloads
    assert sent[0]["options"]["num_ctx"] == 2048


def test_ollama_load_check_loads_then_unloads(monkeypatch):
    sent: list[dict] = []

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/ps"):
            return {"models": []}
        if url.endswith("/api/generate"):
            sent.append(payload)
            return {"done": True, "done_reason": "load"}
        raise AssertionError(url)

    monkeypatch.setattr(servers, "_http", fake_http)

    check = servers.OllamaServer("http://jetson:11434").check_load("qwen3:4b")

    assert check.status == "ok"
    assert [p.get("keep_alive") for p in sent] == ["1m", 0]
    assert sent[0]["prompt"] == ""


@pytest.mark.parametrize(
    ("error", "version", "says"),
    [
        ("unable to load model: /b", "0.24.0", "Update Ollama"),
        ("unable to load model: /b", "0.40.18", "not be supported by this Ollama"),
        ("unable to load model: /b", "", "not be supported by this Ollama"),
        ("model requires more system memory", "0.24.0", None),
    ],
)
def test_explain_ollama_load_error(error, version, says):
    explained = servers.explain_ollama_load_error(error, version)
    if says is None:
        assert explained == error  # other errors already say what is wrong
    else:
        assert says in explained


def test_version_tuple():
    assert servers.version_tuple("0.24.0") < servers.version_tuple("0.30.0")
    assert servers.version_tuple("v0.30.0-rc1") == (0, 30, 0)
    assert servers.version_tuple("") == ()


def test_load_endpoint_explains_ollamas_unable_to_load(monkeypatch):
    class _OldOllama:
        kind = "ollama"
        base_url = "http://jetson:11434"

        def list_models(self):
            return []

        def load(self, model_id, config):
            raise ServerError("unable to load model: /usr/share/ollama/blobs/sha256-1")

        def version(self):
            return "0.24.0"

    monkeypatch.setattr(servers, "make_server", lambda _kind, _url: _OldOllama())
    monkeypatch.setattr(local_api, "_gateway_routing", lambda: ({}, False))

    with _session() as session, pytest.raises(HTTPException) as exc:
        local_api.load(
            local_api.LoadRequest(
                kind="ollama",
                url="http://jetson:11434",
                model_id="hf.co/x/g:Q3_K_M",
                config={"context_length": 8192},
            ),
            session,
        )

    assert exc.value.status_code == 422
    assert "Ollama 0.24.0" in str(exc.value.detail)


def test_hugging_face_pulls_name_their_quantization(monkeypatch):
    tags = {
        "models": [
            {
                "name": "hf.co/turintech/gemma-4-E4B-it-Artemis-GGUF:Q3_K_M",
                "size": 4545477908,
                "details": {"family": "gemma4", "quantization_level": "unknown"},
            },
            {
                "name": "mine:latest",
                "size": 1,
                "details": {"family": "llama", "quantization_level": "unknown"},
            },
        ]
    }

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/tags"):
            return tags
        if url.endswith("/api/ps"):
            return {"models": []}
        raise ServerError("no show")

    monkeypatch.setattr(servers, "_http", fake_http)
    gemma, mine = servers.OllamaServer("http://jetson:11434").list_models()

    assert gemma.quantization == "Q3_K_M"
    assert mine.quantization == "unknown"  # a local tag is not a quantization


def test_gemma4_shape_from_show_defers_to_the_table(monkeypatch):
    """Ollama's flat reading of Gemma 4 counts every layer's KV at full width."""
    show = {
        "model_info": {
            "general.architecture": "gemma4",
            "gemma4.block_count": 42,
            "gemma4.attention.head_count_kv": 2,
            "gemma4.attention.key_length": 512,
            "gemma4.attention.key_length_swa": 256,
            "gemma4.context_length": 131072,
        }
    }
    tags = {
        "models": [
            {
                "name": "hf.co/t/gemma-4-E4B-it-GGUF:Q3_K_M",
                "size": 4545477908,
                "details": {"family": "gemma4", "parameter_size": "7.46B"},
            }
        ]
    }

    def fake_http(url, timeout, payload=None):
        if url.endswith("/api/tags"):
            return tags
        if url.endswith("/api/ps"):
            return {"models": []}
        return show

    monkeypatch.setattr(servers, "_http", fake_http)
    (model,) = servers.OllamaServer("http://jetson:11434").list_models()

    assert model.shape is None  # the table's Gemma 4 shape is used instead
    from harness_lab_app.local_tuning.catalog import DEFAULT_LOAD
    from harness_lab_app.local_tuning.memory_estimate import estimate_memory

    estimate = estimate_memory(model, DEFAULT_LOAD, _JETSON)
    assert estimate.breakdown.kv_cache_gb < 1.0  # was 3.4 with the flat reading
    assert estimate.verdict != Verdict.fail


def test_gemma4_e4b_fits_a_jetson_at_32k_and_gemma4_12b_does_not():
    e4b, small, big = (
        model_hub.with_fit(
            [
                model_hub.HubModel(
                    source="huggingface",
                    repo=repo,
                    params=params,
                    arch="gemma4",
                    max_context=131072,
                    quants=[model_hub._quant(repo, "Q4_K_M", size)],
                )
            ],
            _JETSON,
        )[0].quants[0]
        for repo, params, size in [
            ("unsloth/gemma-4-E4B-it-GGUF", "4B", 4_977_171_584),
            ("unsloth/gemma-4-E2B-it-GGUF", "2B", 3_106_738_272),
            ("unsloth/gemma-4-12b-it-GGUF", "12B", 7_121_861_440),
        ]
    )

    assert e4b.verdict in (Verdict.ok, Verdict.tight)
    assert small.verdict == Verdict.ok
    assert big.verdict == Verdict.fail


def test_search_marks_split_repos_and_orders_common_quants_first(monkeypatch):
    model_hub._search_cache.clear()

    def fake_fetch(url: str):
        if "/tree/main" in url:
            return [
                {"type": "file", "path": "g-UD-IQ2_M.gguf", "size": 2},
                {"type": "file", "path": "g-Q8_0.gguf", "size": 8},
                {"type": "file", "path": "g-Q3_K_M.gguf", "size": 3},
                {"type": "file", "path": "g-BF16.gguf", "size": 16},
                {"type": "file", "path": "g-Q4_K_M.gguf", "size": 4},
                {"type": "file", "path": "mmproj-F16.gguf", "size": 1},
            ]
        return [{"id": "u/gemma-4-E4B-it-GGUF", "gguf": {"architecture": "gemma4"}}]

    monkeypatch.setattr(model_hub, "_fetch", fake_fetch)

    (model,) = model_hub.search_huggingface("gemma 4")

    assert model.separate_projector is True
    assert [q.name for q in model.quants] == [
        "Q3_K_M",
        "Q4_K_M",
        "Q8_0",
        "UD-IQ2_M",
        "BF16",
    ]
    assert [q.common for q in model.quants] == [True, True, True, False, False]
    assert {q.min_ollama_version for q in model.quants} == {"0.30.0"}


def test_a_repo_without_a_vision_file_names_no_ollama_floor(monkeypatch):
    model_hub._search_cache.clear()

    def fake_fetch(url: str):
        if "/tree/main" in url:
            return [{"type": "file", "path": "Qwen3-8B-Q4_K_M.gguf", "size": 5}]
        return [{"id": "Qwen/Qwen3-8B-GGUF", "gguf": {"architecture": "qwen3"}}]

    monkeypatch.setattr(model_hub, "_fetch", fake_fetch)

    (model,) = model_hub.search_huggingface("qwen3 8b")

    assert model.separate_projector is False
    assert model.quants[0].min_ollama_version == ""


def test_curated_gemma4_uses_the_library_build_with_its_floor():
    e4b = next(m for m in model_hub.curated_models() if m.label == "Gemma 4 E4B")
    q4, q8 = e4b.quants

    assert q4.ollama_model == "gemma4:e4b"
    assert q4.min_ollama_version == "0.30.9"  # what its manifest requires
    assert q8.ollama_model == "hf.co/unsloth/gemma-4-E4B-it-GGUF:Q8_0"
    assert q8.min_ollama_version == "0.30.0"


def test_server_test_reports_the_version(monkeypatch):
    class _Server:
        def list_models(self):
            return []

        def version(self):
            return "0.24.0"

    monkeypatch.setattr(servers, "make_server", lambda _kind, _url: _Server())

    result = local_api.test_server(
        local_api.ServerTestRequest(kind="ollama", url="http://jetson:11434")
    )

    assert result["version"] == "0.24.0"
