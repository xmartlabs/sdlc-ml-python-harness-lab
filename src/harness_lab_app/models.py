from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _default_template_repository() -> str:
    # Imported lazily inside the factory so the model module stays importable
    # without settings/.env present (schema generation, offline tooling).
    from harness_lab_app.config import get_settings

    return get_settings().template_repository


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


class CaseProvenance(StrEnum):
    """Where a case came from, which is what its authority rests on.

    ``public`` is a published benchmark (SWE-bench and friends) — comparable
    across labs, unrelated to any one organization. ``synthetic`` is
    handcrafted. ``organization`` is derived from a real repository's own
    history, and is the only provenance that makes a result about *that team's*
    work mean anything.
    """

    public = "public"
    synthetic = "synthetic"
    organization = "organization"


class CaseStatus(StrEnum):
    active = "active"
    inactive = "inactive"
    deleted = "deleted"


class CaseSyncState(StrEnum):
    not_synced = "not_synced"
    synced = "synced"
    modified_since_sync = "modified_since_sync"
    imported_from_yaml = "imported_from_yaml"


class ExperimentMode(StrEnum):
    """What kind of question a set of runs answers (ADR-0028).

    The mode is not decoration: it decides which comparisons are valid, and the
    lab refuses a design that contradicts the mode it declares.
    """

    harness_evaluation = "harness_evaluation"
    cross_repo = "cross_repo"
    discovery = "discovery"


class ExperimentStatus(StrEnum):
    draft = "draft"
    running = "running"
    complete = "complete"
    abandoned = "abandoned"


class ConditionRole(StrEnum):
    control = "control"
    treatment = "treatment"


class HarnessSource(StrEnum):
    """Where the agent-visible harness comes from for a run (ADR-0031).

    ``lab_root`` is the historical behaviour and the default: the agent runs
    from the lab repository, so the lab's governance surface is what it sees.

    ``native`` and ``injected`` both put the agent *inside* the target
    repository. They differ in what is there when it arrives — nothing but the
    repo's own conventions, or the lab's harness materialized on top. That pair
    is the comparison: same repo, same case, same model, harness varied.
    """

    lab_root = "lab_root"
    native = "native"
    injected = "injected"


# The two sources that require an external target: both run the agent inside the
# cloned repository, which does not exist for a lab-root run.
IN_TARGET_HARNESS_SOURCES = frozenset({HarnessSource.native, HarnessSource.injected})


