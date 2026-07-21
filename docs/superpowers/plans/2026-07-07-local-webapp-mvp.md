# Local Web App MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Docker-first local web app that manages benchmark cases, starts dry-run benchmark jobs through the existing runner contract, and lets the user inspect Tetris run artifacts.

**Architecture:** Add a new `harness_lab_app` backend package under `src/` with FastAPI, SQLModel, a synchronous MVP worker, and filesystem artifact indexing. Add a React/Vite frontend under `frontend/` with the Operational Shell layout. Add Docker Compose services for API, frontend, worker, and database while preserving all existing `make bench-*` CLI flows.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, SQLite for the first MVP DB, pytest, React, Vite, TypeScript, Docker Compose.

---

## Scope

This plan implements the MVP vertical slice. It includes Docker scaffolding, local auth stub, case CRUD, Tetris seeding, manual YAML sync status, run config creation, dry-run command preview, queued dry-run execution, artifact indexing, run detail UI, and two-run comparison basics.

This plan does not implement real SWE-bench execution, remote workers, object storage, OAuth, Celery/Redis, or full automated scoring. It preserves extension points for those later platform steps.

## File Structure

Create backend package:

- `src/harness_lab_app/__init__.py` exports package metadata.
- `src/harness_lab_app/config.py` defines environment-backed settings.
- `src/harness_lab_app/db.py` owns engine/session/init helpers.
- `src/harness_lab_app/models.py` defines `Case`, `RunConfig`, `Run`, `Artifact`, and `RunEvent`.
- `src/harness_lab_app/schemas.py` defines request/response Pydantic models.
- `src/harness_lab_app/case_service.py` owns case CRUD, clone, seed, and sync-state logic.
- `src/harness_lab_app/run_service.py` owns run config, command preview, and lifecycle transitions.
- `src/harness_lab_app/artifact_service.py` indexes and reads `data/runs/<run_id>/`.
- `src/harness_lab_app/runner_adapter.py` builds `bench_run.py` commands and fake/dry-run execution results.
- `src/harness_lab_app/worker.py` claims queued runs and executes the runner adapter.
- `src/harness_lab_app/api.py` creates the FastAPI app and routes.

Create tests:

- `tests/harness_lab_app/conftest.py`
- `tests/harness_lab_app/test_cases.py`
- `tests/harness_lab_app/test_runs.py`
- `tests/harness_lab_app/test_artifacts.py`
- `tests/harness_lab_app/test_api.py`

Create frontend:

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/tsconfig.json`
- `frontend/index.html`
- `frontend/src/main.tsx`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/styles.css`
- `frontend/src/components/Shell.tsx`
- `frontend/src/pages/Dashboard.tsx`
- `frontend/src/pages/Cases.tsx`
- `frontend/src/pages/Runs.tsx`
- `frontend/src/pages/RunDetail.tsx`
- `frontend/src/pages/Compare.tsx`
- `frontend/src/pages/Settings.tsx`

Create Docker/dev files:

- `Dockerfile.api`
- `Dockerfile.frontend`
- `docker-compose.yml`
- `.dockerignore`

Modify:

- `pyproject.toml` for backend dependencies.
- `Makefile` for app commands.
- `.gitignore` for local app DB and frontend build outputs.
- `docs/roadmap-local-webapp.md` to check off completed design/planning items after implementation.

---

### Task 1: Add Backend Dependencies and Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/harness_lab_app/__init__.py`
- Create: `src/harness_lab_app/config.py`
- Create: `src/harness_lab_app/db.py`
- Test: `tests/harness_lab_app/test_api.py`

- [ ] **Step 1: Add dependency declarations**

Modify `pyproject.toml` dependencies to include:

```toml
dependencies = [
    "pandas",
    "nbstripout>=0.7.0",
    "ipykernel",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlmodel>=0.0.22",
    "pydantic-settings>=2.6.0",
    "python-multipart>=0.0.12",
]
```

- [ ] **Step 2: Create package metadata**

Create `src/harness_lab_app/__init__.py`:

```python
"""Local web app for Agentic Harness Lab."""

__all__ = ["__version__"]
__version__ = "0.1.0"
```

- [ ] **Step 3: Create settings**

Create `src/harness_lab_app/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_LAB_", env_file=".env")

    repo_root: Path = Field(default_factory=lambda: Path.cwd())
    database_url: str = "sqlite:///data/app/harness_lab.db"
    local_username: str = "admin"
    local_password: str = "admin"
    runner_mode: str = "dry_run"

    @property
    def data_runs_dir(self) -> Path:
        return self.repo_root / "data" / "runs"

    @property
    def benchmarks_dir(self) -> Path:
        return self.repo_root / "benchmarks"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Create DB helpers**

Create `src/harness_lab_app/db.py`:

```python
from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.config import get_settings


