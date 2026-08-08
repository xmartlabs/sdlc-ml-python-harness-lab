from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
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
    swe_pro_instance = "swe_pro_instance"


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
    skills_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    skills_extra: list[str] = Field(default_factory=list, sa_column=Column(JSON))
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


class Sandbox(SQLModel, table=True):
    """An in-platform validation sandbox container for a run.

    Per run + kind (`terminal` = ttyd web shell, `desktop` = Xvfb+noVNC), the
    worker launches a container mounting an ephemeral copy of the run's
    workspace and maps a host port; the frontend iframes `url`. `status` tracks
    the lifecycle for polling.
    """

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    kind: str = "terminal"  # terminal | desktop
    status: str = "starting"  # starting | running | stopped | failed
    container_name: str = ""
    host_port: int | None = None
    url: str = ""
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Audit(SQLModel, table=True):
    """Qualitative LLM audit of a run (descriptive behavior diagnostic).

    A strong auditor model reads the run's artifacts (logs, diff, usage, score,
    available rules) and writes a Markdown diagnostic: skill/rule usage,
    efficiency/loops, SDLC adherence, failure points. Runs in the worker (an LLM
    HTTP call, up to a few minutes), so `status` tracks progress for polling.
    """

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    auditor_model: str = ""
    status: str = "running"  # running | done | failed
    markdown: str = ""
    # Structured per-item verdicts (skills/docs/hooks/subagents) from the
    # second auditor pass: {"items": [{name, kind, used_evidence, verdict,
    # note}], "parse_error"?}. Optional — old audits have an empty dict.
    findings_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StagedSkill(SQLModel, table=True):
    """A skill under trial: authored or imported in the UI, not yet governed.

    The DB is the source of truth; ``data/skills_staging/<name>/`` is a
    rebuildable materialization of it (git-ignored) that the runner injects
    into a run via --extra-skill. That direction matters: a real skill ships
    ``scripts/`` and ``references/`` alongside SKILL.md, and wiping ``data/``
    or cloning fresh must not lose them (ADR-0017).

    Promotion writes the whole tree into .github/skills-external/ and re-syncs
    the native views — committing that result is always a human action.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    # Mirror of SKILL.md, kept so every existing reader keeps working.
    # Deprecated in favour of StagedSkillFile; see ADR-0017.
    content: str = ""
    status: str = "draft"  # draft | testing | promoted | archived
    # Digest of the whole tree (folder_sha256_v2) — flips the arm on any edit.
    content_hash: str = ""
    # Provenance of an imported skill; empty for one authored in the UI.
    source_url: str = ""
    source_ref: str = ""
    source_kind: str = ""  # manual | url | archive | git | upload
    imported_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StagedSkillFile(SQLModel, table=True):
    """One file of a staged skill's tree, stored so `data/` stays disposable.

    ``path`` is relative to the skill root and always POSIX
    (``scripts/run.sh``). Text is stored verbatim; anything that is not valid
    UTF-8 is base64-encoded and marked ``encoding='base64'``, which is also the
    only case subject to the binary size cap.
    """

    id: int | None = Field(default=None, primary_key=True)
    staged_skill_id: int = Field(index=True, foreign_key="stagedskill.id")
    path: str = Field(index=True)
    content: str = ""
    encoding: str = "utf-8"  # utf-8 | base64
    sha256: str = ""
    size: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GatewayModel(SQLModel, table=True):
    """A model the lab can route to, curated from the live gateway list.

    The gateway is the discovery source; this table is the *routing* source the
    web overlay is built from (data/app/gateway-models.json), replacing the
    manual `make sync-gateway-models` step for everyday use.

    Ids that disappear from the gateway are marked ``live=False`` and never
    deleted: a provider that is temporarily down must not cost the user the
    human label they curated.
    """

    id: int | None = Field(default=None, primary_key=True)
    # Bare LiteLLM id (no `gateway/` prefix) — the key opencode resolves.
    model_id: str = Field(index=True, unique=True)
    label: str = ""
    enabled: bool = True
    is_default: bool = False
    live: bool = True
    # Reserved for per-model opencode options (id remaps, context limits...).
    # Unlike the cosmetic label, these DO change the experimental arm, which is
    # why the overlay hash enters condition_hash.
    options_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class SavedProposal(SQLModel, table=True):
    """A run's improvement proposal snapshotted into the DB (UI worklist).

    Source of truth for synthesis stays data/runs/<id>/improvements.json; this
    row is the cross-run index the Improvements page selects from to build a
    combined sanitized issue. Run context is denormalized so the list needs no
    joins. (run_id, title) is the idempotency key for re-saves.
    """

    __table_args__ = (UniqueConstraint("run_id", "title"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    title: str
    body: str = ""
    kind: str = ""  # skill | doc | subagent | hook | process
    target: str = ""
    repo_hint: str = ""
    repo: str = ""
    created_issue_url: str | None = None
    case: str = ""
    harness: str = ""
    executor_model: str = ""
    generator_model: str = ""
    # When the run itself was executed (metadata.json created_at) and which
    # harness/template surface it ran against — the signals for comparing
    # proposals across template iterations. template_version is the friendly
    # label (installed release ref like v0.3.0, else short lab commit);
    # template_fingerprint is the exact harness_fingerprint.harness_hash.
    run_created_at: datetime | None = None
    template_version: str = ""
    template_fingerprint: str = ""
    status: str = "saved"  # saved | issued
    group_issue_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GroupIssue(SQLModel, table=True):
    """A combined sanitized issue generated from N selected SavedProposals."""

    id: int | None = Field(default=None, primary_key=True)
    repository: str = "marcosdh1987/ml-python-base"
    title: str = ""
    markdown: str = ""
    form_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    proposal_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    source_run_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    model: str = ""
    created_at: datetime = Field(default_factory=utc_now)


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