class TargetStatus(StrEnum):
    active = "active"
    archived = "archived"


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
    provenance: CaseProvenance = CaseProvenance.synthetic
    # The surface a correct solution is expected to touch, and the surface it
    # must not. `allowed_files` above is the agent-facing permission; these two
    # are the *measurement* — a solution that edits a forbidden path is wrong
    # even when the tests pass, which is exactly the failure an organizational
    # benchmark exists to catch.
    files_expected: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    files_forbidden: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metadata_json: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TargetRepository(SQLModel, table=True):
    """A repository a case can be run against, independent of the case (ADR-0029).

    The repository is paired with a case *at the run*, not pinned to it, so one
    case can be evaluated across many repositories (Mode B) and a client repo can
    be paired with a case derived from its own history.

    ``setup_commands`` run after checkout and before the agent (dependency
    install, codegen); ``validation_commands`` are the repo's own gates and take
    precedence over the case's when a run has a target — a repo knows how it is
    tested better than a portable case does. Both are shell strings supporting
    the same ``{workspace}`` / ``{run_id}`` placeholders as ``Case``.

    ``base_commit`` is NOT stored here: a repository is a moving thing and the
    immutable commit belongs to the run that used it (``RunConfig.target_commit``).
    """

    id: int | None = Field(default=None, primary_key=True)
    target_id: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    repository_url: str
    default_ref: str = "main"
    status: TargetStatus = TargetStatus.active
    setup_commands: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    validation_commands: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    language: str = ""
    team: str = ""
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
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
    # Follow-up instruction for a continuation run (ADR-0026). Empty for every
    # ordinary run: it replaces the case prompt only when the run has a parent.
    continuation_prompt: str = ""
    skills_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    skills_extra: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # External target repository this run works against (ADR-0029). All three
    # are empty for a run with no external target — which is every run created
    # before targets existed, so absence is the historical default, not a gap.
    # `target_commit` is resolved from `target_ref` at creation and is the
    # immutable fact: it, not the ref, is what makes the run reproducible.
    target_repo_id: str = ""
    target_ref: str = ""
    target_commit: str = ""
    # Which harness the agent sees (ADR-0031). `lab_root` for every run created
    # before harness conditions existed, which is exactly what those runs did.
    harness_source: HarnessSource = HarnessSource.lab_root
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
    # Groups the runs created by one POST /api/runs/batch. Null for a run
    # created singly, and for every run that predates batching — the column is
    # a grouping label, never a required key.
    batch_id: str | None = Field(default=None, index=True)
    # The experiment this run belongs to, and which arm of it (ADR-0033). Null
    # for a run created outside an experiment — every run that predates them,
    # and every ad-hoc run since.
    experiment_id: str | None = Field(default=None, index=True)
    condition_label: str | None = Field(default=None, index=True)
    # The run this one continues from, seeding its workspace (ADR-0026). Null
    # for an original run, and for every run that predates continuations — no
    # backfill invents lineage that never existed.
    parent_run_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class Experiment(SQLModel, table=True):
    """A question, a design, and the runs that answer it (ADR-0033).

    ``Run`` stops being the top-level noun. An experiment states what is being
    asked *before* anything executes, which is what separates a measurement from
    a pile of runs that happen to share a shape.

    ``question`` is required on purpose. An experiment nobody can state the
    question for is a batch, and calling it an experiment lends it authority it
    has not earned.
    """

    id: int | None = Field(default=None, primary_key=True)
    experiment_id: str = Field(index=True, unique=True)
    name: str
    question: str
    mode: ExperimentMode
    status: ExperimentStatus = ExperimentStatus.draft
    case_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    prompt_variants: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    repetitions: int = 1
    # Per-case override of ``repetitions``. Empty for an ordinary experiment,
    # where every case is repeated the same number of times. A curated suite
    # deliberately repeats a cheap deterministic canary more often than an
    # expensive SWE-bench instance, and flattening that to one number would
    # either inflate the expensive case or under-sample the canary.
    case_repetitions: dict[str, int] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    # The regression suite this experiment was generated from, when it was
    # (ADR-0037). Empty for a hand-designed experiment.
    suite_id: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def repetitions_for(self, case_id: str) -> int:
        """How many times this case runs in each arm."""
        return max(1, self.case_repetitions.get(case_id, self.repetitions))


class ExperimentCondition(SQLModel, table=True):
    """One arm of an experiment, declared before it runs.

    The overrides are the axis values this arm fixes — executor model, target
    repository, harness source, skill ablation. Declaring them up front is the
    difference from a batch, where the arm could only ever be *recovered* after
    the fact by parsing a run's command string.
    """

    id: int | None = Field(default=None, primary_key=True)
    experiment_id: str = Field(index=True)
    label: str
    role: ConditionRole = ConditionRole.treatment
    description: str = ""
    overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)

    __table_args__ = (UniqueConstraint("experiment_id", "label"),)


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


