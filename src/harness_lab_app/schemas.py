import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from harness_lab_app.models import (
    CaseProvenance,
    CaseStatus,
    CaseSyncState,
    CaseType,
    ConditionRole,
    ExperimentMode,
    FindingSeverity,
    FindingStatus,
    HarnessSource,
    RunState,
    TargetStatus,
)

# SWE-bench instance ids look like `pallets__flask-4992` (owner__repo-issue).
# SWE-bench Pro ids look like `instance_owner__repo-<sha>[-v<sha|nan>]`.
_SWEBENCH_INSTANCE_RE = re.compile(
    r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+$"
    r"|^instance_[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$"
)


class SwebenchImportRequest(BaseModel):
    instance_id: str

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _SWEBENCH_INSTANCE_RE.match(normalized):
            raise ValueError(
                "instance_id must look like owner__repo-N (e.g. pallets__flask-4992) "
                "or instance_owner__repo-<sha> (SWE-bench Pro)"
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
    provenance: CaseProvenance = CaseProvenance.synthetic
    files_expected: list[str] = Field(default_factory=list)
    files_forbidden: list[str] = Field(default_factory=list)
    metadata_json: dict[str, str] = Field(default_factory=dict)
    max_steps: int = 1
    # The repository this case was written for, when it was written for one.
    # What turns a pile of cases into a repository's own library (ADR-0042).
    origin_target_id: str = ""


class CaseRead(CaseCreate):
    status: CaseStatus
    sync_state: CaseSyncState


class TargetRepositoryCreate(BaseModel):
    target_id: str
    name: str
    repository_url: str
    description: str = ""
    default_ref: str = "main"
    setup_commands: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    language: str = ""
    team: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata_json: dict[str, str] = Field(default_factory=dict)


class TargetRepositoryUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are left alone."""

    name: str | None = None
    repository_url: str | None = None
    description: str | None = None
    default_ref: str | None = None
    status: TargetStatus | None = None
    setup_commands: list[str] | None = None
    validation_commands: list[str] | None = None
    language: str | None = None
    team: str | None = None
    tags: list[str] | None = None
    metadata_json: dict[str, str] | None = None


class TargetRepositoryRead(TargetRepositoryCreate):
    status: TargetStatus


class TargetResolveRequest(BaseModel):
    """Resolve a ref without creating a run — the form's 'check' button."""

    ref: str = ""


class TargetInspectRequest(BaseModel):
    """Inspect a repository before it is saved — the form's 'inspect' button.

    Returns the same readiness report a saved target would, so a user learns the
    runtime is unsupported (or its commands mismatch it) before creating it.
    """

    repository_url: str
    default_ref: str = ""

    setup_commands: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)


class ExperimentConditionCreate(BaseModel):
    """One declared arm. ``overrides`` fixes the axis values this arm holds."""

    label: str
    role: ConditionRole = ConditionRole.treatment
    description: str = ""
    overrides: dict = Field(default_factory=dict)


class ExperimentCreate(BaseModel):
    experiment_id: str
    name: str = ""
    question: str
    mode: ExperimentMode
    # What is under test, for a mode where that is not the harness (ADR-0040).
    # Required for `subject_validation`, ignored otherwise.
    subject: str = ""
    case_ids: list[str] = Field(default_factory=list)
    prompt_variants: list[str] = Field(default_factory=list)
    conditions: list[ExperimentConditionCreate] = Field(default_factory=list)
    repetitions: int = 1
    # Per-case override of ``repetitions``; empty means uniform.
    case_repetitions: dict[str, int] = Field(default_factory=dict)
    suite_id: str = ""
    notes: str = ""


class SuiteArmCreate(BaseModel):
    """One side of a regression-suite comparison."""

    label: str
    description: str = ""
    overrides: dict = Field(default_factory=dict)


class SuiteExperimentCreate(BaseModel):
    """Generate an experiment from a regression suite (§13).

    The suite supplies the cases and repetitions; the caller supplies the
    harness change being measured.
    """

    experiment_id: str
    suite_id: str = "harness-regression-v1"
    control: SuiteArmCreate
    treatment: SuiteArmCreate
    prompt_variant: str = "baseline"
    question: str = ""
    name: str = ""
    notes: str = ""


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
    # Follow-up instruction that replaces the case prompt for a continuation
    # run (ADR-0026). Set by RunService.continue_run, not by the run form.
    continuation_prompt: str = ""
    # Per-run harness ablation: governed skills to hide from the agent for this
    # run (empty = full harness, the historical default arm).
    skills_disabled: list[str] = Field(default_factory=list)
    # Staged/experimental skills (data/skills_staging/<name>/) to add for this
    # run only. Unchecked by default in the UI.
    skills_extra: list[str] = Field(default_factory=list)
    # Skills the agent is told, at the start of every step, to invoke and
    # follow (ADR-0041). Available is not invoked: a skill on the menu is
    # opened only when the instruction or its own triggers call for it. Every
    # name here must be available in this run — staged via `skills_extra`, or
    # governed and not in `skills_disabled`.
    skills_invoke: list[str] = Field(default_factory=list)

    # External target repository (ADR-0029). `target_repo_id` is what the run
    # form sends; `target_ref` defaults to the repository's own default_ref.
    # `target_commit` is NOT accepted from the client — RunService resolves it,
    # so a caller cannot record a commit the run will not actually use.
    target_repo_id: str = ""
    target_ref: str = ""
    # Which harness the agent sees (ADR-0031). `native`/`injected` both run the
    # agent inside the target repository and therefore require one.
    harness_source: HarnessSource = HarnessSource.lab_root

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