def _sqlite_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url.removeprefix(prefix))


def get_engine():
    settings = get_settings()
    sqlite_path = _sqlite_path(settings.database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


engine = get_engine()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Write first API smoke test**

Create `tests/harness_lab_app/test_api.py` with a concrete import test:

```python
def test_api_module_imports():
    from harness_lab_app import __version__

    assert __version__ == "0.1.0"
```

- [ ] **Step 6: Run test**

Run:

```bash
uv run pytest tests/harness_lab_app/test_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/harness_lab_app tests/harness_lab_app
git commit -m "feat: add harness lab app backend skeleton"
```

---

### Task 2: Add Domain Models and Schemas

**Files:**
- Create: `src/harness_lab_app/models.py`
- Create: `src/harness_lab_app/schemas.py`
- Test: `tests/harness_lab_app/test_cases.py`

- [ ] **Step 1: Write model tests**

Create `tests/harness_lab_app/test_cases.py`:

```python
from harness_lab_app.models import Case, CaseStatus, CaseSyncState, CaseType


def test_case_defaults_are_operational():
    case = Case(case_id="sample-case", name="Sample", type=CaseType.single_prompt)

    assert case.status == CaseStatus.active
    assert case.sync_state == CaseSyncState.not_synced
    assert case.prompt_variants == {}
    assert case.metadata_json == {}
```

- [ ] **Step 2: Add models**

Create `src/harness_lab_app/models.py`:

```python
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class CaseType(StrEnum):
    single_prompt = "single_prompt"
    multi_step = "multi_step"
    build_from_scratch = "build_from_scratch"
    bugfix = "bugfix"
    repo_task = "repo_task"
    swe_bench_instance = "swe_bench_instance"


class CaseStatus(StrEnum):
    active = "active"
    inactive = "inactive"
    deleted = "deleted"


class CaseSyncState(StrEnum):
    not_synced = "not_synced"
    synced = "synced"
    modified_since_sync = "modified_since_sync"
    imported_from_yaml = "imported_from_yaml"


class RunState(StrEnum):
    draft = "draft"
    pending = "pending"
    running = "running"
    awaiting_manual_score = "awaiting_manual_score"
    scoring = "scoring"
    auditing = "auditing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Case(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    type: CaseType
    status: CaseStatus = CaseStatus.active
    sync_state: CaseSyncState = CaseSyncState.not_synced
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    subject_repo: str | None = None
    base_commit: str | None = None
    prompt_variants: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    step_prompts: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    success_criteria: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    scoring_rubric: str = ""
    constraints: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    validation_commands: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    timeout_seconds: int | None = None
    max_steps: int = 1
    allowed_files: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metadata_json: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunConfig(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(index=True)
    prompt_variant: str
    harness: str = "opencode"
    planner_model: str | None = None
    executor_model: str | None = None
    score_mode: str = "manual"
    max_steps: int = 1
    resume: bool = False
    dry_run: bool = True
    runner_adapter: str = "bench_run"
    tracking: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    run_config_id: int
    state: RunState = Field(default=RunState.pending, index=True)
    command: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
    score: int | None = None
    interventions: int | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class RunEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    level: str = "info"
    message: str
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    name: str
    path: str
    media_type: str = "text/plain"
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=utc_now)
```

- [ ] **Step 3: Add schemas**

Create `src/harness_lab_app/schemas.py`:

```python
from pydantic import BaseModel, Field

from harness_lab_app.models import CaseStatus, CaseSyncState, CaseType, RunState


class CaseCreate(BaseModel):
    case_id: str
    name: str
    type: CaseType
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    prompt_variants: dict[str, str] = Field(default_factory=dict)
    step_prompts: list[str] = Field(default_factory=list)
    success_criteria: dict[str, str] = Field(default_factory=dict)
    scoring_rubric: str = ""
    constraints: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    max_steps: int = 1


class CaseRead(CaseCreate):
    status: CaseStatus
    sync_state: CaseSyncState


class RunConfigCreate(BaseModel):
    case_id: str
    prompt_variant: str
    harness: str = "opencode"
    planner_model: str | None = None
    executor_model: str | None = None
    score_mode: str = "manual"
    max_steps: int = 1
    dry_run: bool = True


class RunCreate(BaseModel):
    run_id: str
    config: RunConfigCreate


class RunRead(BaseModel):
    run_id: str
    state: RunState
    command: list[str]
    score: int | None = None
    interventions: int | None = None
    failure_reason: str | None = None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/harness_lab_app/test_cases.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness_lab_app/models.py src/harness_lab_app/schemas.py tests/harness_lab_app/test_cases.py
git commit -m "feat: model benchmark cases and runs"
```

---

### Task 3: Implement Case Service with Tetris Seed

**Files:**
- Create: `src/harness_lab_app/case_service.py`
- Modify: `tests/harness_lab_app/test_cases.py`

- [ ] **Step 1: Add service tests**

Append to `tests/harness_lab_app/test_cases.py`:

```python
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import CaseSyncState, CaseType
from harness_lab_app.schemas import CaseCreate


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_create_and_clone_case():
    with make_session() as session:
        service = CaseService(session)
        created = service.create_case(
            CaseCreate(case_id="prompt-1", name="Prompt 1", type=CaseType.single_prompt)
        )
        cloned = service.clone_case("prompt-1", "prompt-2", "Prompt 2")

        assert created.case_id == "prompt-1"
        assert cloned.case_id == "prompt-2"
        assert cloned.name == "Prompt 2"
        assert cloned.sync_state == CaseSyncState.not_synced


def test_seed_tetris_from_yaml():
    with make_session() as session:
        service = CaseService(session)
        case = service.seed_tetris_case()

        assert case.case_id == "tetris-v1"
        assert "sdlc" in case.prompt_variants
        assert case.sync_state == CaseSyncState.imported_from_yaml
```

- [ ] **Step 2: Implement service**

Create `src/harness_lab_app/case_service.py`:

```python
from pathlib import Path

import yaml
from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Case, CaseStatus, CaseSyncState, CaseType, utc_now
from harness_lab_app.schemas import CaseCreate


class CaseService:
    def __init__(self, session: Session):
        self.session = session

    def list_cases(self) -> list[Case]:
        return list(self.session.exec(select(Case).where(Case.status != CaseStatus.deleted)))

    def get_case(self, case_id: str) -> Case:
        case = self.session.exec(select(Case).where(Case.case_id == case_id)).first()
        if case is None:
            raise ValueError(f"Case not found: {case_id}")
        return case

    def create_case(self, data: CaseCreate) -> Case:
        case = Case(**data.model_dump())
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def clone_case(self, source_case_id: str, new_case_id: str, new_name: str) -> Case:
        source = self.get_case(source_case_id)
        clone = Case(
            case_id=new_case_id,
            name=new_name,
            description=source.description,
            type=source.type,
            tags=list(source.tags),
            subject_repo=source.subject_repo,
            base_commit=source.base_commit,
            prompt_variants=dict(source.prompt_variants),
            step_prompts=list(source.step_prompts),
            success_criteria=dict(source.success_criteria),
            scoring_rubric=source.scoring_rubric,
            constraints=list(source.constraints),
            validation_commands=list(source.validation_commands),
            timeout_seconds=source.timeout_seconds,
            max_steps=source.max_steps,
            allowed_files=list(source.allowed_files),
            metadata_json=dict(source.metadata_json),
            sync_state=CaseSyncState.not_synced,
        )
        self.session.add(clone)
        self.session.commit()
        self.session.refresh(clone)
        return clone

    def deactivate_case(self, case_id: str) -> Case:
        case = self.get_case(case_id)
        case.status = CaseStatus.inactive
        case.updated_at = utc_now()
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def seed_tetris_case(self) -> Case:
        existing = self.session.exec(select(Case).where(Case.case_id == "tetris-v1")).first()
        if existing is not None:
            return existing
        settings = get_settings()
        task_path = settings.repo_root / "benchmarks" / "tetris" / "task.yaml"
        return self.import_task_yaml(task_path)

    def import_task_yaml(self, task_path: Path) -> Case:
        raw = yaml.safe_load(task_path.read_text())
        case = Case(
            case_id=raw["task_id"],
            name=raw["task_id"],
            description=raw.get("description", "").strip(),
            type=CaseType.build_from_scratch,
            status=CaseStatus.active,
            sync_state=CaseSyncState.imported_from_yaml,
            tags=list(raw.get("tags", [])),
            subject_repo=raw.get("subject_repo"),
            base_commit=raw.get("base_commit"),
            prompt_variants=dict(raw.get("prompts", {})),
            success_criteria=dict(raw.get("success_criteria", {})),
            scoring_rubric=str(raw.get("scoring_rubric", "")),
            constraints=list(raw.get("constraints", [])),
            validation_commands=[raw["validation_note"]] if raw.get("validation_note") else [],
            max_steps=int(raw.get("target_level", 1)),
            metadata_json={"source_path": str(task_path)},
        )
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/harness_lab_app/test_cases.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/harness_lab_app/case_service.py tests/harness_lab_app/test_cases.py
git commit -m "feat: add benchmark case service"
```

---

### Task 4: Implement Runner Adapter and Run Service

**Files:**
- Create: `src/harness_lab_app/runner_adapter.py`
- Create: `src/harness_lab_app/run_service.py`
- Create: `tests/harness_lab_app/test_runs.py`

- [ ] **Step 1: Add run tests**

Create `tests/harness_lab_app/test_runs.py`:

```python
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.models import RunState
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import RunConfigCreate, RunCreate


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_command_preview_uses_bench_run():
    config = RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="sdlc",
        executor_model="gateway/nim-nemotron-super-120b",
        max_steps=10,
        dry_run=True,
    )

    with make_session() as session:
        service = RunService(session)
        command = service.preview_command("tetris-v1-J-002", config)

    assert command[:4] == ["uv", "run", "python", "scripts/bench_run.py"]
    assert "--dry-run" in command
    assert "tetris-v1-J-002" in command


def test_create_run_starts_pending():
    with make_session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="tetris-v1-J-002",
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc"),
            )
        )

        assert run.state == RunState.pending
        assert "--case" in run.command
```

- [ ] **Step 2: Implement runner adapter**

Create `src/harness_lab_app/runner_adapter.py`:

```python
from harness_lab_app.schemas import RunConfigCreate


class BenchRunAdapter:
    def build_command(self, run_id: str, config: RunConfigCreate) -> list[str]:
        command = [
            "uv",
            "run",
            "python",
            "scripts/bench_run.py",
            "--case",
            config.case_id,
            "--run",
            run_id,
            "--prompt",
            config.prompt_variant,
            "--score-mode",
            config.score_mode,
            "--max-steps",
            str(config.max_steps),
        ]
        if config.planner_model:
            command.extend(["--planner-model", config.planner_model])
        if config.executor_model:
            command.extend(["--executor-model", config.executor_model])
        if config.dry_run:
            command.append("--dry-run")
        return command
```

- [ ] **Step 3: Implement run service**

Create `src/harness_lab_app/run_service.py`:

```python
from sqlmodel import Session, select

from harness_lab_app.models import Run, RunConfig, RunEvent, RunState, utc_now
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate, RunCreate


class RunService:
    def __init__(self, session: Session):
        self.session = session
        self.adapter = BenchRunAdapter()

    def preview_command(self, run_id: str, config: RunConfigCreate) -> list[str]:
        return self.adapter.build_command(run_id, config)

    def list_runs(self) -> list[Run]:
        return list(self.session.exec(select(Run).order_by(Run.created_at.desc())))

    def get_run(self, run_id: str) -> Run:
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    def create_run(self, data: RunCreate) -> Run:
        config = RunConfig(**data.config.model_dump())
        self.session.add(config)
        self.session.commit()
        self.session.refresh(config)
        command = self.preview_command(data.run_id, data.config)
        run = Run(run_id=data.run_id, run_config_id=config.id or 0, command=command)
        self.session.add(run)
        self.session.add(RunEvent(run_id=data.run_id, message="Run queued"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_running(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        run.state = RunState.running
        run.started_at = utc_now()
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run started"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_finished(self, run_id: str, exit_code: int, failure_reason: str | None = None) -> Run:
        run = self.get_run(run_id)
        run.exit_code = exit_code
        run.failure_reason = failure_reason
        run.ended_at = utc_now()
        run.state = RunState.awaiting_manual_score if exit_code == 0 else RunState.failed
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, level="error" if exit_code else "info", message=f"Run exited with {exit_code}"))
        self.session.commit()
        self.session.refresh(run)
        return run
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/harness_lab_app/test_runs.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness_lab_app/runner_adapter.py src/harness_lab_app/run_service.py tests/harness_lab_app/test_runs.py
git commit -m "feat: add run command preview service"
```

---

### Task 5: Add Artifact Indexing

**Files:**
- Create: `src/harness_lab_app/artifact_service.py`
- Create: `tests/harness_lab_app/test_artifacts.py`

- [ ] **Step 1: Add artifact test**

Create `tests/harness_lab_app/test_artifacts.py`:

```python
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.artifact_service import ArtifactService


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_index_run_artifacts(tmp_path: Path):
    run_dir = tmp_path / "data" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text('{"run_id":"run-1"}')
    (run_dir / "diff.patch").write_text("diff --git a/main.py b/main.py")

    with make_session() as session:
        service = ArtifactService(session, data_runs_dir=tmp_path / "data" / "runs")
        artifacts = service.index_run("run-1")

    assert [artifact.name for artifact in artifacts] == ["diff.patch", "metadata.json"]
```

- [ ] **Step 2: Implement artifact service**

Create `src/harness_lab_app/artifact_service.py`:

```python
from pathlib import Path

from sqlmodel import Session, delete, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Artifact


class ArtifactService:
    def __init__(self, session: Session, data_runs_dir: Path | None = None):
        self.session = session
        self.data_runs_dir = data_runs_dir or get_settings().data_runs_dir

    def index_run(self, run_id: str) -> list[Artifact]:
        run_dir = self.data_runs_dir / run_id
        self.session.exec(delete(Artifact).where(Artifact.run_id == run_id))
        if not run_dir.exists():
            self.session.commit()
            return []
        artifacts: list[Artifact] = []
        for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
            artifact = Artifact(
                run_id=run_id,
                name=str(path.relative_to(run_dir)),
                path=str(path),
                media_type=self._media_type(path),
                size_bytes=path.stat().st_size,
            )
            self.session.add(artifact)
            artifacts.append(artifact)
        self.session.commit()
        return artifacts

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        return list(self.session.exec(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.name)))

    def read_text(self, run_id: str, name: str) -> str:
        artifact = self.session.exec(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.name == name)
        ).first()
        if artifact is None:
            raise ValueError(f"Artifact not found: {run_id}/{name}")
        return Path(artifact.path).read_text(errors="replace")

    def _media_type(self, path: Path) -> str:
        if path.suffix == ".json":
            return "application/json"
        if path.suffix in {".md", ".patch", ".log", ".txt"}:
            return "text/plain"
        return "application/octet-stream"
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/harness_lab_app/test_artifacts.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/harness_lab_app/artifact_service.py tests/harness_lab_app/test_artifacts.py
git commit -m "feat: index benchmark run artifacts"
```

---

### Task 6: Add FastAPI Routes

**Files:**
- Create: `src/harness_lab_app/api.py`
- Modify: `tests/harness_lab_app/test_api.py`

- [ ] **Step 1: Add API tests**

Replace `tests/harness_lab_app/test_api.py` with:

```python
from fastapi.testclient import TestClient

from harness_lab_app.api import create_app


def test_health_endpoint():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_endpoint_accepts_default_credentials():
    client = TestClient(create_app())

    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
```

- [ ] **Step 2: Implement API**

Create `src/harness_lab_app/api.py`:

```python
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from harness_lab_app.artifact_service import ArtifactService
from harness_lab_app.case_service import CaseService
from harness_lab_app.config import get_settings
from harness_lab_app.db import get_session, init_db
from harness_lab_app.run_service import RunService
from harness_lab_app.schemas import CaseCreate, RunCreate, RunConfigCreate


class LoginRequest(BaseModel):
    username: str
    password: str


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic Harness Lab")

    @app.on_event("startup")
    def startup() -> None:
        init_db()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(data: LoginRequest) -> dict[str, bool]:
        settings = get_settings()
        if data.username != settings.local_username or data.password != settings.local_password:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    def logout() -> dict[str, bool]:
        return {"authenticated": False}

    @app.get("/api/cases")
    def list_cases(session: Session = Depends(get_session)):
        return CaseService(session).list_cases()

    @app.post("/api/cases")
    def create_case(data: CaseCreate, session: Session = Depends(get_session)):
        return CaseService(session).create_case(data)

    @app.post("/api/cases/seed/tetris")
    def seed_tetris(session: Session = Depends(get_session)):
        return CaseService(session).seed_tetris_case()

    @app.post("/api/cases/{case_id}/clone")
    def clone_case(case_id: str, new_case_id: str, new_name: str, session: Session = Depends(get_session)):
        return CaseService(session).clone_case(case_id, new_case_id, new_name)

    @app.get("/api/runs")
    def list_runs(session: Session = Depends(get_session)):
        return RunService(session).list_runs()

    @app.post("/api/runs/preview")
    def preview_run(run_id: str, config: RunConfigCreate, session: Session = Depends(get_session)):
        return {"command": RunService(session).preview_command(run_id, config)}

    @app.post("/api/runs")
    def create_run(data: RunCreate, session: Session = Depends(get_session)):
        return RunService(session).create_run(data)

    @app.post("/api/runs/{run_id}/artifacts/index")
    def index_artifacts(run_id: str, session: Session = Depends(get_session)):
        return ArtifactService(session).index_run(run_id)

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str, session: Session = Depends(get_session)):
        return ArtifactService(session).list_artifacts(run_id)

    return app


app = create_app()
```

- [ ] **Step 3: Run API tests**

```bash
uv run pytest tests/harness_lab_app/test_api.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/harness_lab_app/api.py tests/harness_lab_app/test_api.py
git commit -m "feat: expose harness lab API"
```

---

### Task 7: Add Worker MVP

**Files:**
- Create: `src/harness_lab_app/worker.py`
- Modify: `tests/harness_lab_app/test_runs.py`

- [ ] **Step 1: Add worker test**

Append to `tests/harness_lab_app/test_runs.py`:

```python
from harness_lab_app.models import RunEvent
from harness_lab_app.worker import run_pending_once


def test_worker_finishes_dry_run():
    with make_session() as session:
        service = RunService(session)
        service.create_run(
            RunCreate(
                run_id="dry-run-1",
                config=RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc", dry_run=True),
            )
        )

        processed = run_pending_once(session, execute_process=False)
        run = service.get_run("dry-run-1")

        assert processed == 1
        assert run.state == RunState.awaiting_manual_score
        assert session.query(RunEvent).filter(RunEvent.run_id == "dry-run-1").count() >= 2
```

- [ ] **Step 2: Implement worker**

Create `src/harness_lab_app/worker.py`:

```python
import subprocess

from sqlmodel import Session, select

from harness_lab_app.models import Run, RunEvent, RunState
from harness_lab_app.run_service import RunService


def run_pending_once(session: Session, execute_process: bool = True) -> int:
    run = session.exec(select(Run).where(Run.state == RunState.pending).order_by(Run.created_at)).first()
    if run is None:
        return 0
    service = RunService(session)
    service.mark_running(run.run_id)
    if execute_process:
        result = subprocess.run(run.command, check=False, capture_output=True, text=True)
        if result.stdout:
            session.add(RunEvent(run_id=run.run_id, message=result.stdout[-4000:]))
        if result.stderr:
            session.add(RunEvent(run_id=run.run_id, level="error", message=result.stderr[-4000:]))
        session.commit()
        service.mark_finished(run.run_id, result.returncode, None if result.returncode == 0 else result.stderr[-1000:])
    else:
        session.add(RunEvent(run_id=run.run_id, message="Dry-run worker simulation completed"))
        session.commit()
        service.mark_finished(run.run_id, 0)
    return 1
```

- [ ] **Step 3: Run worker tests**

```bash
uv run pytest tests/harness_lab_app/test_runs.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/harness_lab_app/worker.py tests/harness_lab_app/test_runs.py
git commit -m "feat: add local benchmark worker"
```

---

### Task 8: Add Docker Compose and Make Targets

**Files:**
- Create: `Dockerfile.api`
- Create: `Dockerfile.frontend`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Modify: `Makefile`
- Modify: `.gitignore`

- [ ] **Step 1: Add backend Dockerfile**

Create `Dockerfile.api`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev || uv sync
COPY . .
ENV PYTHONPATH=/app/src
CMD ["uv", "run", "uvicorn", "harness_lab_app.api:app", "--host", "0.0.0.0", "--port", "8008"]
```

- [ ] **Step 2: Add frontend Dockerfile**

Create `Dockerfile.frontend`:

```dockerfile
FROM node:22-slim

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [ ] **Step 3: Add compose**

Create `docker-compose.yml`:

```yaml
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    environment:
      HARNESS_LAB_REPO_ROOT: /app
      HARNESS_LAB_DATABASE_URL: sqlite:////app/data/app/harness_lab.db
    volumes:
      - .:/app
    ports:
      - "8008:8008"

  worker:
    build:
      context: .
      dockerfile: Dockerfile.api
    command: ["uv", "run", "python", "-c", "from harness_lab_app.db import init_db, engine; from sqlmodel import Session; from harness_lab_app.worker import run_pending_once; init_db(); session=Session(engine); run_pending_once(session, execute_process=True)"]
    environment:
      HARNESS_LAB_REPO_ROOT: /app
      HARNESS_LAB_DATABASE_URL: sqlite:////app/data/app/harness_lab.db
    volumes:
      - .:/app
    profiles:
      - worker

  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    volumes:
      - ./frontend:/app/frontend
      - /app/frontend/node_modules
    ports:
      - "5173:5173"
    depends_on:
      - api
```

- [ ] **Step 4: Add dockerignore and gitignore entries**

Create `.dockerignore`:

```gitignore
.git
.venv
__pycache__
.pytest_cache
.ruff_cache
.mypy_cache
htmlcov
frontend/node_modules
frontend/dist
.superpowers
```

Append to `.gitignore`:

```gitignore
data/app/
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 5: Add Make targets**

Append to `Makefile`:

```make
app-up:
	docker compose up --build api frontend

app-worker-once:
	docker compose --profile worker run --rm worker

app-test:
	uv run pytest tests/harness_lab_app -v
```

- [ ] **Step 6: Validate compose config**

Run:

```bash
docker compose config
```

Expected: exits 0 and renders `api`, `worker`, and `frontend`.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile.api Dockerfile.frontend docker-compose.yml .dockerignore .gitignore Makefile
git commit -m "feat: add dockerized webapp runtime"
```

---

### Task 9: Add Frontend Skeleton and API Client

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/styles.css`

- [ ] **Step 1: Add frontend package**

Create `frontend/package.json`:

```json
{
  "name": "agentic-harness-lab-web",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "test": "tsc --noEmit"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.0",
    "typescript": "^5.7.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "lucide-react": "^0.468.0"
  },
  "devDependencies": {}
}
```

- [ ] **Step 2: Add Vite config**

Create `frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://api:8008"
    }
  }
});
```

- [ ] **Step 3: Add TypeScript config**

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Add HTML entry**

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agentic Harness Lab</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Add frontend types and client**

Create `frontend/src/types.ts`:

```ts
export type RunState = "draft" | "pending" | "running" | "awaiting_manual_score" | "scoring" | "auditing" | "completed" | "failed" | "cancelled";

