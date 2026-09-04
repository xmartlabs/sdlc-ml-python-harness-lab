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
    # Kill a run that has produced no output for this long, even though its
    # total budget is nowhere near spent.
    #
    # The total timeout above only catches runs that are too *slow*; it cannot
    # see a run that is *stuck*, and the two look identical from outside. Run
    # tetris-v1-K-034 is the case in point: the agent shelled out to
    # `timeout 2 python main.py`, the pygame process caught SIGTERM and never
    # died, and because the surviving grandchild held the stdout pipe open
    # opencode blocked forever reading it. Not one byte of output for 47
    # minutes, state "running" in the UI, and nothing would have moved until
    # the 2-hour ceiling. Ten minutes of total silence is already pathological
    # for a harness that streams its own logs — provided the run is not simply
    # waiting on the model, which is what the next setting is for. Set to 0 to
    # disable.
    run_stall_seconds: int = 600
    # The same clock, for silence while a model request is in flight.
    #
    # It needs its own value because the two silences differ by orders of
    # magnitude. A local model says nothing at all while it processes the
    # prompt, and that is not idle time: measured against the box serving
    # qwen3.6-35b-a3b, a trivial prompt answers in 0.2s and a 24k-token one
    # takes 318s before the first token. One 600s clock for both therefore
    # killed every 35B run at its first turn (K-040/049/050, all dead in the
    # same place) while the 9B — whose worst gap was 76s — sailed through,
    # which reads as "the big model broke" and is really "the threshold was
    # below its prefill time". Forty minutes sits far above any prefill
    # measured here and far below the two-hour total, and a genuinely dead
    # endpoint is caught by the error count below long before it. 0 disables.
    run_model_wait_seconds: int = 2400
    # Give up after this many provider requests fail back-to-back with no
    # progress in between.
    #
    # opencode retries a failing stream forever, so without this a run spends
    # its whole budget on it: tetris-v1-K-029 logged 123 consecutive "Cannot
    # connect to API" errors over 70 minutes and was then recorded as a
    # two-hour timeout, which blames the model for a connectivity failure.
    # Five in a row is already conclusive — the endpoint is down, the model id
    # is wrong, or the prompt does not fit its context. Set to 0 to disable.
    run_max_stream_errors: int = 5
    # In-platform validation sandboxes (web terminal / noVNC desktop per run).
    sandbox_image: str = "harness-lab-sandbox:latest"
    sandbox_idle_seconds: int = 3600

    # Session auth (ADR-0015). The password hash, session secret and TTL are read
    # from `.env` per request (see session_middleware) so `make set-password`
    # applies without a restart; only this deployment-time flag is cached here.
    # Default false: over plain HTTP a `Secure` cookie is silently dropped, and
    # the lab's default deployment is http://<lan-host>:8080.
    session_cookie_secure: bool = False

    # GitHub slugs the improvement cycle files issues against. Defaults point at
    # the upstream trio this lab was built to measure; a fork retargets them via
    # HARNESS_LAB_TEMPLATE_REPOSITORY etc. without touching code.
    #
    # Note these are *runtime* defaults only. Run.repository persists the slug an
    # existing proposal was authored against (models.py), so changing them here
    # never rewrites history.
    template_repository: str = "xmartlabs/sdlc-ml-python-base"
    lab_repository: str = "xmartlabs/sdlc-ml-python-harness-lab"
    guide_repository: str = "xmartlabs/sdlc-harness-engineering-guide"

    # Upper bound on runs created by one POST /api/runs/batch. A four-list
    # cross-product reaches the hundreds from a single click, and each run is a
    # Docker container plus model spend.
    max_batch_runs: int = 50

    @property
    def data_runs_dir(self) -> Path:
        return self.repo_root / "data" / "runs"

    @property
    def target_mirrors_dir(self) -> Path:
        # Blobless bare mirrors of registered target repositories, kept so case
        # derivation can read history without re-cloning per request. Disposable
        # by construction: deleting it costs one slower derivation.
        return self.repo_root / "data" / "target-mirrors"

    @property
    def checkouts_dir(self) -> Path:
        # Where the operator clones a target repository with their own
        # credentials (ADR-0042). Under the lab root on purpose: runner
        # containers already bind-mount the root, so a clone here is visible to
        # API, worker and runners without a new mount or a credential in a
        # container.
        return self.repo_root / "data" / "checkouts"

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


def env_file_path() -> Path:
    """Location of the repository `.env`.

    The single seam every reader goes through, so the file can be redirected in
    one place. The test suite redirects it to a fixture (see
    ``tests/harness_lab_app/conftest.py``): reading the developer's real `.env`
    made the suite pass locally and fail on a fresh clone.
    """
    return get_settings().repo_root / ".env"


def read_env_file_value(name: str, default: str = "") -> str:
    """Read a single KEY=VALUE from the repo `.env`.

    Used for values that are not first-class Settings fields (e.g. GATEWAY_TOKEN,
    consumed by the runner container). Mirrors the parser in
    executors.build_run_environment so the API can preflight config without
    importing worker-only code.
    """
    env_file = env_file_path()
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
