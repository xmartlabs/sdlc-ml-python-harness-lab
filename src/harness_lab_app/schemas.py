import re

from pydantic import BaseModel, Field, field_validator

from harness_lab_app.models import CaseStatus, CaseSyncState, CaseType, RunState

# SWE-bench instance ids look like `pallets__flask-4992` (owner__repo-issue).
_SWEBENCH_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+$")


class SwebenchImportRequest(BaseModel):
    instance_id: str

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _SWEBENCH_INSTANCE_RE.match(normalized):
            raise ValueError(
                "instance_id must look like owner__repo-N (e.g. pallets__flask-4992)"
            )
        return normalized


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
    resume: bool = False
    dry_run: bool = False
    # Per-run harness ablation: governed skills to hide from the agent for this
    # run (empty = full harness, the historical default arm).
    skills_disabled: list[str] = Field(default_factory=list)
    # Staged/experimental skills (data/skills_staging/<name>/) to add for this
    # run only. Unchecked by default in the UI.
    skills_extra: list[str] = Field(default_factory=list)

    @field_validator("score_mode")
    @classmethod
    def validate_score_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "interactive":
            raise ValueError(
                "score_mode=interactive is not supported from the web API; use score_mode=manual and submit via POST /api/runs/{run_id}/score"
            )
        # bench_run.py argparse accepts manual|interactive|none; the web flow
        # standardizes on manual + POST /api/runs/{run_id}/score afterwards.
        if normalized != "manual":
            raise ValueError("score_mode must be: manual")
        return normalized


class StagedSkillCreate(BaseModel):
    name: str
    description: str = ""
    content: str


class StagedSkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    status: str | None = None


class RunCreate(BaseModel):
    # run_id is optional: when omitted (and no label), the server assigns the
    # next free <case>-K-NNN id (ADR-0006). An explicit run_id or label is used
    # verbatim and still 409s on duplicate.
    run_id: str | None = None
    label: str | None = None
    config: RunConfigCreate


class RunRead(BaseModel):
    run_id: str
    state: RunState
    command: list[str]
    score: int | None = None
    interventions: int | None = None
    notes: str = ""
    failure_reason: str | None = None


class ValidationRead(BaseModel):
    run_id: str
    objective: dict = Field(default_factory=dict)
    cost: dict = Field(default_factory=dict)
    effort: dict = Field(default_factory=dict)
    subjective: dict = Field(default_factory=dict)
    composite: float | None = None
