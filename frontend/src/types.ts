export type RunState =
  | "draft"
  | "pending"
  | "running"
  | "awaiting_manual_score"
  | "scoring"
  | "auditing"
  | "completed"
  | "failed"
  | "cancelled";

export type CaseRecord = {
  case_id: string;
  name: string;
  type: string;
  status: string;
  sync_state: string;
  description: string;
  prompt_variants?: Record<string, string>;
  tags?: string[];
  success_criteria?: Record<string, string>;
  step_prompts?: string[];
  scoring_rubric?: string;
  constraints?: string[];
  validation_commands?: string[];
  max_steps?: number;
  origin_target_id?: string;
};

export type CaseCreatePayload = {
  case_id: string;
  name: string;
  type: string;
  description: string;
  tags: string[];
  prompt_variants: Record<string, string>;
  step_prompts: string[];
  success_criteria: Record<string, string>;
  scoring_rubric: string;
  constraints: string[];
  validation_commands: string[];
  max_steps: number;
  origin_target_id?: string;
};

export interface CaseDraftRequest {
  description: string;
  model?: string;
  selected_skills: string[];
  /** Draft against this repository's checkout rather than in the abstract. */
  target_id?: string;
  /** Ids already in the catalog, so the proposed one is free to save. */
  existing_case_ids?: string[];
}

export interface DroppedCommand {
  command: string;
  reason: string;
}

export interface CaseDraftResponse {
  subject: string;
  instruction: string;
  validation_commands: string[];
  max_steps: number;
  /** Commands the model wrote that the lab would not run, with the reason. */
  dropped_commands: DroppedCommand[];
  attempts: number;
  /** The rest of the case's shape — the Cases form saves all of it at once. */
  case_id?: string;
  description?: string;
  case_type?: string;
  /** What the judge grades against; model-written or defaulted, never empty. */
  scoring_rubric?: string;
  step_prompts?: string[];
}

export type RunRecord = {
  run_id: string;
  state: RunState;
  command: string[];
  // API timestamps are naive UTC strings (no offset) — parse them with
  // dates.ts/parseApiDate, never with a bare `new Date(...)`.
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  score?: number | null;
  interventions?: number | null;
  notes?: string;
  failure_reason?: string | null;
  // Improvement-synthesis status, derived from data/runs/<id>/improvements.json:
  // "yes" = proposed changes, "no" = ran but proposed none, "pending" = not run.
  improvements?: "yes" | "no" | "pending";
  // Verdict of the automated checks: false means the produced code was run and
  // failed. null when they have not run, the case has no objective criterion, or
  // every check was inconclusive (timed out / killed) and so measured nothing.
  // Without this a run that does not even start is indistinguishable in the
  // list from one merely waiting for its manual score.
  objective_passed?: boolean | null;
  // Why it failed, classified server-side (see run_failures.py) so the list,
  // the leaderboard and the API share one definition. "lab" means the run
  // measured nothing — it is excluded from the leaderboard and should be
  // retried rather than read as a result.
  failure_kind?: "lab" | "stalled" | "timeout" | "agent" | null;
  failure_label?: string | null;
  // Set when the run came from POST /api/runs/batch; null for a run created
  // singly and for every run that predates batching.
  batch_id?: string | null;
  // The run this one continues from (ADR-0026). Null for an original run.
  parent_run_id?: string | null;
  // The arm this run answers for. Empty for a run created outside an
  // experiment — which is a real state, not a bug, but one worth seeing in the
  // list: an unattributed run counts towards no comparison.
  experiment_id?: string | null;
  condition_label?: string | null;
};

/**
 * The condition a run *was*, composed from run, config, experiment and target.
 *
 * §16: a run page leads with this and keeps the logs available further down.
 * Which arm of which experiment, against which commit, with whose governance,
 * is what makes a log worth reading in the first place.
 */
export type RunContext = {
  run_id: string;
  experiment_id: string;
  experiment_question: string;
  experiment_mode: string;
  condition_label: string;
  case_id: string;
  case_name: string;
  prompt_variant: string;
  harness: string;
  harness_source: string;
  executor_model: string;
  planner_model: string;
  target_repo_id: string;
  target_name: string;
  target_url: string;
  target_ref: string;
  target_commit: string;
  validation_commands: string[];
  max_steps: number;
  parent_run_id: string;
};

export type RunEventRecord = {
  id: number;
  run_id: string;
  level: string;
  message: string;
  created_at: string;
};

export type ValidationCheck = {
  name: string;
  passed: boolean;
  // false when the command timed out, was killed, never launched, could not be
  // found or had no network: the check says something about the lab, not about
  // the agent's code. Absent on rows written before validation recorded it.
  conclusive?: boolean;
  // Which of those it was. Absent on rows written before ADR-0061.
  inconclusive_reason?: string | null;
  exit_code: number | null;
  detail: string;
};

export type ValidationRecord = {
  run_id: string;
  objective: {
    strategy?: string;
    // true = every conclusive check passed; false = at least one really failed;
    // null = nothing was measured (no criterion, or every check inconclusive).
    passed?: boolean | null;
    summary?: string;
    checks?: ValidationCheck[];
  };
  cost: {
    available?: boolean;
    total_tokens?: number | null;
    cost_usd?: number | null;
    time_minutes?: number | null;
  };
  effort: { steps?: number | null; interventions?: number | null };
  subjective: {
    functional_score?: number | null;
    notes?: string;
    score_source?: "manual" | "objective" | "llm_judge" | string;
  };
  composite: number | null;
};

export type AttributionRecord = {
  run_id: string;
  tools: Record<string, number>;
  files: { read?: string[]; edited?: string[] };
  bash_commands: Record<string, number>;
  agent_modes: Record<string, number>;
  harness_usage: {
    governance_docs_available?: string[];
    governance_docs_read?: string[];
    governance_coverage?: string;
    skills_available?: string[];
    skills_consulted?: string[];
    skills_note?: string;
    /**
     * Which repository the coverage denominator came from (ADR-0036).
     * `target_workspace` = the run's own archived workspace, `lab_root` = the
     * repository the lab itself runs from. 2/9 against a client's documents
     * and 2/9 against the lab's are different measurements, and without this
     * they render identically.
     */
    governance_source?: string;
    governance_fingerprint?: string;
    harness_source?: string;
    governance_warning?: string;
  };
  summary: string;
  steps_parsed: number;
};

export type AuditFinding = {
  name: string;
  kind: "skill" | "doc" | "hook" | "subagent";
  used_evidence: string;
  verdict: "useful" | "redundant" | "improve" | "unknown";
  note: string;
};

export type AuditFindings = {
  items?: AuditFinding[];
  parse_error?: string;
};

/** The governance surface an audit judged one run against (ADR-0036). */
export type GovernanceSurface = {
  source: string;
  root: string;
  harness_source: string;
  rules_file: string;
  files: string[];
  file_count: number;
  fingerprint: string;
  truncated: boolean;
  warning: string;
};

/** The LLM reading of a finished experiment: prose over the summary's evidence. */
export type ExperimentAuditRecord = {
  experiment_id: string;
  auditor_model: string;
  status: "running" | "done" | "failed";
  markdown: string;
  evidence_json?: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
};

/**
 * The LLM reading of an ad-hoc comparison: prose over the Compare table.
 *
 * A comparison is a selection someone made in the browser, so it has no id of
 * its own. `comparison_key` is a hash of the sorted run-id set: the same runs
 * always resolve to the same reading, whatever order the columns were added in.
 */
