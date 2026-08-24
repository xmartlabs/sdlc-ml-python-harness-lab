"""Execution backends for benchmark runs.

The Celery task talks to a `RunExecutor` port; two adapters implement it:

- `SubprocessRunExecutor`: runs the command as a local child process (used by
  tests and as an escape hatch via HARNESS_LAB_EXECUTION_BACKEND=subprocess).
- `DockerRunExecutor`: one Docker container per run (SWE-bench style), created
  through the host daemon (docker-out-of-docker), bind-mounting the host repo
  so workspace/ and data/runs/ artifacts land on the host filesystem.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import threading
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, Protocol

from harness_lab_app.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_LOCALHOST_RE = re.compile(r"\blocalhost\b|\b127\.0\.0\.1\b")

# Env vars that point at services running on the host machine; inside a run
# container "localhost" must be rewritten to the docker host alias.
_HOST_URL_ENV_VARS = (
    "GATEWAY_BASE_URL",
    "LMSTUDIO_BASE_URL",
    "OLLAMA_BASE_URL",
    "LANGFUSE_HOST",
    "MLFLOW_TRACKING_URI",
)


# The one skill catalog a run is allowed to see, and the mirrors of it that must
# stay hidden.
#
# `adapters/registry.toml` materializes the same governed skills once per AI
# tool — `.claude/skills` (symlinks), `.agents/skills` (copies, Antigravity
# cannot follow symlinks), `.opencode/skills` and `.codex/skills`. On the host
# that is correct: each tool reads its own directory. In the runner it is not,
# because the whole repo is bind-mounted at /app and opencode scans all four,
# so the same 24 skills are discovered four times.
#
# The cost is not the "duplicate skill name" warnings (240 of them in run
# tetris-v1-K-064); it is that the skill catalog is part of the system prompt,
# re-sent and re-prefilled on every turn of every step. Four copies of 428 KB
# of SKILL.md is minutes per turn against a local model.
#
# Masking is done with empty tmpfs mounts rather than by deleting directories,
# because the host tree is the tools' source of truth and a run must never
# mutate it. `tests/harness_lab_app/test_executors.py` asserts this list still
# covers every tool in the registry.
RUNNER_SKILLS_DIR = ".claude/skills"
RUNNER_MASKED_SKILL_DIRS = (
    ".agents/skills",
    ".opencode/skills",
    ".codex/skills",
)


def runner_skill_masks(mount_root: str = "/app") -> dict[str, str]:
    """tmpfs mounts that hide every duplicate skill catalog from a run.

    Keys are absolute container paths, values the docker-py tmpfs options
    string (empty = defaults). Mounted over the repo bind, so opencode
    discovers exactly one catalog: :data:`RUNNER_SKILLS_DIR`.
    """
    root = mount_root.rstrip("/")
    return {f"{root}/{directory}": "" for directory in RUNNER_MASKED_SKILL_DIRS}


def rewrite_localhost(url: str, alias: str) -> str:
    """Rewrite localhost/127.0.0.1 host references to the docker host alias."""
    return _LOCALHOST_RE.sub(alias, url)


def build_run_environment() -> dict[str, str]:
    """Environment for a run container: .env values with host URLs rewritten.

    `scripts/create_run.py:_load_env` uses `os.environ.setdefault`, so values
    injected here win over the `.env` file bind-mounted inside the container.
    """
    settings = get_settings()
    # IS_SANDBOX=1: the run container is an isolated sandbox; Claude Code
    # refuses --dangerously-skip-permissions as root unless this is set (same
    # escape hatch Anthropic's devcontainers use). Harmless for other CLIs.
    # SDL_*=dummy makes the container honestly headless. It used to be a line
    # of prose in the case prompt that the agent had to retype on every single
    # invocation — measured compliance was 72% (run K-036) and 40% (K-038), and
    # each miss left a pygame loop running forever, holding opencode's one
    # persistent shell open until the whole run wedged. That is an environment
    # fact, not something to test a model's memory on, so the environment now
    # states it. A case that still passes the prefix by hand is unaffected.
    env: dict[str, str] = {
        "PYTHONUNBUFFERED": "1",
        "IS_SANDBOX": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
    }
    env_file = settings.repo_root / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    for key in _HOST_URL_ENV_VARS:
        if env.get(key):
            env[key] = rewrite_localhost(env[key], settings.docker_host_alias)
    return env


class RunHandle(Protocol):
    def iter_lines(self) -> Iterator[tuple[str, str]]:
        """Yield (level, message) pairs until the run's output is exhausted."""
        ...

    def stop(self) -> None:
        """Request termination of the running command."""
        ...

    def wait(self) -> int:
        """Block until the run finishes and return its exit code."""
        ...