class ExperimentLaunch(BaseModel):
    """Settings every arm of the launch shares; conditions override on top."""

    config: RunConfigCreate


class FindingCreate(BaseModel):
    finding_id: str
    title: str
    body: str = ""
    severity: FindingSeverity = FindingSeverity.medium
    # A finding written by a person starts open. `suggested` is reserved for the
    # lab's own proposals, which are not findings of record until accepted.
    status: FindingStatus = FindingStatus.open
    # The suggestion key this came from, when it came from one (§14).
    origin: str = ""
    author: str = ""
    target_repo_id: str = ""
    evidence_run_ids: list[str] = Field(default_factory=list)
    affected_case_ids: list[str] = Field(default_factory=list)
    recommendation: str = ""
    intervention: str = ""


class SuggestionAccept(BaseModel):
    """Accept a suggested finding, with the edits and the name that make it one."""

    author: str
    finding_id: str = ""
    # Any field of the suggestion the operator rewrote before signing it.
    overrides: dict[str, Any] = Field(default_factory=dict)


class SuggestionReject(BaseModel):
    author: str
    reason: str = ""


class FindingUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    severity: FindingSeverity | None = None
    status: FindingStatus | None = None
    recommendation: str | None = None
    intervention: str | None = None
    experiment_id: str | None = None
    outcome: str | None = None
    evidence_run_ids: list[str] | None = None
    affected_case_ids: list[str] | None = None


class AssessmentCreate(BaseModel):
    assessment_id: str
    organization: str
    summary: str = ""
    assessed_by: str = ""
    # {dimension_key: {"level": 1-5, "rationale": str, "evidence": [ids]}}
    dimensions: dict = Field(default_factory=dict)


class StagedSkillCreate(BaseModel):
    name: str
    description: str = ""
    content: str


class StagedSkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    status: str | None = None


class StagedSkillImportRequest(BaseModel):
    """Import a skill tree from a URL, or from a pasted install command.

    ``confirm`` is what makes this two-step: without it the endpoint fetches,
    validates and returns a preview, and writes nothing at all (ADR-0017).

    ``skill`` picks one skill out of a source that holds several (a catalog repo
    like ``anthropics/skills``). Left empty, such a source answers with the list
    of choices instead of guessing.
    """

    url: str
    ref: str = ""
    name: str = ""
    skill: str = ""
    confirm: bool = False
    replace: bool = False


class StagedSkillFileWrite(BaseModel):
    content: str


class GatewayModelUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    options: dict | None = None


class GatewayTestRequest(BaseModel):
    """Test-before-save probe. Empty fields fall back to what .env holds."""

    base_url: str = ""
    token: str = ""


class EnvUpdateRequest(BaseModel):
    """Partial update of the web-editable ``.env`` keys.

    Only the keys present are written. Secrets may carry the "unchanged"
    sentinel returned by the read endpoint, which the service skips, so the UI
    can round-trip a masked value without ever learning the real one.
    """

    updates: dict[str, str] = Field(default_factory=dict)