export type CaseRecord = {
  case_id: string;
  name: string;
  type: string;
  status: string;
  sync_state: string;
  description: string;
};

export type RunRecord = {
  run_id: string;
  state: RunState;
  command: string[];
  score?: number | null;
  interventions?: number | null;
  failure_reason?: string | null;
};
```

Create `frontend/src/api.ts`:

```ts
import type { CaseRecord, RunRecord } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  login: (username: string, password: string) =>
    request<{ authenticated: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  listCases: () => request<CaseRecord[]>("/api/cases"),
  seedTetris: () => request<CaseRecord>("/api/cases/seed/tetris", { method: "POST" }),
  listRuns: () => request<RunRecord[]>("/api/runs")
};
```

- [ ] **Step 6: Add app shell**

Create `frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

Create `frontend/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "./api";
import type { CaseRecord, RunRecord } from "./types";

export default function App() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listCases(), api.listRuns()])
      .then(([nextCases, nextRuns]) => {
        setCases(nextCases);
        setRuns(nextRuns);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Agentic Harness Lab</div>
        <nav>
          <button>Dashboard</button>
          <button className="active">Runs</button>
          <button>Cases</button>
          <button>Compare</button>
          <button>Settings</button>
        </nav>
      </aside>
      <main className="workbench">
        <header className="toolbar">
          <div>
            <h1>Runs</h1>
            <p>Operate local benchmark runs from the existing harness.</p>
          </div>
          <button onClick={() => api.seedTetris().then((created) => setCases((items) => [created, ...items]))}>
            Seed Tetris
          </button>
        </header>
        {error && <div className="error">{error}</div>}
        <section className="panel">
          <h2>Cases</h2>
          {cases.map((item) => (
            <div className="row" key={item.case_id}>
              <strong>{item.case_id}</strong>
              <span>{item.type}</span>
              <span>{item.sync_state}</span>
            </div>
          ))}
        </section>
        <section className="panel">
          <h2>Recent runs</h2>
          {runs.length === 0 ? <p className="empty">No runs yet.</p> : runs.map((run) => <div className="row" key={run.run_id}>{run.run_id}</div>)}
        </section>
      </main>
      <aside className="inspector">
        <h2>Inspector</h2>
        <p>Select a run to view status, artifacts, score, and audit.</p>
      </aside>
    </div>
  );
}
```