class Score(SQLModel, table=True):
    """One recorded metric for one run, filed under a versioned definition.

    Stored as ``name`` + ``version`` rather than ``name`` alone (ADR-0034): when
    a judge or a rubric changes, the old and new quantities must not pool into a
    trend line that is an artefact of the change.

    ``provenance`` travels with the value because the difference between a
    command's exit status and a model's opinion is invisible once both are
    numbers on a screen.
    """

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    name: str = Field(index=True)
    version: str = "v1"
    category: str = ""
    provenance: str = ""
    source: str = ""
    value_num: float | None = None
    value_bool: bool | None = None
    value_text: str | None = None
    # Why the evaluator produced this, and how sure it is. Meaningful for a
    # judgement, usually empty for a fact.
    reason: str = ""
    confidence: float | None = None
    # Which instrument produced the value: evaluator, adapter version, judge
    # model, rubric. Empty for a deterministic score, where the code is the
    # provenance; populated for anything a model decided, so a rubric change is
    # visible on the value and not only in a changelog.
    provenance_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)

    __table_args__ = (UniqueConstraint("run_id", "name", "version"),)


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
    # The governance surface this audit judged the run against: which files the
    # run could actually read, where they came from (`target_workspace` vs
    # `lab_root`), and a content fingerprint of the set. An audit whose
    # denominator is unrecorded cannot be re-read a month later, and a `native`
    # verdict scored against the lab's own rules has to be identifiable rather
    # than trusted (see `harness_lab_app.governance`).
    governance_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FindingSeverity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class FindingStatus(StrEnum):
    """A finding's life: suggested → observed → hypothesis → tested → settled.

    ``suggested`` is the machine's half of the loop and deliberately not the
    same thing as ``open``: the lab may notice a pattern, but nothing it notices
    becomes a finding of record until a person accepts it and puts their name on
    it (ADR-0035, §14).
    """

    suggested = "suggested"
    open = "open"
    proposed = "proposed"
    experiment_running = "experiment_running"
    validated = "validated"
    refuted = "refuted"
    dismissed = "dismissed"


class Finding(SQLModel, table=True):
    """Something observed about how an organization works with agents.

    The unit that turns a pile of runs into consulting output (ADR-0035): an
    observation, the evidence behind it, and a proposed intervention that can be
    *tested* rather than asserted.

    ``experiment_id`` is what closes the loop. A finding whose recommendation
    was validated by an experiment carries the evidence for it; one that was
    refuted keeps that on the record too, which is the part a consultancy is
    normally tempted to drop.
    """

    id: int | None = Field(default=None, primary_key=True)
    finding_id: str = Field(index=True, unique=True)
    title: str
    body: str = ""
    severity: FindingSeverity = FindingSeverity.medium
    status: FindingStatus = FindingStatus.open
    # Where it was observed. Both optional: a finding may span repositories.
    target_repo_id: str = ""
    # Runs and cases the observation rests on. A finding with no evidence is an
    # opinion, and the field being empty is how that stays visible.
    evidence_run_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    affected_case_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    recommendation: str = ""
    # The change proposed to test the recommendation (a skill, a rule, a gate).
    intervention: str = ""
    # Where the finding came from. Empty for one a person wrote unprompted; for
    # an accepted or rejected suggestion this is the suggestion's stable key
    # (e.g. "regression:cli-textstats-v1"), which is what stops the same
    # pattern being suggested again after somebody has already ruled on it.
    origin: str = ""
    # The person accountable for the finding. A suggestion has none until it is
    # accepted, and accepting without one is refused: an unowned finding is an
    # assertion nobody has to defend.
    author: str = ""
    accepted_at: datetime | None = None
    # The experiment that tested it, once one exists.
    experiment_id: str | None = Field(default=None, index=True)
    outcome: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Assessment(SQLModel, table=True):
    """A maturity assessment of how an organization works with coding agents.

    Scored **by a human**, against a published rubric, with every claim linked to
    runs or findings the lab produced (ADR-0035). Not automated: a number the
    lab computes about a team's practices, from a handful of runs, would carry
    more authority than the evidence supports.
    """

    id: int | None = Field(default=None, primary_key=True)
    assessment_id: str = Field(index=True, unique=True)
    organization: str
    summary: str = ""
    # {dimension: {"level": 0-5, "rationale": str, "evidence": [run/finding ids]}}
    dimensions: dict = Field(default_factory=dict, sa_column=Column(JSON))
    assessed_by: str = ""
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
    # Resolved per instance, not at import: retargeting the template repo must
    # apply to new issues without rewriting the slug rows already carry.
    repository: str = Field(
        default_factory=lambda: _default_template_repository(),
    )
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
