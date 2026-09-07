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
  // failed. null when they have not run or the case has no objective criterion.
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
  exit_code: number | null;
  detail: string;
};

export type ValidationRecord = {
  run_id: string;
  objective: {
    strategy?: string;
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
 */
export type HarnessSource = "lab_root" | "native" | "injected";

/** What the lab knows about itself. Every figure carries its denominator. */
export type OverviewRate = { rate: number | null; n: number; note: string };

/** One registry metric aggregated across the corpus, direction attached. */
export type ScoreAggregate = {
  name: string;
  version: string;
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
  | "subject_validation";
export type ConditionRole = "control" | "treatment";

export type ExperimentRecord = {
  experiment_id: string;
  name: string;
  question: string;
  mode: ExperimentMode;
  /** What is under test when it is not the harness. Empty under every other mode. */
  subject: string;
  status: "draft" | "running" | "complete" | "abandoned";
  case_ids: string[];
  prompt_variants: string[];
  repetitions: number;
  notes: string;
  condition_count?: number;
  run_count?: number;
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

export type ConditionSummary = {
  label: string;
  role: ConditionRole;
  runs: number;
  scored_runs: number;
  success_rate: number | null;
  warnings: string[];
  metrics: Record<string, AggregateRecord>;
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
  verdict?: VerdictBlock;
  conditions: ConditionSummary[];
  comparisons: {
    control: string;
    treatment: string;
    deltas: DeltaRecord[];
    success: { control: number | null; treatment: number | null; points: number | null };
    case_matrix: CaseMatrixRow[];
  }[];
  /**
   * Per-arm, per-case counts: {arm label: {case_id: {runs, scored_runs,
   * successes}}}. Raw grid with the repetitions kept — not a comparison:
   * `comparisons[].case_matrix` pairs a control against a treatment, which a
   * mode without a control never has.
   */
  case_outcomes?: Record<string, Record<string, CaseOutcomeCell>>;
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