export type CompareAuditRecord = {
  comparison_key: string;
  run_ids: string[];
  auditor_model: string;
  status: "running" | "done" | "failed";
  markdown: string;
  evidence_json?: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
};

/**
 * The LLM reading of a repository's harness, before the lab runs anything.
 *
 * `findings` is the structured half (what the model was asked to return as
 * JSON) and `markdown` the rendered report. `next_step` is decided by the lab
 * from the deterministic evidence, never by the model: which runtimes the
 * runner ships and whether any harness file exists are facts, not judgements.
 */
export type RepositoryAuditNextStep = "continue" | "no_harness" | "unsupported_runtime";

export type RepositoryAuditComponent = {
  kind: "rules_file" | "skill" | "agent" | "command" | "hook" | "ci" | "docs" | "other";
  name: string;
  assessment: string;
};

export type RepositoryAuditUseCase = {
  title: string;
  why: string;
  validation_hint: string;
};

/**
 * The lab's verdict on one proposed case, decided deterministically.
 *
 * The model proposes the case; a capability match decides whether the runner
 * can settle it. `static` is the reason a Shell repository is still evaluable:
 * a case answered by reading the tree depends on no runtime at all.
 */
export type RepositoryAuditCaseStatus = "runnable" | "blocked" | "static";

export type RepositoryAuditCase = {
  title: string;
  why: string;
  validation_hint: string;
  /**
   * The hint, split. A `validation_hint` is either a command or a written
   * check; the model often writes the command inside backticks with prose
   * around it, so the lab separates the two rather than making every consumer
   * guess. `validation_command` is empty for a static case, `validation_note`
   * for a bare command. Absent on audits stored before the split.
   */
  validation_command?: string;
  validation_note?: string;
  /**
   * What had to change for the command to measure what it claims — anchoring a
   * repository path to `{workspace}`, or turning a glob handed to an
   * interpreter into the loop it meant. Empty when nothing was repaired.
   */
  validation_repair?: string;
  /** Empty for a static case: it needs none. */
  runtime: string;
  /** What the runner measured, when it could. */
  runtime_version: string;
  /** What the repository declares it targets, when it declares one. */
  required_runtime_version: string;
  compatibility: "exact" | "partial" | "unknown";
  status: RepositoryAuditCaseStatus;
  reason: string;
};

export type RepositoryAuditCasePlan = {
  cases: RepositoryAuditCase[];
  runnable: number;
  blocked: number;
  static: number;
  coverage: "none" | "partial" | "full";
};

/**
 * One harness component and what the auditor read into it (ADR-0058).
 *
 * `claim`, `trigger`, `observable`, `lab_counterpart` and `needs` are the
 * model's. `evaluable_here`, `evaluable_reason` and `counterpart_valid` are
 * the lab's, decided from declared service capabilities and the lab's own
 * skill catalogue — a fact the model cannot override.
 */
export type RepositoryAuditMapRow = {
  kind: "skill" | "hook" | "rule" | "agent" | "command" | "unknown";
  name: string;
  source_path: string;
  claim: string;
  trigger: string;
  observable: string;
  lab_counterpart: string;
  needs: string[];
  evaluable_here: boolean;
  evaluable_reason: string;
  counterpart_valid: boolean;
};

export type RepositoryAuditShape = {
  shape: "in_place" | "installable" | "both" | "unknown";
  install_entry: string;
  install_entry_exists: boolean;
  unit_of_work: string;
};

/** An experiment arm the lab derived from a kept case — never model-written. */
export type RepositoryAuditDerivedCondition = {
  label: string;
  role: "control" | "treatment";
  overrides: Record<string, unknown>;
};

export type RepositoryAuditProposedCase = {
  title: string;
  /** Names from the component map this case exercises. */
  exercises: string[];
  measures: "harness_claim" | "harness_development" | "unknown";
  task: string;
  observable: string;
  validation_hint: string;
  required_runtime_version: string;
  /** Empty while the case is kept; otherwise why the lab will not run it. */
  dropped_reason: string;
  conditions?: RepositoryAuditDerivedCondition[];
  conditions_note?: string;
};

export type RepositoryAuditFindings = {
  summary: string;
  harness_verdict: "complete" | "partial" | "none" | "unknown";
  language_notes: string;
  standards: string[];
  gaps: string[];
  parse_error?: string;
  /** Audits recorded before ADR-0058. */
  harness_components?: RepositoryAuditComponent[];
  use_cases?: RepositoryAuditUseCase[];
  /** Audits recorded under ADR-0058. */
  component_map?: RepositoryAuditMapRow[];
  shape?: RepositoryAuditShape;
  cases?: RepositoryAuditProposedCase[];
  warnings?: string[];
};

export type RepositoryAuditHealth = {
  status: "pass" | "fail" | "unmeasured";
  reason?: string;
  counts?: { passed: number; failed: number } | null;
};

export type RepositoryAuditRuntimeCapability = {
  runtime: string;
  available: boolean;
  /** Empty when the runtime was declared by the image recipe but never probed. */
  version: string;
  executable: string;
  source: "declared" | "probed";
};

export type RepositoryAuditRuntime = {
  ecosystems: string[];
  supported: string[];
  unsupported: string[];
  /**
   * Whether the runner can execute the repository's *dominant* language. It no
   * longer decides whether the repository can be evaluated — `case_plan` does,
   * per case. Kept because it is still the honest answer to its own question.
   */
  runner_can_execute: boolean;
  /** Absent on audits recorded before capabilities were part of the evidence. */
  capabilities?: RepositoryAuditRuntimeCapability[];
};

/** One deterministic harness check (ADR-0065), decided by code, not a model. */
export type HarnessCheck = {
  id: string;
  title: string;
  status: "pass" | "warn" | "fail" | "not_applicable";
  evidence: string;
  why: string;
  fix: string;
};