Create `frontend/src/styles.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #1f2937; }
button { border: 1px solid #cbd5e1; background: #fff; color: #1f2937; border-radius: 6px; padding: 8px 10px; cursor: pointer; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 220px minmax(0, 1fr) 320px; }
.sidebar { border-right: 1px solid #e5e7eb; background: #fff; padding: 18px; }
.brand { font-weight: 700; margin-bottom: 24px; }
nav { display: grid; gap: 8px; }
nav button { text-align: left; }
nav button.active { border-color: #2563eb; color: #1d4ed8; }
.workbench { padding: 20px; overflow: auto; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
h1, h2, p { margin-top: 0; }
.panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
.row { min-height: 40px; display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 12px; border-top: 1px solid #f1f5f9; padding: 8px 0; }
.row:first-of-type { border-top: 0; }
.inspector { border-left: 1px solid #e5e7eb; background: #fff; padding: 18px; }
.error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; border-radius: 6px; padding: 10px; margin-bottom: 12px; }
.empty { color: #64748b; }
```

- [ ] **Step 7: Build frontend**

Run:

```bash
cd frontend && npm install && npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "feat: add operational webapp shell"
```

---

### Task 10: Add Case and Run API Integration UI

**Files:**
- Create: `frontend/src/components/Shell.tsx`
- Create: `frontend/src/pages/Cases.tsx`
- Create: `frontend/src/pages/Runs.tsx`
- Create: `frontend/src/pages/RunDetail.tsx`
- Create: `frontend/src/pages/Compare.tsx`
- Create: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Split shell into components**

