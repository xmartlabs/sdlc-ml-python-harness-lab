from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness_lab_app import executors
from harness_lab_app.executors import (
    RUNNER_MASKED_SKILL_DIRS,
    RUNNER_SKILLS_DIR,
    DockerRunExecutor,
    SubprocessRunExecutor,
    build_run_environment,
    container_name_for_run,
    rewrite_localhost,
    runner_skill_masks,
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
    # Claude Code refuses --dangerously-skip-permissions as root without this.
    assert env["IS_SANDBOX"] == "1"


def test_build_run_environment_without_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    # The SDL pair is part of the baseline, not something a case opts into: the
    # run container has no display, and leaving that for the agent to remember
    # is what let pygame windows hang whole runs (K-036, K-038).
    assert env == {
        "PYTHONUNBUFFERED": "1",
        "IS_SANDBOX": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
    }


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


class FakeImages:
    def __init__(self, known: bool = True):
        self.known = known

    def get(self, name: str) -> Any:
        if not self.known:
            raise RuntimeError(f"No such image: {name}")
        return object()


class FakeDockerClient:
    def __init__(self, container: FakeContainer, image_known: bool = True):
        self.containers = FakeContainers(container)
        self.images = FakeImages(image_known)


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
    # One skill catalog reaches the agent; the per-tool mirrors are masked.
    assert kwargs["tmpfs"] == {
        "/app/.agents/skills": "",
        "/app/.opencode/skills": "",
        "/app/.codex/skills": "",
    }
    assert f"/app/{RUNNER_SKILLS_DIR}" not in kwargs["tmpfs"]
    assert messages == ["step one", "step two"]
    assert exit_code == 0
    assert container.removed is True


def test_docker_executor_raises_clear_error_when_image_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]), image_known=False)
    executor = DockerRunExecutor(client_factory=lambda: client)

    with pytest.raises(RuntimeError, match="was not found locally"):
        executor.start("tetris-v1-K-001", ["python", "scripts/bench_run.py"])


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


def test_runner_skill_masks_never_hides_the_catalog_in_use() -> None:
    masks = runner_skill_masks()

    assert f"/app/{RUNNER_SKILLS_DIR}" not in masks
    assert all(path.startswith("/app/") for path in masks)
    assert set(masks.values()) == {""}


def test_runner_skill_masks_cover_every_tool_in_the_registry() -> None:
    """The mask list must not drift from ``adapters/registry.toml``.

    Adding an AI tool there materializes a fifth skill catalog into the repo,
    which the runner would then load on top of the one it is measuring. The
    registry is the source of truth; this test is the tripwire that says so
    when a tool is added and :data:`RUNNER_MASKED_SKILL_DIRS` is not updated.
    """
    import tomllib

    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "adapters" / "registry.toml").read_text()
    )
    materialized = {
        entry["native_skills_dir"]
        for entry in registry["tool"]
        if entry.get("native_skills_dir")
        and entry.get("link_strategy", "none") != "none"
    }

    assert materialized - {RUNNER_SKILLS_DIR} == set(RUNNER_MASKED_SKILL_DIRS)