export type RepositoryAuditRecord = {
  target_id: string;
  commit: string;
  auditor_model: string;
  status: "running" | "done" | "failed";
  markdown: string;
  findings_json: RepositoryAuditFindings | Record<string, never>;
  evidence_json: {
    runtime?: RepositoryAuditRuntime;
    case_plan?: RepositoryAuditCasePlan;
    health_at_pin?: RepositoryAuditHealth;
    harness_present?: boolean;
    next_step?: RepositoryAuditNextStep;
    next_step_reason?: string;
    /** Absent on audits recorded before the checks existed. */
    harness_checks?: HarnessCheck[];
    [key: string]: unknown;
  };
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type AuditRecord = {
  run_id: string;
  auditor_model: string;
  status: "running" | "done" | "failed";
  markdown: string;
  findings_json?: AuditFindings;
  /** Empty for audits recorded before the surface was pinned per run. */
  governance_json?: GovernanceSurface | Record<string, never>;
  error: string | null;
  created_at: string;
  updated_at: string;
};

// Tracing status for one run. `enabled: false` is the normal state of a lab
// that never turned Langfuse on; `reason` explains why there is nothing to see.
export type LangfuseStatus = {
  enabled: boolean;
  reason: string;
  session_id: string;
  session_url: string;
  auto_export: boolean;
};

export type LangfuseExport = {
  run_id: string;
  session_id: string;
  session_url: string;
  ok: boolean;
  spans: number;
  traces: number;
  reason: string;
};

export type ArtifactRecord = {
  run_id: string;
  name: string;
  path: string;
  media_type: string;
  size_bytes: number;
};

// The slice of data/runs/<id>/metadata.json the harness catalog reads.
export type RunMetadata = {
  skills_bundle?: string[];
  subagents?: string[];
  skills_disabled?: string[];
  skills_experimental?: string[];
  harness_fingerprint?: {
    governance_docs?: Record<string, string>;
    hooks?: Record<string, string>;
  };
  condition_hash?: string;
};

export type SandboxRecord = {
  run_id: string;
  kind: "terminal" | "desktop";
  status: "starting" | "running" | "stopped" | "failed";
  container_name: string;
  host_port: number | null;
  url: string;
  error: string | null;
};

export type HarnessSkill = {
  name: string;
  kind: string;
  description: string;
};

export type StagedSkillRecord = {
  name: string;
  description: string;
  /** Mirror of SKILL.md. Kept for compatibility; the file list is the truth. */
  content: string;
  status: "draft" | "testing" | "promoted" | "archived";
  content_hash: string;
  source_url: string;
  source_ref: string;
  source_kind: string;
  imported_at: string | null;
  created_at: string;
  updated_at: string;
};

export type StagedSkillFileRecord = {
  path: string;
  content: string;
  /** "utf-8" for text; "base64" for binary assets, which the UI cannot edit. */
  encoding: string;
  sha256: string;
  size: number;
};

/** One skill inside a source that holds several — a catalog repo. */
export type StagedSkillImportChoice = {
  name: string;
  path: string;
  description: string;
};

/** What an unconfirmed import returns: reviewed by the user, never written. */
export type StagedSkillImportPreview = {
  preview: true;
  choices: StagedSkillImportChoice[];
  name: string;
  description: string;
  source_url: string;
  source_ref: string;
  source_kind: string;
  warnings: string[];
  total_bytes: number;
  files: { path: string; size: number }[];
};

/**
 * A catalog source that has not been narrowed down yet. Not an error: the user
 * picks one of `choices` and the same source is previewed again.
 */
export type StagedSkillImportChoices = {
  preview: false;
  choices: StagedSkillImportChoice[];
  detail: string;
};

export type StagedSkillPromoteResult = {
  name: string;
  written: string[];
  synced: boolean;
  next_steps: string;
};

/**
 * A repository a case can be run against, independent of the case (ADR-0029).
 *
 * `default_ref` is what a run pins when it names no ref of its own. The commit
 * itself is never stored here — a repository moves, and the immutable commit
 * belongs to the run that used it.
 */
/**
 * Which harness the agent can see (ADR-0031).
 *
 * `lab_root` is the historical default: the agent works from the lab repo and
 * edits the target from outside. `native` and `injected` both put it *inside*
 * the target — with the repo's own conventions, or with ours projected on top.
 * That pair is the comparison; `lab_root` vs `native` would mix two changes.
 *
 * `bare` needs no target (ADR-0049): the agent works in an empty workspace and
 * the runner masks the lab's governance, so it sees the prompt and nothing
 * else. It is the control a lab-native case needs — a `lab_root` arm with its
 * skills hidden still carries CLAUDE.md, the hooks and `.github/`.
 */
export type HarnessSource = "lab_root" | "native" | "injected" | "bare";

/** What the lab knows about itself. Every figure carries its denominator. */
export type OverviewRate = { rate: number | null; n: number; note: string };

/** One registry metric aggregated across the corpus, direction attached. */
export type ScoreAggregate = {
  name: string;
  version: string;
  /** Judgements are aggregated per judge model; null for facts or an unrecorded judge. */
  judge_model: string | null;
  category: string;
  provenance: string;
  direction: string;
  description: string;
  runs: number;
  mean: number | null;
  median: number | null;
  stdev: number | null;
  minimum: number | null;
  maximum: number | null;
  true_rate: number | null;
};

export type OverviewReport = {
  window_days: number;
  harness_version: string | null;
  experiments: { total: number; recent: number };
  success: OverviewRate;
  success_all_time: OverviewRate;
  regressions: {
    count: number;
    eligible_cases: number;
    rate: number | null;
    cases: string[];
  };
  cost: {
    median_usd: number | null;
    n: number;
    total_usd: number | null;
    cost_per_success: number | null;
    successes: number;
  };
  regression_suite: { cases: number; of_active: number };
  findings: { open: number; validated: number; total: number };
  scoring: { scored: number; terminal: number };
  trend: { week_ending: string; n: number; rate: number | null }[];
  runs: { total: number; recent: number };
  scores: {
    runs: number;
    adherence: ScoreAggregate | null;
    useful: ScoreAggregate | null;
    redundant: ScoreAggregate | null;
    diff_size: ScoreAggregate | null;
    forbidden: ScoreAggregate | null;
    metrics: ScoreAggregate[];
  };
};

export type ExperimentMode =
  | "harness_evaluation"
  | "cross_repo"
  | "discovery"
  /**
   * Does the tool I built work reliably across harnesses and models? (ADR-0040)
   * The harness is a setting here, not the subject — so this mode has no
   * ablated arm and reports a pass rate per arm, never a delta.
   */
  | "subject_validation"
  /**
   * Do stronger models need less harness? (ADR-0066) A full grid of harness
   * levels x models; read through `ExperimentSummary.factorial`, never a pair.
   */
  | "harness_by_model";
export type ConditionRole = "control" | "treatment";

/** A harness effect for one model: one level against the first, in points. */
export type FactorialEffect = {
  model: string;
  level: string;
  points: number | null;
  ci_low: number | null;
  ci_high: number | null;
};

export type FactorialCell = {
  level: string;
  model: string;
  runs: number;
  passed: number;
  pass_rate: number | null;
  cost_usd: number | null;
  cost_per_resolved_usd: number | null;
};

/** One tier's reliability for one arm (ADR-0069). */
export type TierReading = {
  status: "reliable" | "capable" | "not_established";
  reason: string;
  cases: number;
  runs: number;
  passed: number;
  pass_rate: number | null;
  wilson_low: number | null;
  pass_hat_3: number | null;
  /** Every case scored so far clears the per-case floor (may still be too few cases). */
  runs_ok: boolean;
};

/** Per arm, the highest difficulty tier it does reliably (ADR-0069). */
export type CapabilityBlock = {
  tiers: string[];
  arms: {
    label: string;
    frontier: string | null;
    tiers: Record<string, TierReading>;
    /** Cases still missing per tier before it can be called reliable. */
    case_shortfall: Record<string, number>;
    /** Where the frontier would sit on the runs so far, given enough cases. */
    frontier_if_enough_cases: string | null;
  }[];
};

/** The harness x model reading (ADR-0066). */
export type FactorialBlock = {
  axis: string;
  harness_levels: string[];
  models: string[];
  paired_cases: number;
  cells: FactorialCell[];
  harness_effect: FactorialEffect[];
  interaction: {
    level: string;
    from_model: string;
    to_model: string;
    points: number | null;
    ci_low: number | null;
    ci_high: number | null;
    established: boolean;
  };
  reading: string;
};

export type ExperimentRecord = {
  experiment_id: string;
  name: string;
  question: string;
  mode: ExperimentMode;
  /** What is under test when it is not the harness. Empty under every other mode. */
  subject: string;
  status: "draft" | "running" | "complete" | "abandoned";
  /** The ladder or other family this experiment is read as part of (ADR-0051). */
  family_id?: string | null;
  case_ids: string[];
  prompt_variants: string[];
  repetitions: number;
  notes: string;
  condition_count?: number;
  run_count?: number;
  /** Every run has stopped (derived from the runs, not from `status`). */
  finished?: boolean;
  warnings?: string[];
};

/**
 * The shared configuration an experiment's real runs were launched with.
 * Null while the experiment is a draft (dry-run rehearsals do not count).
 * Once set, the backend refuses a relaunch that differs — so the UI shows
 * this instead of asking for the configuration a second time.
 */
export type LaunchConfigSummary = {
  harness: string;
  executor_model: string | null;
  planner_model: string | null;
  prompt_variant: string;
  max_steps: number;
  score_mode: string;
  skills_disabled?: string[];
  skills_extra?: string[];
  skills_invoke?: string[];
};

export type ConditionRecord = {
  label: string;
  role: ConditionRole;
  description: string;
  overrides: Record<string, unknown>;
};

/** One metric aggregated over a condition's repetitions. */
export type AggregateRecord = {
  name: string;
  n: number;
  mean: number | null;
  median: number | null;
  stdev: number | null;
  minimum: number | null;
  maximum: number | null;
  missing: number;
};

/** pass@k and pass^k over one arm's cases, every arm at one shared k (ADR-0064). */
export type PassAtK = {
  k: number;
  cases: number;
  /** At least one of k tries passes, averaged over cases. */
  pass_at_k: number;
  /** All k tries pass, averaged over cases — the reliability number. */
  pass_hat_k: number;
};

export type ConditionSummary = {
  label: string;
  role: ConditionRole;
  runs: number;
  scored_runs: number;
  success_rate: number | null;
  /** 95% Wilson interval around `success_rate`; null with nothing scored (ADR-0064). */
  success_rate_ci_low?: number | null;
  success_rate_ci_high?: number | null;
  pass_k?: PassAtK | null;
  warnings: string[];
  metrics: Record<string, AggregateRecord>;
};

/** The success delta, in points, with its case-clustered 95% interval (ADR-0064). */
export type SuccessDelta = {
  control: number | null;
  treatment: number | null;
  points: number | null;
  delta_ci_low?: number | null;
  delta_ci_high?: number | null;
  /** Descriptive: the runs are also compatible with no effect. Never a test. */
  interval_crosses_zero?: boolean | null;
  /** Cases both arms scored — what the interval was resampled over. */
  paired_cases?: number;
};

/** Advice for the next design, never a verdict on this one (ADR-0064). */
export type RepetitionPlan = {
  difference_points: number;
  alpha: number;
  power: number;
  /** "assumed" plans from a 50% rate; "observed" from the noisiest arm. */
  basis: "assumed" | "observed";
  base_rate: number;
  runs_per_arm: number;
  repetitions_per_case: number;
};

export type DeltaRecord = {
  name: string;
  control: number | null;
  treatment: number | null;
  absolute: number | null;
  relative: number | null;
  direction: string;
  exceeds_spread: boolean | null;
};

export type CaseMatrixRow = {
  case_id: string;
  control: boolean | null;
  treatment: boolean | null;
  movement: "fixed" | "broken" | "unchanged" | "incomplete";
};

/** One number worth putting at the top of the page, with both arms' values. */
export type VerdictHighlight = {
  label: string;
  metric: string;
  /** A control/treatment pair — present for a mode that compares two arms. */
  control?: number | null;
  treatment?: number | null;
  /**
   * Which way is good for this metric, from the score registry. Absent on
   * summaries computed by an older backend, which rendered the pair bare and
   * left the reader to supply the direction from the label.
   */
  direction?: "higher_is_better" | "lower_is_better" | "neutral";
  /**
   * One arm on its own — present under `subject_validation`, where the label is
   * the arm and there is nothing to compare it against.
   */
  successes?: number;
  scored_runs?: number;
  rate?: number | null;
};

/**
 * The ten-second answer, computed on the backend.
 *
 * Deliberately not assembled in the browser: what an experiment is allowed to
 * claim follows from its mode and its repetition count, and that rule belongs
 * next to the design validation that enforces it — not in a template.
 */
export type VerdictBlock = {
  /** False for any mode that cannot support a causal claim. */
  causal: boolean;
  headline: string;
  claim: string;
  /** "exploratory" … "stronger evidence". Never "significant". */
  strength: string;
  /** e.g. "12 cases x 5 repetitions". */
  evidence: string;
  highlights: VerdictHighlight[];
  movements: { fixed: number; broken: number; unchanged: number; incomplete: number };
  caveats: string[];
  /**
   * How the claim should be read. Present since ADR-0046; before it the page
   * sniffed the headline for "improvement"/"regression", which is kept as a
   * fallback for summaries computed by an older backend.
   */
  tone?: "invalid" | "better" | "worse" | "neutral";
  /**
   * The sentence that would have been the claim, when validity replaced it.
   * The measurement is never withheld (ADR-0041), only demoted.
   */
  observed?: string;
};

/** One reason a comparison could not have measured the thing it names. */
export type ValidityIssue = {
  code:
    | "treatment_not_activated"
    | "infrastructure_failures"
    | "no_headroom"
    // The ceiling's mirror, and the case where nothing was measured at all.
    | "objective_unmeasured"
    | "floor_effect"
    | "mixed_treatment"
    | "design_objection"
    // Fewer than five paired cases: the interval describes these cases only.
    | "few_cases";
  severity: "invalid" | "degraded";
  /** The arm the issue belongs to, when it belongs to one. */
  arm?: string | null;
  detail: string;
  remedy: string;
};

/**
 * Validity is read before the verdict (ADR-0046).
 *
 * Not folded into `verdict.caveats` on purpose: a caveat printed under a
 * confident headline is the failure this block exists to fix.
 */
export type ValidityBlock = {
  status: "valid" | "degraded" | "invalid";
  issues: ValidityIssue[];
};

/** One cell of the per-arm, per-case grid: a count, never a verdict. */
export type CaseOutcomeCell = {
  runs: number;
  scored_runs: number;
  successes: number;
};

export type ExperimentSummary = {
  experiment_id: string;
  question: string;
  mode: ExperimentMode;
  /** The named subject under `subject_validation`; empty elsewhere. */
  subject?: string;
  repetitions: number;
  case_repetitions?: Record<string, number>;
  suite_id?: string | null;
  /** The ladder or other family this experiment is read as part of (ADR-0051). */
  family_id?: string | null;
  verdict?: VerdictBlock;
  conditions: ConditionSummary[];
  comparisons: {
    control: string;
    treatment: string;
    deltas: DeltaRecord[];
    success: SuccessDelta;
    case_matrix: CaseMatrixRow[];
  }[];
  /** How many repetitions a 20-point move would need; null without a comparison. */
  repetition_plan?: RepetitionPlan | null;
  repetitions_needed_for_20pp?: number | null;
  /**
   * Per-arm, per-case counts: {arm label: {case_id: {runs, scored_runs,
   * successes}}}. Raw grid with the repetitions kept — not a comparison:
   * `comparisons[].case_matrix` pairs a control against a treatment, which a
   * mode without a control never has.
   */
  case_outcomes?: Record<string, Record<string, CaseOutcomeCell>>;
  /** Present only under `harness_by_model` (ADR-0066). */
  factorial?: FactorialBlock | null;
  /** Present when the cases carry `tier:<name>` tags (ADR-0069). */
  capability?: CapabilityBlock | null;
  /** Whether this comparison could have measured what it names (ADR-0046). */
  validity?: ValidityBlock;
  /**
   * Per-arm run counts behind the validity call: how many ran, how many the
   * harness killed, how many left a score at all.
   */
  run_health?: Record<
    string,
    {
      runs: number;
      harness_error: number;
      failed: number;
      unhealthy: number;
      scored_runs: number;
      /** Finished runs whose checks all settled nothing (ADR-0061). */
      unmeasured?: number;
    }
  >;
};

export type FindingRecord = {
  finding_id: string;
  title: string;
  body: string;
  severity: "low" | "medium" | "high";
  status:
    | "suggested"
    | "open"
    | "proposed"
    | "experiment_running"
    | "validated"
    | "refuted"
    | "dismissed";
  target_repo_id: string;
  evidence_run_ids: string[];
  affected_case_ids: string[];
  recommendation: string;
  intervention: string;
  experiment_id: string | null;
  outcome: string;
  /** The suggestion key this finding came from, empty when a person wrote it. */
  origin?: string;
  /** Who put their name on it. Required to accept a suggestion. */
  author?: string;
  accepted_at?: string | null;
  warnings?: string[];
};

/**
 * A pattern the lab noticed. Not a finding, and the distinction is the point:
 * nothing here is a claim until a person accepts it and their name goes on it.
 */
export type SuggestionRecord = {
  key: string;
  title: string;
  body: string;
  severity: "low" | "medium" | "high";
  recommendation: string;
  intervention: string;
  target_repo_id: string;
  evidence_run_ids: string[];
  affected_case_ids: string[];
  source: string;
  status: "suggested";
};

/** One thing worth doing, with the way into it attached. */
export type AttentionItem = {
  kind: string;
  severity: "critical" | "warning" | "info";
  count: number;
  headline: string;
  detail: string;
  action: string;
  route: string;
};

export type AttentionFeed = {
  items: AttentionItem[];
  clear: boolean;
};

/** The five or six numbers the home page is allowed to show. */
export type EvidenceSummary = {
  experiments_active: number;
  repositories: number;
  cases: number;
  success: { rate: number | null; n: number; note: string };
  // Field names mirror `overview_service` exactly. They carry their unit in the
  // name, and a type that renamed them would compile happily while reading
  // `undefined` off every payload.
  cost: {
    median_usd: number | null;
    total_usd: number | null;
    cost_per_success: number | null;
    successes: number;
    n: number;
  };
  findings_open: number;
  harness_version: string | null;
  window_days: number;
};

/** One readiness line. `unknown` means "we could not look", never "absent". */
export type ReadinessCheck = {
  label: string;
  status: "ok" | "missing" | "unknown";
  detail: string;
  items: string[];
};

export type ReadinessReport = {
  target_id: string;
  name: string;
  repository_url: string;
  ref: string;
  commit: string | null;
  detected: {
    rules_files: string[];
    skills: string[];
    agents: string[];
    commands: string[];
    governance_docs: string[];
    memory: string[];
    adrs: string[];
    ci_workflows: string[];
    test_markers: string[];
  } | null;
  read_error: string;
  /**
   * Inferred from the repository's own files (Makefile test target,
   * package.json scripts, pytest config, go.mod, Cargo.toml). Suggestions
   * only — a person adopts them into the target, and can edit them there.
   */
  suggested_validation_commands?: string[];
  suggested_setup_commands?: string[];
  checks: ReadinessCheck[];
  /** Narrowly: a run would execute and be scored. Not a governance verdict. */
  ready: boolean;
  blocking: string[];
};

/** One recorded metric. `provenance` is the field that must reach the eye. */
export type ScoreRecord = {
  run_id: string;
  name: string;
  version: string;
  category: string;
  provenance: "fact" | "observation" | "judgement" | string;
  source: string;
  value_num: number | null;
  value_bool: boolean | null;
  value_text: string | null;
  reason: string;
};

export type TargetRecord = {
  target_id: string;
  name: string;
  description: string;
  repository_url: string;
  default_ref: string;
  status: "active" | "archived";
  setup_commands: string[];
  validation_commands: string[];
  language: string;
  team: string;
  tags: string[];
  /** The operator's own clone, once they have made and pinned one. */
  checkout_path?: string;
  pinned_commit?: string;
  pinned_at?: string | null;
};

/**
 * What the lab can see of the engineer's own clone of a target repository.
 *
 * `exists: false` is an ordinary state, not an error: before they have run the
 * clone command it is exactly what the page should say.
 */
export type CheckoutState = {
  target_id: string;
  /** The command to paste into their own terminal, with their credentials. */
  command: string;
  /** Where the lab reads it (its own view of the path). */
  path: string;
  /** The same location as their shell sees it. */
  host_path: string;
  exists: boolean;
  is_repo: boolean;
  head: string;
  branch: string;
  remote_url: string;
  dirty: boolean;
  dirty_files: number;
  error: string;
  /**
   * The clone lives in a directory not named after this target — somebody
   * attached one that already existed. Say so, or the path reads as a typo.
   */
  attached: boolean;
  /** Whether the operator mounted a directory of their own repositories. */
  local_sources_enabled?: boolean;
  pinned_commit: string;
  pinned_at: string;
  /** HEAD has moved since the pin — offer a re-pin, do not move it silently. */
  pin_is_stale: boolean;
};

/** One clone sitting under data/checkouts, whoever put it there. */
export type CheckoutSummary = {
  name: string;
  path: string;
  head: string;
  branch: string;
  remote_url: string;
  /** The targets already pointing at it, if any. Sharing one clone is fine. */
  claimed_by: string[];
};

/** One level of the operator's own repositories, when they mounted them. */
export type LocalSourceListing = {
  enabled: boolean;
  /** Why the picker is unavailable, when it is. */
  reason?: string;
  /** The mounted root, as their own shell sees it. */
  root?: string;
  /** Where we are, relative to the root. Empty is the root itself. */
  path?: string;
  host_path?: string;
  /** Null at the root; "" is the root, so it cannot be conflated with it. */
  parent?: string | null;
  is_repo?: boolean;
  /** `origin` of the folder we are in, when it is a repository. */
  remote_url?: string;
  /** Its current branch — the ref a target registered from here defaults to. */
  branch?: string;
  entries: { name: string; path: string; is_repo: boolean }[];
};

/** One command a gate check ran, and how it ended. */
export type GateCheckStep = {
  phase: "provision" | "setup" | "validate" | string;
  command: string;
  exit_code: number | null;
  passed: boolean;
  detail: string;
};

/**
 * The repository measured alone: its own gate, at the pin, with no agent.
 *
 * What separates "the agent failed" from "this repository does not build here".
 */
export type GateCheckRecord = {
  gate_check_id: string;
  target_id: string;
  commit: string;
  status: "running" | "done" | "failed";
  passed: boolean | null;
  failed_phase: string;
  checks_json: GateCheckStep[];
  error: string | null;
  created_at: string;
  updated_at: string;
};

/** A case mined from a target's history — a proposal, nothing persisted. */
export type CaseCandidate = {
  commit: string;
  subject: string;
  authored_at: string;
  source_files: string[];
  test_files: string[];
  suggested_case_id: string;
  suggested_prompt: string;
};

export type CaseCandidates = {
  target_id: string;
  ref: string;
  scanned: number;
  candidates: CaseCandidate[];
};

export type TargetCreatePayload = {
  target_id: string;
  name: string;
  repository_url: string;
  description?: string;
  default_ref?: string;
  setup_commands?: string[];
  validation_commands?: string[];
  language?: string;
  team?: string;
  tags?: string[];
};

/** What `main` resolves to right now — shown before a run is created. */
export type TargetResolveResult = {
  target_id: string;
  ref: string;
  commit: string;
};

export type RunConfigPayload = {
  case_id: string;
  prompt_variant: string;
  harness: string;
  planner_model: string | null;
  executor_model: string | null;
  score_mode: string;
  max_steps: number;
  resume: boolean;
  dry_run: boolean;
  skills_disabled?: string[];
  skills_extra?: string[];
  /** Skills the agent is told to invoke at the start of every step; each must also be available. */
  skills_invoke?: string[];
  target_repo_id?: string;
  target_ref?: string;
  harness_source?: HarnessSource;
};

export type RunCreatePayload = {
  run_id?: string;
  label?: string;
  config: RunConfigPayload;
};

/**
 * One request that expands into the cross-product of its axes.
 *
 * Every array is a dimension; the scalars are shared by every generated run.
 * There is no `resume` and no `run_id`/`label`: resuming targets one specific
 * workspace, and N generated runs each take the next auto-assigned id.
 */
export type RunBatchPayload = {
  case_ids: string[];
  prompt_variants: string[];
  harnesses: string[];
  executor_models: (string | null)[];
  planner_models: (string | null)[];
  /** The Mode B axis: one case family, N repositories. `[""]` = no target. */
  target_repo_ids?: string[];
  target_ref?: string;
  harness_sources?: HarnessSource[];
  repetitions: number;
  score_mode: string;
  max_steps: number;
  dry_run: boolean;
  skills_disabled?: string[];
  skills_extra?: string[];
  skills_invoke?: string[];
};

export type RunBatchResult = {
  batch_id: string;
  count: number;
  run_ids: string[];
};

/**
 * Verdict of the pre-creation check on a run configuration.
 *
 * These are the same checks `POST /api/runs` applies, so `ok: true` means the
 * form will not be rejected for configuration reasons; it is not a promise that
 * the run itself will succeed.
 */
export type RunValidationResult = {
  ok: boolean;
  detail: string;
};

export type ModelOption = {
  id: string;
  label: string;
  // False when neither opencode.json nor the enabled web catalog can route the
  // id: the gateway serves it, but a run using it would fail at launch.
  routable?: boolean;
  // False when the machine behind a gateway card is provably not serving it
  // right now — the LAN host is down, or the alias names a model it does not
  // have. Null when undecidable (a cloud card, a host the lab does not probe),
  // which is why this is not just `routable` with a wider meaning: one asks
  // whether the id can be addressed, the other whether anything is listening.
  reachable?: boolean | null;
  unreachable_reason?: string | null;
  // Other gateway ids that the router resolves to this same backend model.
  // They are hidden from the picker so one model appears once.
  aliases?: string[];
  // Serving backend ("lmstudio" | "cloud" | "ollama") and its display heading.
  // The API returns models pre-sorted by group_rank, so rendering in order
  // reproduces the intended hierarchy: LM Studio → Cloud → Ollama.
  backend?: string;
  group?: string;
  group_rank?: number;
  // The picker's two upper tiers. `source` is how the model is *reached* —
  // through the LiteLLM gateway, or straight at a local host — which is a
  // different axis from ModelCatalog.source (where the *list* came from).
  // `provider` is the vendor: lmstudio, ollama, openai, anthropic, google,
  // nvidia, other.
  source?: "gateway" | "direct";
  provider?: string;
  provider_label?: string;
  // Direct models only: what the host reports about the model right now.
  loaded?: boolean;
  loaded_context?: number | null;
  max_context?: number | null;
};

/**
 * Outcome of measuring the context window a model is *actually* serving.
 *
 * `status` is "ok" when the model accepted a prompt of `required_tokens`,
 * "insufficient" when it refused one and named a smaller window, and "unknown"
 * when the probe could not reach a verdict — the run gate only blocks on
 * "insufficient", so an unreachable backend never stops a run.
 */
export type ContextProbeResult = {
  model: string;
  status: "ok" | "insufficient" | "unknown";
  ok: boolean;
  required_tokens: number;
  context_tokens: number | null;
  prompt_tokens: number | null;
  detail: string;
  /** Non-blocking: passes, but tight for agentic coding (below 64k). */
  advisory?: string;
};

export type ModelCatalog = {
  // "gateway" when the list came live from LiteLLM; "opencode.json" when the
  // gateway was unreachable and the routing map was used as a fallback.
  source: string;
  models: ModelOption[];
  default_executor: string;
  // Why a direct provider has no models (server off, URL unset), keyed by
  // provider id. Per provider because one host can be up while the other is
  // down — an empty group with no reason is unactionable.
  direct_errors?: Record<string, string>;
};

/** A host CLI's native model with gateway coverage (ADR-0069). */
export type NativeModelRecord = {
  id: string;
  label: string;
  generation: "current" | "previous";
  size_rank: number;
  /** null when no gateway is configured to ask. */
  on_gateway: boolean | null;
};

/** One model as the LM Studio host reports it, with no gateway in between. */
export type LMStudioModel = {
  // Prefixed id the run form uses ("lmstudio/prism-ml/bonsai-27b").
  id: string;
  // The id LM Studio itself registers ("prism-ml/bonsai-27b").
  bare_id: string;
  type: string;
  state: string;
  loaded: boolean;
  // The window it is serving right now; null unless it is loaded, because an
  // unloaded model gets whatever default a JIT auto-load picks.
  loaded_context: number | null;
  max_context: number | null;
  arch: string;
  quantization: string;
};

export type LMStudioCatalog = {
  base_url: string;
  reachable: boolean;
  error: string | null;
  models: LMStudioModel[];
};

/** One model as the Ollama host reports it, with no gateway in between. */
export type OllamaModel = {
  // Prefixed id the run form uses ("ollama/qwen3:32b").
  id: string;
  // The id Ollama itself registers ("qwen3:32b").
  bare_id: string;
  state: string;
  running: boolean;
  // The window the server serves for this model; only knowable while it runs.
  // Anything else gets the host's OLLAMA_CONTEXT_LENGTH, which defaults to
  // 4096 — far below what a run needs, and not readable from here.
  context_length: number | null;
  parameter_size: string;
  quantization: string;
  family: string;
};

export type OllamaCatalog = {
  base_url: string;
  reachable: boolean;
  error: string | null;
  models: OllamaModel[];
};

export type EnvSettingItem = {
  key: string;
  label: string;
  group: string;
  is_secret: boolean;
  help_text: string;
  is_set: boolean;
  // Secrets come back masked; writing the mask back means "leave unchanged".
  value: string;
};

/**
 * `auth_enabled` false means the lab runs in open mode — no password has been
 * set, so every request is allowed and the UI shows a warning banner.
 */
export type SessionState = {
  authenticated: boolean;
  auth_enabled: boolean;
};

export type EnvSettings = {
  items: EnvSettingItem[];
  warnings: string[];
  env_path: string;
};

export type EnvUpdateResult = EnvSettings & {
  written: string[];
  backup: string | null;
};

export type RuntimeFacts = {
  repo_root: string;
  database_url: string;
  execution_backend: string;
  runner_image: string;
  sandbox_image: string;
  docker_host_alias: string;
  redis_url: string;
  run_timeout_seconds: number;
  // Seconds of silence, with no model request in flight, after which a run is
  // killed as stuck; 0 = disabled.
  run_stall_seconds: number;
  // The same silence while the model owes the run a first token. Much larger:
  // a local model says nothing while it processes the prompt.
  run_model_wait_seconds: number;
  // Consecutive failed provider requests tolerated before giving up.
  run_max_stream_errors: number;
  // Ceiling on the cross-product of one POST /api/runs/batch. Read rather
  // than mirrored in the client, so the two can never disagree.
  max_batch_runs: number;
};

export type GatewayModelRecord = {
  id: number;
  model_id: string;
  label: string;
  enabled: boolean;
  is_default: boolean;
  live: boolean;
  options_json: Record<string, unknown>;
  first_seen_at: string;
  last_seen_at: string;
  // Serving backend and its display heading. Rows arrive pre-sorted by
  // group_rank so the table sections match the run picker's ordering.
  backend?: string;
  group?: string;
  group_rank?: number;
};

export type ModelRefreshResult = {
  added: string[];
  stale: string[];
  total: number;
  gateway_reachable: boolean;
};

export type GatewayTestResult = {
  ok: boolean;
  detail?: string;
  model_count?: number;
  sample?: string[];
};

export type DocEntry = {
  slug: string;
  title: string;
  description: string;
  group: string;
  /** The file under docs/, so a link between two catalogued docs opens in place. */
  file: string;
};

export type DocContent = {
  slug: string;
  title: string;
  content: string;
};

// One flat leaderboard row per terminal run (GET /api/leaderboard); the
// Dashboard groups by condition (harness · model · prompt · version) client-side.
export type LeaderboardRunRow = {
  run_id: string;
  case_id: string;
  case_type: string;
  harness: string;
  prompt_variant: string;
  executor_model: string | null;
  planner_model: string | null;
  harness_version: string;
  // true/false = objective verdict; null = manual-score case (no % resolved).
  resolved: boolean | null;
  score: number | null;
  composite: number | null;
  cost_usd: number | null;
  total_tokens: number | null;
  steps: number | null;
  // The run this one continues from, and how many continuations led to it
  // (ADR-0026). chain_depth 0 = a one-shot attempt.
  parent_run_id: string | null;
  chain_depth: number;
  state: string;
  created_at: string;
};

// Curated official swebench.com rows (data/reference/swebench_official.json).
export type OfficialReferenceRow = {
  dataset: string;
  agent: string;
  model: string;
  pct_resolved: number;
  date?: string;
  source_url?: string;
};

export type SwebenchProInstance = {
  instance_id: string;
  repo: string;
  repo_language: string;
  title: string;
  summary: string;
  issue_categories: string[];
  f2p_count: number;
  p2p_count: number;
  imported: boolean;
};

export type ImprovementProposal = {
  title: string;
  body: string;
  kind?: string;
  target?: string;
  repo: string;
  created_issue_url: string | null;
};

export type ImprovementsDoc = {
  run_id: string;
  context: { case: string; harness: string; executor_model: string };
  generator_model: string;
  strictness?: string;
  gate?: { skipped: boolean; reason: string | null; min_score: number };
  proposals: ImprovementProposal[];
};

export type ProposalIssueField = {
  id: string;
  label: string;
  value: string;
  kind: "input" | "textarea" | "dropdown";
  recommended: boolean;
  reason: string;
};

export type ProposalIssueForm = {
  repository: string;
  template: string;
  title: string;
  url: string;
  fields: ProposalIssueField[];
};

export type ProposalIssue = {
  run_id: string;
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
};

export type SavedProposalRecord = {
  id: number;
  run_id: string;
  title: string;
  body: string;
  kind: string;
  target: string;
  repo_hint: string;
  repo: string;
  created_issue_url: string | null;
  case: string;
  harness: string;
  executor_model: string;
  generator_model: string;
  run_created_at: string | null;
  template_version: string;
  template_fingerprint: string;
  status: "saved" | "issued";
  group_issue_id: number | null;
  created_at: string;
  updated_at: string;
};

export type SaveImprovementsResult = {
  run_id: string;
  saved: number;
  updated: number;
  total: number;
};

export type GroupIssueResult = {
  group_issue_id: number;
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
  proposal_ids: number[];
  source_run_ids: string[];
};

/** One row of the health panel (ADR-0018). */
export type HealthCheck = {
  id: string;
  label: string;
  status: "ok" | "warn" | "error" | "unknown";
  detail: string;
  latency_ms: number | null;
  /** The allowlisted repair for this row, or null when nothing to fix. */
  heal: string | null;
  /** Offending paths / run ids, when the check can name them. */
  items: string[];
};

export type HealthReport = {
  status: "ok" | "warn" | "error" | "unknown";
  checks: HealthCheck[];
};

export type HealResult = {
  action: string;
  ok: boolean;
  detail: string;
};

/**
 * "Tune your local model" (`/local/tune`): mirrors
 * `src/harness_lab_app/local_tuning/contracts.py` field for field. Prefixed
 * `Local…` only where the bare name would collide with an existing type.
 */
export type LocalServerKind = "lmstudio" | "ollama";
export type LocalMemoryKind = "unified" | "discrete";

export type LocalVerdict = "ok" | "tight" | "fail" | "inconclusive" | "not_applicable";
export type LocalConfidence = "exact" | "good" | "rough";

export type RefMachine = {
  id: string;
  label: string;
  memory_kind: LocalMemoryKind;
  gpu_gb: number;
  ram_gb: number;
  reserve_gb: number;
  /** Memory bandwidth, GB/s; 0 = unknown. */
  bandwidth_gbs: number;
  /** Present when the preset's memory layout is not the Mac default (0.75) — e.g. a Jetson's unified 1.0. */
  gpu_wired_fraction?: number;
};

export type LocalReference = {
  machines: RefMachine[];
  context_options: number[];
  eval_batch_options: number[];
  kv_cache_types: Record<string, number>;
  default_load: LocalLoadConfig;
  defaults: { lmstudio_url: string; ollama_url: string };
};

export type LocalMachine = {
  id: string;
  name: string;
  memory_kind: LocalMemoryKind;
  gpu_gb: number;
  ram_gb: number;
  reserve_gb: number;
  gpu_wired_fraction: number;
  bandwidth_gbs: number | null;
  notes: string;
  created_at: string;
};

export type MachineSpec = {
  memory_kind: LocalMemoryKind;
  gpu_gb: number;
  ram_gb: number;
  reserve_gb: number;
  gpu_wired_fraction: number;
  bandwidth_gbs?: number | null;
};

export type LocalArchShape = {
  layers: number;
  kv_layers: number;
  kv_heads: number;
  head_dim: number;
  sliding_window: number | null;
  swa_layers: number;
};

export type LocalServedConfig = {
  context_length: number | null;
  gpu_offload: number | null;
  flash_attention: boolean | null;
  offload_kv_cache_to_gpu: boolean | null;
  eval_batch_size: number | null;
  num_experts: number | null;
  parallel: number | null;
  vram_bytes: number | null;
  /** The instance the lab loaded, when the backend can tell them apart. */
  instance_id: string | null;
};

export type LocalModelInfo = {
  id: string;
  display_name: string;
  arch: string;
  params: string;
  quantization: string;
  bits_per_weight: number | null;
  size_bytes: number | null;
  max_context: number | null;
  is_moe: boolean;
  supports_tools: boolean | null;
  loaded: boolean;
  instance_ids: string[];
  served: LocalServedConfig | null;
  shape: LocalArchShape | null;
};

export type LocalLoadConfig = {
  context_length: number;
  gpu_offload: number;
  flash_attention: boolean | null;
  offload_kv_cache_to_gpu: boolean | null;
  eval_batch_size: number | null;
  num_experts: number | null;
  parallel: number;
  kv_cache_bytes: number;
};

export type LocalConfigDiff = {
  field: string;
  requested: unknown;
  served: unknown;
};

export type LocalMemoryBreakdown = {
  weights_gb: number;
  kv_cache_gb: number;
  overhead_gb: number;
  total_gb: number;
  gpu_gb: number;
  ram_gb: number;
  available_gb: number;
  low_gb: number | null;
  high_gb: number | null;
};

export type LocalMemoryEstimate = {
  breakdown: LocalMemoryBreakdown;
  verdict: LocalVerdict;
  confidence: LocalConfidence;
  lever: string;
  notes: string[];
};

/** How a measured write speed compares to the hardware's bandwidth ceiling. */
export type LocalHeadroomLevel =
  | "near_ceiling"
  | "some_headroom"
  | "far_below"
  | "compute_bound";

export type LocalHeadroom = {
  level: LocalHeadroomLevel;
  ceiling_tps: number;
  fraction: number;
  typical_tps: [number, number];
  bytes_per_token_gb: number;
  reading: string;
};

/** How a reference device holds its memory. */
export type LocalReferenceDeviceKind = "gpu" | "unified" | "cpu";

/** One piece of hardware the lab keeps published speeds for. */
export type LocalReferenceDevice = {
  id: string;
  label: string;
  kind: LocalReferenceDeviceKind;
  /** Memory bandwidth, GB/s; null = unknown (e.g. H100, where PCIe/SXM differ). */
  bandwidth_gbs: number | null;
  memory: string;
};

/** One model the lab keeps published speeds for. */
export type LocalReferenceModel = {
  id: string;
  label: string;
  params: string;
  quant: string;
  /** File size in GB, when a server or llama-bench reported one. */
  size_gb: number | null;
};

/** One published single-user speed for a model/device pair (`REFERENCE_DEVICES`). */
export type LocalReferenceRow = {
  model: string;
  device: string;
  tg: number;
  pp: number | null;
  runtime: string;
  note: string;
  source: string;
  /** Read from a summary table or a third-party aggregator, not a raw bench row. */
  secondary: boolean;
  /** The bandwidth ceiling for this model/device pair, when computable. */
  ceiling_tps: number | null;
  /** `tg / ceiling_tps`, when both are known. */
  fraction: number | null;
};

export type LocalReferenceDevices = {
  devices: LocalReferenceDevice[];
  models: LocalReferenceModel[];
  rows: LocalReferenceRow[];
};

export type LocalTestName = "fit" | "speed" | "window" | "agent";

export type LocalTestResult = {
  name: LocalTestName;
  verdict: LocalVerdict;
  reading: string;
  detail: Record<string, unknown>;
};

export type LocalTestSession = {
  id: string;
  server_url: string;
  model_id: string;
  status: "running" | "done" | "failed";
  current: string;
  results: LocalTestResult[];
  error: string;
  /** The history row this session was saved as (a machine was given), once it finished. */
  profile_id: string | null;
};

export type LocalActiveRunRef = {
  run_id: string;
  state: string;
  model: string;
  experiment_id: string | null;
};

export type LocalGuardReport = {
  host: string;
  running: LocalActiveRunRef[];
  queued: LocalActiveRunRef[];
  clear: boolean;
  /** True when gateway routing could not be fetched, so gateway runs on this host could not be checked. */
  routing_unknown: boolean;
};

export type LocalServerTestResult = {
  kind: LocalServerKind;
  url: string;
  reachable: boolean;
  models: LocalModelInfo[];
  error: string;
};

export type LocalLoadResult = {
  served: LocalServedConfig;
  diff: LocalConfigDiff[];
  unsupported: string[];
  /** True when the model was already loaded with this config and nothing was loaded. */
  reused: boolean;
};

/** The 409 body `load`/`unload`/`tests` answer with when the host guard fires. */
export type LocalGuardBlockedDetail = {
  guard: LocalGuardReport;
};

/** The 409 body `tests` answers with when a session is already running on the host. */
export type LocalTestsBusyDetail = {
  session: LocalTestSession;
};

/** The 409 body `tests` answers with in evaluate-only mode when the model is not loaded. */
export type LocalNotLoadedDetail = {
  not_loaded: string;
  message: string;
};

/**
 * The 409 body `unload` answers with when the model has instances the lab
 * did not load itself — unloading needs an explicit `force: true` to proceed.
 */
export type LocalUnloadForeignInstancesDetail = {
  instances: string[];
  message: string;
};

export type LocalTuningProfile = {
  id: string;
  machine_id: string;
  server_kind: LocalServerKind;
  server_url: string;
  model_id: string;
  label: string;
  requested_config: LocalLoadConfig;
  served_config: LocalServedConfig;
  estimate: LocalMemoryEstimate;
  results: LocalTestResult[];
  observed_memory_gb: number | null;
  recommended: boolean;
  created_at: string;
};

export type LocalProfileCreatePayload = {
  machine_id: string;
  server_kind: LocalServerKind;
  server_url: string;
  model_id: string;
  label: string;
  requested_config: LocalLoadConfig;
  served_config: LocalServedConfig;
  estimate: LocalMemoryEstimate;
  results: LocalTestResult[];
  observed_memory_gb?: number | null;
};

/** One of the handful of numbers a history row and a comparison need (`history.summarize`). */
export type LocalResultSummary = {
  verdicts: Partial<Record<LocalTestName, LocalVerdict>>;
  seconds_per_step: number | null;
  read_tokens_per_second: number | null;
  write_tokens_per_second: number | null;
  agent_passed: number | null;
  agent_total: number | null;
  context_length: number | null;
  estimated_gb: number | null;
};

/** One row of `GET /api/local/history`: a saved profile with its summary numbers. */
export type LocalHistoryRow = {
  profile: LocalTuningProfile;
  summary: LocalResultSummary;
};

/** Which side a metric favors, "tie" within noise, or "" when not measured on both. */
export type LocalComparisonWinner = "a" | "b" | "tie" | "";

export type LocalComparisonMetricRow = {
  key: string;
  label: string;
  better: "higher" | "lower";
  a: number | null;
  b: number | null;
  delta_pct: number | null;
  winner: LocalComparisonWinner;
};

export type LocalComparisonVerdictRow = {
  name: LocalTestName;
  a: LocalVerdict | null;
  b: LocalVerdict | null;
  winner: LocalComparisonWinner;
};

export type LocalComparison = {
  rows: LocalComparisonMetricRow[];
  verdicts: LocalComparisonVerdictRow[];
  overall: "a" | "b" | "tie";
  summary: string;
  noise_pct: number;
};

export type LocalProfileCompareResult = {
  a: LocalTuningProfile | null;
  b: LocalTuningProfile | null;
  comparable: boolean;
  reason: string;
  /** Present whenever `comparable` is true. */
  comparison?: LocalComparison;
};

/** `GET /api/local/results`: speed-test ranking and local-model case runs. */
export type LocalSpeedRow = LocalResultSummary & {
  profile_id: string;
  machine: string;
  machine_id: string;
  model_id: string;
  label: string;
  server_kind: LocalServerKind;
  recommended: boolean;
  created_at: string;
  /** Null when the speed test recorded none (no bandwidth/model size, or an older row). */
  headroom?: LocalHeadroom | null;
  /** The reference model id matching this row's model, or null if none matched. */
  reference_model?: string | null;
};

export type LocalCaseRow = {
  model: string;
  harness: string;
  case_id: string;
  /** The condition label the run answered for, or the prompt variant without one. */
  arm: string;
  runs: number;
  validated_runs: number;
  passed_runs: number;
  test_pass_rate: number | null;
  median_minutes: number | null;
};

export type LocalResults = {
  speed: LocalSpeedRow[];
  cases: LocalCaseRow[];
};

/** `GET /api/local/model-load-check`: whether the host already has the model
 * loaded, used by New evaluation's "Check configuration" guard. */
export type LocalModelLoadCheck = {
  model: string;
  host: string | null;
  server_kind: LocalServerKind | null;
  model_bare: string;
  loaded: boolean | null;
  size_gb: number | null;
  guard: LocalGuardReport | null;
};