Move the sidebar and inspector layout from `App.tsx` into `frontend/src/components/Shell.tsx`:

```tsx
import type { ReactNode } from "react";

type ShellProps = {
  active: string;
  children: ReactNode;
  inspector: ReactNode;
  onNavigate: (page: string) => void;
};

const navItems = ["Dashboard", "Runs", "Cases", "Compare", "Settings"];

export function Shell({ active, children, inspector, onNavigate }: ShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Agentic Harness Lab</div>
        <nav>
          {navItems.map((item) => (
            <button className={active === item ? "active" : ""} key={item} onClick={() => onNavigate(item)}>
              {item}
            </button>
          ))}
        </nav>
      </aside>
      <main className="workbench">{children}</main>
      <aside className="inspector">{inspector}</aside>
    </div>
  );
}
```

- [ ] **Step 2: Add pages**

Create simple page components:

```tsx
// frontend/src/pages/Runs.tsx
import type { RunRecord } from "../types";

export function Runs({ runs }: { runs: RunRecord[] }) {
  return <section className="panel"><h2>Recent runs</h2>{runs.map((run) => <div className="row" key={run.run_id}><strong>{run.run_id}</strong><span>{run.state}</span></div>)}</section>;
}
```

```tsx
// frontend/src/pages/Cases.tsx
import type { CaseRecord } from "../types";

export function Cases({ cases, onSeedTetris }: { cases: CaseRecord[]; onSeedTetris: () => void }) {
  return <section className="panel"><header className="toolbar"><h2>Cases</h2><button onClick={onSeedTetris}>Seed Tetris</button></header>{cases.map((item) => <div className="row" key={item.case_id}><strong>{item.case_id}</strong><span>{item.type}</span><span>{item.sync_state}</span></div>)}</section>;
}
```

