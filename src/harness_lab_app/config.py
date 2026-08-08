from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_LAB_", env_file=".env", extra="ignore"
    )

    repo_root: Path = Field(default_factory=lambda: Path.cwd())
    database_url: str = "sqlite:///data/app/harness_lab.db"
    runner_mode: str = "dry_run"
    runner_python: str | None = None

    # Queue / execution backend (Celery + one Docker container per run).
    redis_url: str = "redis://redis:6379/0"
    execution_backend: str = "docker"  # "docker" | "subprocess"
    runner_image: str = "harness-lab-runner:latest"
    # Host path of the repo, required for docker-out-of-docker bind mounts:
    # the sibling runner container is created by the HOST daemon, so bind
    # sources must be host paths, not this container's /app.
    host_repo_root: str = ""
    docker_host_alias: str = "host.docker.internal"
    run_timeout_seconds: int = 7200
    # In-platform validation sandboxes (web terminal / noVNC desktop per run).
    sandbox_image: str = "harness-lab-sandbox:latest"
    sandbox_idle_seconds: int = 3600

    # Session auth (ADR-0015). The password hash, session secret and TTL are read
    # from `.env` per request (see session_middleware) so `make set-password`
    # applies without a restart; only this deployment-time flag is cached here.
    # Default false: over plain HTTP a `Secure` cookie is silently dropped, and
    # the lab's default deployment is http://<lan-host>:8080.
    session_cookie_secure: bool = False

    @property
    def data_runs_dir(self) -> Path:
        return self.repo_root / "data" / "runs"

    @property
    def workspace_dir(self) -> Path:
        # Live working directory the runner writes into, per run
        # (workspace/<run_id>/). Mirrors WORKSPACE_ROOT in scripts/bench_start.py
        # so deletion/cleanup paths are centralized on the settings object.
        return self.repo_root / "workspace"

    def sandbox_dir(self, run_id: str, kind: str) -> Path:
        # Ephemeral, isolated copy of a run's workspace that a sandbox container
        # mounts (so exploring/running the code never mutates the artifact).
        return self.data_runs_dir / run_id / f"sandbox-{kind}"

    @property
    def benchmarks_dir(self) -> Path:
        return self.repo_root / "benchmarks"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def read_env_file_value(name: str, default: str = "") -> str:
    """Read a single KEY=VALUE from the repo `.env`.

    Used for values that are not first-class Settings fields (e.g. GATEWAY_TOKEN,
    consumed by the runner container). Mirrors the parser in
    executors.build_run_environment so the API can preflight config without
    importing worker-only code.
    """
    env_file = get_settings().repo_root / ".env"
    if not env_file.exists():
        return default
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip()
    return default
