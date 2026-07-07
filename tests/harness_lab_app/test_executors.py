from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness_lab_app import executors
from harness_lab_app.executors import (
    DockerRunExecutor,
    SubprocessRunExecutor,
    build_run_environment,
    container_name_for_run,
    rewrite_localhost,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:4000/v1", "http://host.docker.internal:4000/v1"),
        ("http://127.0.0.1:1234/v1", "http://host.docker.internal:1234/v1"),
        (
            "http://localhost:4000/localhost",
            "http://host.docker.internal:4000/host.docker.internal",
        ),
        ("http://example.com:4000/v1", "http://example.com:4000/v1"),
        ("", ""),
    ],
)
def test_rewrite_localhost(url: str, expected: str) -> None:
    assert rewrite_localhost(url, "host.docker.internal") == expected


def test_container_name_for_run_sanitizes_id() -> None:
    assert container_name_for_run("tetris-v1-K-001") == "harness-run-tetris-v1-K-001"
    assert container_name_for_run("weird id/x") == "harness-run-weird-id-x"


class FakeSettings:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.docker_host_alias = "host.docker.internal"
        self.host_repo_root = "/host/repo"
        self.runner_image = "harness-lab-runner:latest"
        self.execution_backend = "docker"


def test_build_run_environment_rewrites_host_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "# comment\n"
        "GATEWAY_BASE_URL=http://localhost:4000/v1\n"
        "GATEWAY_TOKEN=sk-test\n"
        "LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1\n"
        "UNRELATED=keep-me\n"
    )
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    assert env["GATEWAY_BASE_URL"] == "http://host.docker.internal:4000/v1"
    assert env["LMSTUDIO_BASE_URL"] == "http://host.docker.internal:1234/v1"
    assert env["GATEWAY_TOKEN"] == "sk-test"
    assert env["UNRELATED"] == "keep-me"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_build_run_environment_without_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    assert env == {"PYTHONUNBUFFERED": "1"}


def test_subprocess_executor_streams_and_waits() -> None:
    handle = SubprocessRunExecutor().start(
        "stream-1",
        ["python3", "-c", "print('one'); print('two')"],
    )
    messages = [message for _, message in handle.iter_lines()]
    assert handle.wait() == 0
    assert messages.index("one") < messages.index("two")


def test_subprocess_executor_stop_terminates_process() -> None:
    handle = SubprocessRunExecutor().start(
        "stop-1",
        ["python3", "-c", "import time; time.sleep(30); print('late')"],
    )
    handle.stop()
    messages = [message for _, message in handle.iter_lines()]
    assert handle.wait() != 0
    assert "late" not in messages


class FakeContainer:
    def __init__(self, log_chunks: list[bytes], status_code: int = 0):
        self._log_chunks = log_chunks
        self._status_code = status_code
        self.stopped = False
        self.removed = False
        self.status = "running"

    def logs(self, **_kwargs: Any):
        yield from self._log_chunks

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True

    def wait(self) -> dict[str, int]:
        return {"StatusCode": self._status_code}

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer):
        self._container = container
        self.run_kwargs: dict[str, Any] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        self.run_kwargs = kwargs
        return self._container

    def get(self, name: str) -> FakeContainer:
        raise RuntimeError(f"No such container: {name}")


class FakeDockerClient:
    def __init__(self, container: FakeContainer):
        self.containers = FakeContainers(container)


def test_docker_executor_configures_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("GATEWAY_BASE_URL=http://localhost:4000/v1\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    container = FakeContainer([b"step one\nstep two\n"])
    client = FakeDockerClient(container)
    executor = DockerRunExecutor(client_factory=lambda: client)

    handle = executor.start("tetris-v1-K-001", ["python", "scripts/bench_run.py"])
    messages = [message for _, message in handle.iter_lines()]
    exit_code = handle.wait()

    kwargs = client.containers.run_kwargs
    assert kwargs["image"] == "harness-lab-runner:latest"
    assert kwargs["name"] == "harness-run-tetris-v1-K-001"
    assert kwargs["volumes"] == {"/host/repo": {"bind": "/app", "mode": "rw"}}
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert (
        kwargs["environment"]["GATEWAY_BASE_URL"]
        == "http://host.docker.internal:4000/v1"
    )
    assert kwargs["labels"] == {"harness-lab.run-id": "tetris-v1-K-001"}
    assert messages == ["step one", "step two"]
    assert exit_code == 0
    assert container.removed is True


def test_docker_executor_stop_stops_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    container = FakeContainer([], status_code=137)
    client = FakeDockerClient(container)
    executor = DockerRunExecutor(client_factory=lambda: client)

    handle = executor.start("stop-run", ["python", "scripts/bench_run.py"])
    handle.stop()

    assert container.stopped is True
    assert handle.wait() == 137
    assert container.removed is True