class RunContinueRequest(BaseModel):
    """Continue a terminal run with a follow-up prompt (ADR-0026).

    ``max_steps`` left unset inherits the parent's budget, which is what the
    operator picked for this case in the first place.
    """

    prompt: str
    max_steps: int | None = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "prompt is required: a continuation exists to give the agent a "
                "new instruction. To re-run the original prompt, use retry."
            )
        return normalized

    @field_validator("max_steps")
    @classmethod
    def validate_max_steps(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("max_steps must be at least 1")
        return value


class RunCreate(BaseModel):
    # run_id is optional: when omitted (and no label), the server assigns the
    # next free <case>-K-NNN id (ADR-0006). An explicit run_id or label is used
    # verbatim and still 409s on duplicate.
    run_id: str | None = None
    label: str | None = None
    config: RunConfigCreate


def _unset_axis() -> list[str | None]:
    """A one-entry axis meaning "do not vary this dimension"."""
    return [None]


class RunBatchCreate(BaseModel):
    """One request that expands into the cross-product of its axes.

    Every list is an axis: N cases by M harnesses by ... by ``repetitions``
    runs, all sharing the scalar fields below. One entry per list reproduces what
    ``POST /api/runs`` would have created, which is what keeps the single-run
    path from needing a special case here.

    ``resume`` is deliberately absent. It resumes *a specific run's* workspace,
    which is meaningless when the request is asking for N fresh trials.
    """

    case_ids: list[str]
    prompt_variants: list[str]
    harnesses: list[str] = Field(default_factory=lambda: ["opencode"])
    # `None` means "unset", matching RunConfigCreate. A batch that does not vary
    # the axis passes a single-element list.
    executor_models: list[str | None] = Field(default_factory=_unset_axis)
    planner_models: list[str | None] = Field(default_factory=_unset_axis)
    # Target repositories to run against (ADR-0029) — the Mode B axis: one case
    # family, N repositories, harness held fixed. The default single empty
    # string means "no external target", which is what every batch created
    # before targets existed asked for.
    target_repo_ids: list[str] = Field(default_factory=lambda: [""])
    # The Mode A axis for an external target: run the same case with the repo's
    # own conventions and with the lab harness projected on top.
    harness_sources: list[HarnessSource] = Field(
        default_factory=lambda: [HarnessSource.lab_root]
    )
    # Shared across the axis: one ref name resolved per repository. Empty means
    # each repository's own `default_ref` — the only sensible default when the
    # repositories do not share a branch naming convention.
    target_ref: str = ""
    repetitions: int = 1

    # Shared across every generated run.
    score_mode: str = "manual"
    max_steps: int = 1
    dry_run: bool = False
    skills_disabled: list[str] = Field(default_factory=list)
    skills_extra: list[str] = Field(default_factory=list)
    skills_invoke: list[str] = Field(default_factory=list)

    @field_validator("case_ids", "prompt_variants", "harnesses")
    @classmethod
    def validate_non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("must contain at least one entry")
        return value

    @field_validator("executor_models", "planner_models")
    @classmethod
    def validate_model_axis(cls, value: list[str | None]) -> list[str | None]:
        # An empty list would silently collapse the cross-product to zero runs
        # and report success. Ask for [null] instead — it says "do not vary".
        if not value:
            raise ValueError("must contain at least one entry (use [null] for unset)")
        return value

    @field_validator("repetitions")
    @classmethod
    def validate_repetitions(cls, value: int) -> int:
        if value < 1:
            raise ValueError("repetitions must be at least 1")
        return value


class RunBatchRead(BaseModel):
    batch_id: str
    count: int
    run_ids: list[str]


class RunRead(BaseModel):
    run_id: str
    state: RunState
    command: list[str]
    score: int | None = None
    interventions: int | None = None
    notes: str = ""
    failure_reason: str | None = None


class HealRequest(BaseModel):
    """One of ``health_service.HEAL_ACTIONS`` — validated there, not here, so
    the allowlist has a single home."""

    action: str


class GroupIssueRequest(BaseModel):
    proposal_ids: list[int] = Field(min_length=1)
    model: str | None = None


class ValidationRead(BaseModel):
    run_id: str
    objective: dict = Field(default_factory=dict)
    cost: dict = Field(default_factory=dict)
    effort: dict = Field(default_factory=dict)
    subjective: dict = Field(default_factory=dict)
    composite: float | None = None


class CaseDraftRequest(BaseModel):
    description: str
    model: str | None = None
    selected_skills: list[str] = Field(default_factory=list)
    # Ids already taken in the catalog. The assistant proposes a case id and
    # the service keeps it free, so the wizard never opens with an id whose
    # save is going to be refused.
    existing_case_ids: list[str] = Field(default_factory=list)
    # The repository the case is being written for, when there is one. A case
    # drafted blind against someone else's codebase names files that do not
    # exist and a gate that is not theirs; with it, the assistant reads the
    # checkout the run will actually use (ADR-0042).
    target_id: str = ""


class DroppedCommand(BaseModel):
    """A validation command the assistant produced but the lab would not run."""

    command: str
    reason: str


class CaseDraftResponse(BaseModel):
    subject: str
    instruction: str
    validation_commands: list[str] = Field(default_factory=list)
    max_steps: int = 10
    # The rest of the case's shape. Optional because the subject-validation
    # wizard only needs subject/instruction/commands and fills the remaining
    # fields itself; the Cases form saves a whole case in one press and would
    # otherwise leave a model that already decided these things unread.
    case_id: str = ""
    description: str = ""
    case_type: str = CaseType.single_prompt
    step_prompts: list[str] = Field(default_factory=list)
    # What the model wrote that was screened out, so the person sees the gap
    # here rather than after the runs have spent their budget.
    dropped_commands: list[DroppedCommand] = Field(default_factory=list)
    attempts: int = 1