```tsx
// frontend/src/pages/RunDetail.tsx
export function RunDetail() {
  return <><h2>Inspector</h2><p>Select a run to view status, artifacts, score, and audit.</p></>;
}
```

```tsx
// frontend/src/pages/Compare.tsx
export function Compare() {
  return <section className="panel"><h2>Compare</h2><p className="empty">Select two runs after creating benchmark history.</p></section>;
}
```

```tsx
// frontend/src/pages/Settings.tsx
export function Settings() {
  return <section className="panel"><h2>Settings</h2><p>Docker, runner, paths, and tracking configuration.</p></section>;
}
```

- [ ] **Step 3: Update App routing state**

Modify `frontend/src/App.tsx` to use the new components and simple state-driven navigation.

- [ ] **Step 4: Build frontend**

```bash
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: add webapp navigation pages"
```

---

### Task 11: End-to-End MVP Verification

**Files:**
- Modify: `docs/roadmap-local-webapp.md`

- [ ] **Step 1: Run backend tests**

```bash
uv run pytest tests/harness_lab_app -v
```

Expected: all `harness_lab_app` tests pass.

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Validate Docker Compose config**

```bash
docker compose config
```

Expected: config renders successfully.

- [ ] **Step 4: Start stack**

```bash
docker compose up --build api frontend
```

