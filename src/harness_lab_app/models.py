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
    prompt_variants: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    step_prompts: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    success_criteria: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
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
    dry_run: bool = False
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
    cancel_requested: bool = False
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


class Validation(SQLModel, table=True):
    """Multidimensional result of validating a completed run.

    Dimensions are kept separate on purpose (never collapsed prematurely):
    - objective: automated checks (smoke / tests) — did the produced code run/pass?
    - cost: tokens and money spent (from usage.json).
    - effort: steps and human interventions.
    - subjective: manual/LLM functional score (0-10) and optional rubric.
    - composite: an explicit, documented roll-up of the above.
    """

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    objective: dict = Field(default_factory=dict, sa_column=Column(JSON))
    cost: dict = Field(default_factory=dict, sa_column=Column(JSON))
    effort: dict = Field(default_factory=dict, sa_column=Column(JSON))
    subjective: dict = Field(default_factory=dict, sa_column=Column(JSON))
    composite: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Attribution(SQLModel, table=True):
    """Machine-readable record of which harness surface a run actually used.

    Extracted from the run's opencode step logs: tool usage, files, bash
    commands, agent modes, and — the key signal — coverage of the governance
    docs / memory the harness provides ("harness applied?").
    """

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    tools: dict = Field(default_factory=dict, sa_column=Column(JSON))
    files: dict = Field(default_factory=dict, sa_column=Column(JSON))
    bash_commands: dict = Field(default_factory=dict, sa_column=Column(JSON))
    agent_modes: dict = Field(default_factory=dict, sa_column=Column(JSON))
    harness_usage: dict = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str = ""
    steps_parsed: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
