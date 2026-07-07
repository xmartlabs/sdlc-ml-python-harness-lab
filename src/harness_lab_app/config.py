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
    local_username: str = "admin"
    local_password: str = "admin"
    runner_mode: str = "dry_run"
    runner_python: str | None = None

    @property
    def data_runs_dir(self) -> Path:
        return self.repo_root / "data" / "runs"

    @property
    def benchmarks_dir(self) -> Path:
        return self.repo_root / "benchmarks"


@lru_cache
def get_settings() -> Settings:
    return Settings()