Expected:

- API available at `http://localhost:8008/api/health`.
- Frontend available at `http://localhost:5173`.

- [ ] **Step 5: Smoke test Tetris seed**

In the UI, click `Seed Tetris`. Expected: `tetris-v1` appears in the Cases list with `imported_from_yaml`.

- [ ] **Step 6: Update roadmap**

In `docs/roadmap-local-webapp.md`, check off the MVP items that are actually complete:

- Docker Compose operating mode.
- Backend skeleton.
- Case seed/import.
- Operational Shell UI skeleton.
- Dry-run command preview if implemented.
- Artifact indexing if implemented.

- [ ] **Step 7: Commit verification docs**

```bash
git add docs/roadmap-local-webapp.md
git commit -m "docs: mark local webapp mvp progress"
```

---

## Self-Review Checklist

- [ ] Spec coverage: Docker Compose, Operational Shell, DB-owned UI cases, manual YAML sync state, Tetris seed, run preview, worker, artifacts, and UI shell are covered.
- [ ] Completion-marker scan: this plan contains no unfinished markers or undefined future task slots.
- [ ] Type consistency: `CaseType`, `RunState`, `CaseSyncState`, `CaseCreate`, `RunConfigCreate`, and `RunCreate` names match across tasks.
- [ ] MVP limit: SWE-bench execution, Celery/Redis, remote workers, object storage, and OAuth remain outside this first implementation plan.