class RunExecutor(Protocol):
    def start(self, run_id: str, command: list[str]) -> RunHandle: ...


class _SubprocessHandle:
    def __init__(self, process: subprocess.Popen[str]):
        self.process = process
        self._lines: Queue[tuple[str, str]] = Queue()
        self._threads = [
            threading.Thread(
                target=self._pump, args=(process.stdout, "info"), daemon=True
            ),
            threading.Thread(
                target=self._pump, args=(process.stderr, "error"), daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    def _pump(self, stream: Any, level: str) -> None:
        for line in iter(stream.readline, ""):
            self._lines.put((level, line.rstrip("\n")))
        stream.close()

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        while (
            any(thread.is_alive() for thread in self._threads)
            or not self._lines.empty()
        ):
            try:
                yield self._lines.get(timeout=0.1)
            except Empty:
                continue

    def stop(self) -> None:
        self.process.terminate()

    def wait(self) -> int:
        for thread in self._threads:
            thread.join()
        return self.process.wait()


class SubprocessRunExecutor:
    """Run the command as a local child process (legacy worker behavior)."""

    def start(self, run_id: str, command: list[str]) -> RunHandle:  # noqa: ARG002
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return _SubprocessHandle(process)


class _DockerHandle:
    def __init__(self, container: Any):
        self.container = container
        self._stopped = False

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        # `logs()` does not demux stdout/stderr; bench_run.py already merges
        # opencode stderr into stdout, so a single "info" level is fine.
        buffer = b""
        for chunk in self.container.logs(stream=True, follow=True):
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    yield ("info", line)
        tail = buffer.decode("utf-8", errors="replace").strip()
        if tail:
            yield ("info", tail)

    def stop(self) -> None:
        self._stopped = True
        self.container.stop(timeout=10)

    def wait(self) -> int:
        try:
            result = self.container.wait()
            return int(result.get("StatusCode", -1))
        finally:
            # Cleanup is best-effort; the container may already be gone.
            with contextlib.suppress(Exception):
                self.container.remove(force=True)


def container_name_for_run(run_id: str) -> str:
    # Docker names only allow [a-zA-Z0-9][a-zA-Z0-9_.-]*.
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)
    return f"harness-run-{safe}"


class DockerRunExecutor:
    """One container per run, created via the host Docker daemon (DooD)."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import docker  # local import: the api process never needs the socket

        return docker.from_env()

    def start(self, run_id: str, command: list[str]) -> RunHandle:
        settings = get_settings()
        host_repo_root = settings.host_repo_root or str(settings.repo_root)
        if not host_repo_root:
            raise RuntimeError(
                "HARNESS_LAB_HOST_REPO_ROOT must be set for the docker backend"
            )
        client = self._client()
        self._require_local_image(client, settings.runner_image)
        name = container_name_for_run(run_id)
        self._remove_stale_container(client, name)
        container = client.containers.run(
            image=settings.runner_image,
            command=command,
            detach=True,
            name=name,
            working_dir="/app",
            volumes={host_repo_root: {"bind": "/app", "mode": "rw"}},
            # Empty tmpfs over the duplicate skill catalogs: one catalog in the
            # system prompt instead of four (see RUNNER_MASKED_SKILL_DIRS).
            tmpfs=runner_skill_masks(),
            environment=build_run_environment(),
            extra_hosts={settings.docker_host_alias: "host-gateway"},
            labels={"harness-lab.run-id": run_id},
        )
        return _DockerHandle(container)

    @staticmethod
    def _require_local_image(client: Any, image: str) -> None:
        """Fail fast and clearly if the runner image was never built.

        `client.containers.run()` auto-pulls from the registry when an image
        is missing locally, which for a local-only image like
        `harness-lab-runner` fails with a confusing 404 ImageNotFound. Check
        first so the error tells the operator exactly what to run.
        """
        try:
            client.images.get(image)
        except Exception as exc:
            raise RuntimeError(
                f"Runner image '{image}' was not found locally. Build it from "
                "the web UI: Health -> Docker runtime -> 'Build runner image' "
                "(or `make app-up` from a shell)."
            ) from exc

    @staticmethod
    def _remove_stale_container(client: Any, name: str) -> None:
        """Remove a leftover container with the same name (e.g. after retry)."""
        try:
            stale = client.containers.get(name)
        except Exception:
            return
        stale.remove(force=True)


def get_executor() -> RunExecutor:
    settings = get_settings()
    if settings.execution_backend == "subprocess":
        return SubprocessRunExecutor()
    return DockerRunExecutor()
