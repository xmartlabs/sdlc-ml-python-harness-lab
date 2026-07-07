from pydantic import BaseModel, Field, field_validator

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
    resume: bool = False
    dry_run: bool = False

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


class RunCreate(BaseModel):
    run_id: str
    config: RunConfigCreate


class RunRead(BaseModel):
    run_id: str
    state: RunState
    command: list[str]
    score: int | None = None
    interventions: int | None = None
    notes: str = ""
    failure_reason: str | None = None
